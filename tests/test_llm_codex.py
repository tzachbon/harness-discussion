"""CodexWriter against a fake app-server: the exact turn/start params, the JSON reply, cancel, failures, the warm
server with its spare thread, and the flags that leave out the user's hooks and MCP servers."""
import json
import os
import queue
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from harness_discussion import llm_codex, writer


class FakeServer:
    """Records calls. `script` is the list of events to emit after turn/start."""
    made = []

    def __init__(self, thread=None, script=(), models=()):
        self.thread, self.thread_id, self.calls, self.closed, self._threads = thread, "t1", [], False, 1
        self.events, self.alive, self._script, self._models = queue.Queue(), True, script, models
        FakeServer.made.append(self)

    def call(self, method, params, timeout=20):
        self.calls.append((method, params))
        if method == "turn/start":
            for event in self._script:
                self.events.put({**event, "params": {"threadId": params["threadId"], **event["params"]}})
            return {"turn": {"id": "turn9"}}
        if method == "thread/start":
            self._threads += 1
            return {"thread": {"id": f"t{self._threads}"}}
        if method == "model/list":
            return {"data": list(self._models)}
        return {}

    def close(self):
        self.closed = True


def _reply(text, status="completed"):
    return [{"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": json.dumps({"text": text})}}},
            {"method": "turn/completed", "params": {"turn": {"status": status}}}]


def _writer(**kw):
    FakeServer.made = []
    return llm_codex.CodexWriter(lambda thread=None: FakeServer(thread, **kw))


