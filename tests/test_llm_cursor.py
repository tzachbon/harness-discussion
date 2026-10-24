"""Cursor writer and transcript: the ACP call sequence with a fake agent, and the TranscribeAudio bytes. No network."""
import io
import queue
import threading
import unittest
import urllib.error
from unittest import mock

from harness_discussion import llm_cursor, writer


class FakeAcp:
    cwd, alive = "/tmp/empty", True

    def __init__(self, chunks=("Fixed", " text."), stop="end_turn"):
        self.calls, self.notes, self.updates, self._chunks, self.closed = [], [], queue.Queue(), chunks, False
        self.stop, self.sessions, self.fail_new, self.gate = stop, 0, 0, None

    def call(self, method, params, timeout=20):
        self.calls.append((method, params))
        if method == "session/new":
            gate, self.gate = self.gate, None
            if gate:
                gate.wait(2)
            if self.fail_new:
                self.fail_new -= 1
                raise RuntimeError("session/new failed")
            self.sessions += 1
            return {"sessionId": f"s{self.sessions}", "models": {"availableModels": [{"modelId": "gpt[effort=low]", "name": "GPT"}]},
                    "modes": {"availableModes": [{"id": "agent"}, {"id": "ask"}]}}
        if method == "session/prompt":
            for chunk in self._chunks:
                self.updates.put({"method": "session/update", "params": {"sessionId": params["sessionId"], "update": {
                    "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": chunk}}}})
            return {"stopReason": self.stop}
        return {}

    def notify(self, method, params):
        self.notes.append((method, params))

    def close(self):
        self.closed = True