class CodexWriterTest(unittest.TestCase):
    def setUp(self):
        self.spawned = mock.Mock()  # the prewarm after a rewrite runs only when a test calls it
        fake = types.SimpleNamespace(Thread=self.spawned, Lock=threading.Lock, RLock=threading.RLock,
                                     Timer=threading.Timer)
        patch = mock.patch.object(llm_codex, "threading", fake)
        patch.start()
        self.addCleanup(patch.stop)

    def turns(self, srv):
        return [p for m, p in srv.calls if m == "turn/start"]

    def test_turn_params_include_model_effort_and_schema(self):
        source = _writer(script=_reply("Fixed."))
        self.addCleanup(source.close)
        self.assertEqual(source.rewrite("SYSTEM", "fixd", "gpt-5.5", "low", lambda: False), "Fixed.")
        srv = FakeServer.made[0]
        self.assertEqual(srv.thread, {"ephemeral": True, "baseInstructions": "SYSTEM", "cwd": source._cwd,
                                      "config": llm_codex.lean_config()})
        self.assertEqual(self.turns(srv), [{"threadId": "t1", "input": [{"type": "text", "text": "fixd"}],
                                            "model": "gpt-5.5", "effort": "low", "approvalPolicy": "never",
                                            "sandboxPolicy": {"type": "readOnly"}, "outputSchema": llm_codex.SCHEMA}])
        self.assertFalse(srv.closed)

    def test_no_model_or_effort_uses_the_codex_defaults(self):
        source = _writer(script=_reply("Ok."))
        self.addCleanup(source.close)
        source.rewrite("S", "ok", None, None, lambda: False)
        params = self.turns(FakeServer.made[0])[0]
        self.assertNotIn("model", params)
        self.assertNotIn("effort", params)

    def test_a_failed_turn_raises(self):
        source = _writer(script=_reply("x", status="failed"))
        self.addCleanup(source.close)
        with self.assertRaises(RuntimeError):
            source.rewrite("S", "t", None, None, lambda: False)

    def test_cancel_interrupts_the_turn(self):
        source = _writer()
        self.addCleanup(source.close)
        with self.assertRaises(writer.Cancelled):
            source.rewrite("S", "t", None, None, lambda: True)
        self.assertIn(("turn/interrupt", {"threadId": "t1", "turnId": "turn9"}), FakeServer.made[0].calls)

    def test_timeout_interrupts_the_turn(self):
        source = _writer()
        self.addCleanup(source.close)
        with mock.patch.object(llm_codex, "TIMEOUT_S", 0), self.assertRaises(TimeoutError):
            source.rewrite("S", "t", None, None, lambda: False)
        self.assertIn(("turn/interrupt", {"threadId": "t1", "turnId": "turn9"}), FakeServer.made[0].calls)

    def test_events_from_other_threads_are_ignored(self):
        source = _writer(script=_reply("Mine."))
        self.addCleanup(source.close)
        source._take("S")
        srv = FakeServer.made[0]
        srv.events.put({"method": "turn/completed", "params": {"threadId": "t2", "turn": {"status": "failed"}}})
        srv._script = [{"method": "turn/completed", "params": {"threadId": "old", "turn": {"status": "failed"}}},
                       *_reply("Mine.")]
        self.assertEqual(source.rewrite("S", "t", None, None, lambda: False), "Mine.")

    def test_the_server_stays_warm_and_each_rewrite_gets_a_fresh_thread(self):
        source = _writer(script=_reply("Ok."))
        self.addCleanup(source.close)
        source.rewrite("S", "a", None, None, lambda: False)
        source.prewarm()
        source.rewrite("S", "b", None, None, lambda: False)
        srv = FakeServer.made[0]
        self.assertEqual(len(FakeServer.made), 1)
        self.assertEqual([p["threadId"] for p in self.turns(srv)], ["t1", "t2"])
        self.assertEqual([m for m, _ in srv.calls].count("thread/start"), 1)  # the spare, made before the rewrite

    def test_a_spare_for_other_instructions_is_not_used(self):
        source = _writer(script=_reply("Ok."))
        self.addCleanup(source.close)
        source.rewrite("S", "a", None, None, lambda: False)
        source.prewarm()
        source.rewrite("OTHER", "b", None, None, lambda: False)
        srv = FakeServer.made[0]
        starts = [p for m, p in srv.calls if m == "thread/start"]
        self.assertEqual([p["baseInstructions"] for p in starts], ["S", "OTHER"])
        self.assertEqual(self.turns(srv)[-1]["threadId"], "t3")

    def test_a_rewrite_queues_a_prewarm(self):
        source = _writer(script=_reply("Ok."))
        self.addCleanup(source.close)
        source.rewrite("S", "a", None, None, lambda: False)
        self.spawned.assert_called_with(target=source.prewarm, daemon=True)

    def test_a_dead_server_is_replaced(self):
        source = _writer(script=_reply("Ok."))
        self.addCleanup(source.close)
        source.rewrite("S", "a", None, None, lambda: False)
        FakeServer.made[0].alive = False
        source.rewrite("S", "b", None, None, lambda: False)
        self.assertEqual(len(FakeServer.made), 2)

    def test_prewarm_before_any_rewrite_only_starts_the_server_and_never_raises(self):
        source = _writer()
        self.addCleanup(source.close)
        source.prewarm("m")
        self.assertEqual(len(FakeServer.made), 1)
        self.assertIsNone(source._spare)
        broken = llm_codex.CodexWriter(mock.Mock(side_effect=OSError))
        self.addCleanup(broken.close)
        broken.prewarm("m")

    def test_a_new_rewrite_waits_until_a_cancelled_one_stops_reading_events(self):
        source = _writer()
        self.addCleanup(source.close)
        cancel, results = threading.Event(), {}

        def first():
            try:
                source.rewrite("S", "a", None, None, cancel.is_set)
            except writer.Cancelled:
                results["first"] = "cancelled"
        a = threading.Thread(target=first)
        a.start()
        while not FakeServer.made or not self.turns(FakeServer.made[0]):
            time.sleep(0.01)
        srv = FakeServer.made[0]
        srv._script = _reply("Second.")
        b = threading.Thread(target=lambda: results.update(second=source.rewrite("S", "b", None, None, lambda: False)))
        b.start()
        time.sleep(0.3)
        self.assertEqual(len(self.turns(srv)), 1)  # the second rewrite has not started its turn
        cancel.set()
        a.join(5)
        b.join(5)
        self.assertEqual(results, {"first": "cancelled", "second": "Second."})

    def test_a_prewarm_queued_before_close_starts_nothing(self):
        source = _writer(script=_reply("Ok."))
        source.rewrite("S", "a", None, None, lambda: False)
        source.close()
        source.prewarm()
        self.assertEqual(len(FakeServer.made), 1)
        self.assertIsNone(source._srv)
        self.assertIsNone(source._cwd)

    def test_close_stops_the_server_and_removes_the_folder(self):
        source = _writer(script=_reply("Ok."))
        source.rewrite("S", "a", None, None, lambda: False)
        cwd = source._cwd
        source.close()
        self.assertTrue(FakeServer.made[0].closed)
        self.assertFalse(os.path.exists(cwd))

    def test_models_are_visible_only_and_cached(self):
        models = [{"id": "a", "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]},
                  {"id": "b", "hidden": True}]
        source = _writer(models=models)
        self.assertEqual(source.models(), [{"id": "a", "efforts": ["low", "high"]}])
        source.models()
        self.assertEqual(len(FakeServer.made), 1)
        self.assertTrue(FakeServer.made[0].closed)


class LeanConfigTest(unittest.TestCase):
    def config(self, toml):
        with tempfile.TemporaryDirectory() as home:
            if toml is not None:
                with open(os.path.join(home, "config.toml"), "w") as f:
                    f.write(toml)
            with mock.patch.dict(os.environ, {"CODEX_HOME": home}):
                return llm_codex.lean_config()

    def test_turns_off_hooks_plugins_web_search_and_each_mcp_server(self):
        config = self.config('[mcp_servers.notion]\nurl = "x"\n[mcp_servers."a.b"]\ncommand = "y"\n')
        self.assertFalse(config["features"]["hooks"])
        self.assertFalse(config["features"]["plugins"])
        self.assertEqual(config["web_search"], "disabled")
        self.assertEqual(config["mcp_servers"], {"notion": {"enabled": False}, "a.b": {"enabled": False}})

    def test_a_missing_or_broken_config_still_turns_off_the_features(self):
        for toml in (None, "not = [toml"):
            config = self.config(toml)
            self.assertFalse(config["features"]["hooks"])
            self.assertEqual(config["mcp_servers"], {})


if __name__ == "__main__":
    unittest.main()