class CursorWriterTest(unittest.TestCase):
    def test_status_decodes_cli_output_as_utf8_on_windows(self):
        with mock.patch.object(llm_cursor, "agent_exe", return_value="cursor-agent"), mock.patch.object(
                llm_cursor.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="✓ Logged in")) as run:
            self.assertEqual(llm_cursor.login_status(), "ready")
        self.assertEqual(run.call_args.kwargs.get("encoding"), "utf-8")
        self.assertEqual(run.call_args.kwargs.get("errors"), "replace")

    def setUp(self):
        self.acp = FakeAcp()
        self.source = llm_cursor.CursorWriter(lambda: self.acp)
        self.addCleanup(self.source.close)
        llm_cursor._ready_at = float("-inf")
        self.addCleanup(setattr, llm_cursor, "_ready_at", float("-inf"))

    def test_a_rewrite_uses_ask_mode_the_saved_model_and_joins_the_chunks(self):
        self.assertEqual(self.source.rewrite("SYSTEM", "fixd txt", "gpt[effort=low]", None, lambda: False), "Fixed text.")
        methods = [m for m, _ in self.acp.calls]
        self.assertEqual(methods, ["session/new", "session/set_mode", "session/set_model", "session/prompt"])
        self.assertEqual(self.acp.calls[1][1], {"sessionId": "s1", "modeId": "ask"})
        prompt = self.acp.calls[3][1]["prompt"][0]["text"]
        self.assertEqual(prompt, "SYSTEM\n\nfixd txt")  # writer.rewrite has already wrapped the text

    def test_a_turn_that_ran_out_of_tokens_is_refused(self):
        self.acp.stop = "max_tokens"
        with self.assertRaisesRegex(RuntimeError, "max_tokens"):
            self.source.rewrite("SYSTEM", "long text", None, None, lambda: False)

    def test_the_process_stays_warm_between_rewrites(self):
        starts = []
        source = llm_cursor.CursorWriter(lambda: starts.append(1) or self.acp)
        self.addCleanup(source.close)
        source.rewrite("S", "a", None, None, lambda: False)
        source.rewrite("S", "b", None, None, lambda: False)
        self.assertEqual(len(starts), 1)

    def test_cancel_sends_session_cancel(self):
        with self.assertRaises(writer.Cancelled):
            self.source.rewrite("S", "t", None, None, lambda: True)
        self.assertEqual(self.acp.notes, [("session/cancel", {"sessionId": "s1"})])

    def test_a_rewrite_waits_until_the_previous_one_has_left_the_update_queue(self):
        out = []
        with self.source._one:
            worker = threading.Thread(target=lambda: out.append(self.source.rewrite("S", "t", None, None, lambda: False)))
            worker.start()
            worker.join(0.2)
            self.assertEqual(self.acp.calls, [])
        worker.join(2)
        self.assertEqual(out, ["Fixed text."])

    def test_a_confirmed_sign_in_is_cached_for_a_minute_and_nothing_else_is(self):
        done = mock.Mock(returncode=0, stdout="✓ Logged in as someone\n")
        now = [1000.0]
        with mock.patch.object(llm_cursor, "agent_exe", return_value="/x/cursor-agent"), \
                mock.patch.object(llm_cursor.subprocess, "run", return_value=done) as run, \
                mock.patch.object(llm_cursor.time, "monotonic", lambda: now[0]):
            self.assertEqual([llm_cursor.login_status(), llm_cursor.login_status()], ["ready", "ready"])
            self.assertEqual(run.call_count, 1)
            now[0] += 61
            done.stdout = "Not logged in\n"
            self.assertEqual([llm_cursor.login_status(), llm_cursor.login_status()], ["signed_out", "signed_out"])
            self.assertEqual(run.call_count, 3)
            run.side_effect = OSError
            self.assertEqual([llm_cursor.login_status(), llm_cursor.login_status()], ["ready", "ready"])
            self.assertEqual(run.call_count, 5)  # an unknown answer is tried, never cached

    def prewarmed(self, model="m"):
        with mock.patch.object(llm_cursor, "login_status", return_value="ready"):
            self.source.prewarm(model)

    def counts(self):
        methods = [m for m, _ in self.acp.calls]
        return methods.count("session/new"), methods.count("session/set_model")

    def test_a_prewarmed_session_skips_setup_and_serves_one_rewrite(self):
        self.prewarmed()
        self.assertEqual(self.counts(), (1, 1))
        self.assertEqual(self.source.rewrite("S", "a", "m", None, lambda: False), "Fixed text.")
        self.assertEqual(self.counts(), (1, 1))
        self.assertEqual(self.acp.calls[-1][1]["sessionId"], "s1")
        self.source.rewrite("S", "b", "m", None, lambda: False)
        self.assertEqual(self.counts(), (2, 2))
        self.assertEqual(self.acp.calls[-1][1]["sessionId"], "s2")

    def test_a_spare_for_another_model_or_too_old_is_not_used(self):
        self.prewarmed("m")
        self.source.rewrite("S", "a", "other", None, lambda: False)
        self.assertEqual(self.acp.calls[-1][1]["sessionId"], "s2")
        now = [1000.0]
        with mock.patch.object(llm_cursor.time, "monotonic", lambda: now[0]):
            self.prewarmed("m")
            now[0] += llm_cursor.SPARE_TTL_S + 1
            self.source.rewrite("S", "b", "m", None, lambda: False)
        self.assertEqual(self.acp.calls[-1][1]["sessionId"], "s4")

    def test_a_failed_prewarm_never_raises_and_the_rewrite_still_works(self):
        self.acp.fail_new = 1
        self.prewarmed()
        self.assertEqual(self.source.rewrite("S", "a", "m", None, lambda: False), "Fixed text.")

    def test_a_hung_prewarm_holds_up_the_rewrite_only_briefly(self):
        self.acp.gate = gate = threading.Event()
        warming = threading.Thread(target=self.prewarmed)
        warming.start()
        with mock.patch.object(llm_cursor, "PREWARM_WAIT_S", 0.1):
            self.assertEqual(self.source.rewrite("S", "a", "m", None, lambda: False), "Fixed text.")
        self.assertFalse(gate.is_set())  # the rewrite finished while the prewarm was still stuck
        gate.set()
        warming.join(2)

    def test_a_signed_out_prewarm_does_nothing(self):
        with mock.patch.object(llm_cursor, "login_status", return_value="signed_out"):
            self.source.prewarm("m")
        self.assertEqual(self.acp.calls, [])

    def test_close_drops_the_spare(self):
        self.prewarmed()
        self.source.close()
        self.source.rewrite("S", "a", "m", None, lambda: False)
        self.assertEqual(self.acp.calls[-1][1]["sessionId"], "s2")

    def test_models_come_from_the_session(self):
        self.assertEqual(self.source.models(), [{"id": "gpt[effort=low]", "label": "GPT", "efforts": []}])

    def test_status_reads_cursor_agent_status(self):
        done = mock.Mock(returncode=0, stdout="✓ Logged in as someone\n")
        with mock.patch.object(llm_cursor, "agent_exe", return_value="/x/cursor-agent"), \
                mock.patch.object(llm_cursor.subprocess, "run", return_value=done):
            self.assertEqual(llm_cursor.login_status(), "ready")
            llm_cursor._ready_at = float("-inf")
            done.stdout = "Not logged in\n"
            self.assertEqual(llm_cursor.login_status(), "signed_out")
        with mock.patch.object(llm_cursor, "agent_exe", return_value=None):
            self.assertEqual(llm_cursor.login_status(), "missing")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class CursorSttTest(unittest.TestCase):
    def test_request_bytes(self):
        self.assertEqual(llm_cursor.encode_stt(b"RIFF", "audio/wav"), b"\x0a\x04RIFF\x12\x09audio/wav")
        self.assertEqual(llm_cursor.encode_stt(b"A", "audio/wav", "he")[-4:], b"\x1a\x02he")

    def test_reply_text_is_decoded(self):
        sent = []

        def fake_open(req, timeout):
            sent.append(req)
            return _Resp(b"\x0a\x05Hello")
        with mock.patch.object(llm_cursor, "_open", fake_open), \
                mock.patch.object(llm_cursor.cursor_auth, "token", return_value="tok"):
            self.assertEqual(llm_cursor.CursorStt().transcribe(b"RIFF"), "Hello")
        self.assertEqual(sent[0].get_header("Authorization"), "Bearer tok")

    def test_http_errors_name_only_the_code(self):
        err = urllib.error.HTTPError(llm_cursor.STT_URL, 401, "secret body", {}, None)
        with mock.patch.object(llm_cursor, "_open", side_effect=err), \
                mock.patch.object(llm_cursor.cursor_auth, "token", return_value="tok"):
            with self.assertRaisesRegex(llm_cursor.SttError, "^HTTP 401$"):
                llm_cursor.CursorStt().transcribe(b"RIFF")


if __name__ == "__main__":
    unittest.main()
