"""Summarize: the verified download, the local server request, and the app flow, with no network or model."""
import hashlib
import io
import json
import queue
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from harness_discussion import app_summarize, summarize, writer


class _Reply(io.BytesIO):
    def __init__(self, data, url="https://huggingface.co/x", status=200):
        super().__init__(data)
        self.url, self.status = url, status

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _tar(members):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as t:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(body), 0o755
            t.addfile(info, io.BytesIO(body))
    return data.getvalue()


def _pin(data):
    return len(data), hashlib.sha256(data).hexdigest()


class DownloadTest(unittest.TestCase):
    def vulkan_runtime(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr(summarize.SERVER, b"vulkan runtime")
        body = data.getvalue()
        name = f"llama-{summarize.BUILD}-bin-win-vulkan-x64.zip"
        self.bodies[name] = body
        return name, *_pin(body)

    def test_runtime_upgrade_reuses_the_verified_model_and_preserves_old_runtime(self):
        summarize.download()
        old_model = summarize.model_path().read_bytes()
        seen = []
        with mock.patch.object(summarize, "runtime", return_value=self.vulkan_runtime()), mock.patch.object(
                summarize, "_download_open", wraps=self.serve) as opened:
            summarize.download(lambda done, total: seen.append((done, total)))
            self.assertEqual(summarize.status(), "ready")
        self.assertEqual(opened.call_count, 1)  # only the runtime archive, never the 2.5 GB model again
        self.assertEqual(summarize.model_path().read_bytes(), old_model)
        self.assertTrue((self.dir / "runtime").is_dir())
        self.assertTrue((self.dir / "runtime-vulkan").is_dir())
        self.assertEqual(seen[-1][0], seen[-1][1])

    def test_failed_runtime_upgrade_keeps_model_provenance_and_old_runtime(self):
        summarize.download()
        marker = summarize._marker().read_bytes()
        name, size, _ = self.vulkan_runtime()
        with mock.patch.object(summarize, "runtime", return_value=(name, size, "0" * 64)):
            with self.assertRaises(summarize.Error):
                summarize.download()
        self.assertEqual(summarize._marker().read_bytes(), marker)
        self.assertEqual(summarize.model_path().read_bytes(), self.model)
        self.assertTrue((self.dir / "runtime").is_dir())

    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory())) / "summarize"
        self.archive = _tar({f"llama-{summarize.BUILD}/{summarize.SERVER}": b"#!/bin/sh\n"})
        self.model = b"GGUF" + b"\0" * 2000
        self.enterContext(mock.patch.object(summarize, "DIR", self.dir))
        self.enterContext(mock.patch.object(summarize, "runtime", return_value=("rt.tar.gz", *_pin(self.archive))))
        self.enterContext(mock.patch.object(summarize, "MODEL", ("https://huggingface.co/m/model.gguf",
                                                                 *_pin(self.model))))
        self.bodies = {"rt.tar.gz": self.archive, "model.gguf": self.model}
        self.enterContext(mock.patch.object(summarize, "_download_open", self.serve))

    def serve(self, request, timeout):
        return _Reply(self.bodies[request.full_url.rsplit("/", 1)[1]])

    def test_a_verified_download_is_ready_and_remove_deletes_it(self):
        seen = []
        self.assertEqual(summarize.status(), "missing")
        summarize.download(lambda done, total: seen.append((done, total)))
        self.assertEqual(summarize.status(), "ready")
        self.assertEqual(seen[-1][0], seen[-1][1])
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["model.gguf", "ready.json", "runtime"])
        summarize.remove()
        self.assertEqual(summarize.status(), "missing")

    def test_a_tampered_model_leaves_nothing_that_looks_ready(self):
        self.bodies["model.gguf"] = b"GGUF" + b"\1" * 2000
        with self.assertRaisesRegex(summarize.Error, "verification"):
            summarize.download()
        self.assertEqual(summarize.status(), "missing")
        self.assertEqual([p.name for p in self.dir.iterdir()], [])

    def test_cancel_stops_the_download_and_cleans_up(self):
        with self.assertRaises(summarize.Cancelled):
            summarize.download(cancelled=lambda: True)
        self.assertEqual([p.name for p in self.dir.iterdir()], [])

    def test_an_archive_that_escapes_its_folder_is_refused(self):
        self.bodies["rt.tar.gz"] = bad = _tar({"../escape": b"x"})
        with mock.patch.object(summarize, "runtime", return_value=("rt.tar.gz", *_pin(bad))):
            with self.assertRaisesRegex(summarize.Error, "unsafe"):
                summarize.download()
        zipped = io.BytesIO()
        with zipfile.ZipFile(zipped, "w") as z:
            z.writestr("../escape.exe", b"x")
        path = self.dir / "rt.zip"
        self.dir.mkdir(exist_ok=True)
        path.write_bytes(zipped.getvalue())
        with self.assertRaisesRegex(summarize.Error, "unsafe"):
            summarize._extract(path, self.dir / "out")
        self.assertFalse((self.dir.parent / "escape.exe").exists())

    def test_only_https_github_and_hugging_face_are_trusted(self):
        for url, ok in (("https://huggingface.co/a", True), ("https://us.aws.cdn.hf.co/a", True),
                        ("https://release-assets.githubusercontent.com/a", True), ("http://huggingface.co/a", False),
                        ("https://evil.example/a", False), ("https://huggingface.co.evil.example/a", False),
                        ("https://u:p@huggingface.co/a", False)):
            self.assertEqual(summarize._trusted(url), ok, url)

    def test_every_pinned_runtime_has_a_sha256(self):
        for name, size, sha in summarize.RUNTIMES.values():
            self.assertTrue(name.startswith(f"llama-{summarize.BUILD}-bin-") and size > 0 and len(sha) == 64)


class ServerTest(unittest.TestCase):
    def test_the_request_disables_thinking_caps_the_text_and_cleans_the_reply(self):
        sent = []

        def local(request, timeout):
            sent.append(json.loads(request.data))
            content = "<think>hmm</think>\n## Summary\n- It is **short**.\n- It reads well."
            return _Reply(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
        server = summarize.Server()
        server._proc, server._port = SimpleNamespace(poll=lambda: None), 1234
        with mock.patch.object(summarize, "_local_open", local):
            self.assertEqual(server.summarize("word " * 10000, "Hebrew"), "Summary It is short. It reads well.")
        body = sent[0]
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertIn("in Hebrew", body["messages"][0]["content"])
        user = body["messages"][1]["content"]
        self.assertTrue(user.startswith("<text>\n") and user.endswith("\n</text>"))
        self.assertEqual(len(user), summarize.MAX_CHARS + len("<text>\n\n</text>"))

    def test_an_empty_reply_is_an_error(self):
        server = summarize.Server()
        server._proc, server._port = SimpleNamespace(poll=lambda: None), 1234
        reply = json.dumps({"choices": [{"message": {"content": "<think>x</think>"}}]}).encode()
        with mock.patch.object(summarize, "_local_open", lambda request, timeout: _Reply(reply)):
            with self.assertRaisesRegex(summarize.Error, "no summary"):
                server.summarize("text", "English")

    def test_local_writer_sends_the_mode_prompt_and_keeps_line_breaks(self):
        sent = []

        def local(request, timeout):
            sent.append(json.loads(request.data))
            content = "<think>x</think>\nHi Dana,\n\nThanks.\n"
            return _Reply(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
        server = summarize.Server()
        server._proc, server._port = SimpleNamespace(poll=lambda: None), 1234
        source = summarize.LocalWriter(server)
        with mock.patch.object(summarize, "_local_open", local):
            self.assertEqual(source.rewrite("SYSTEM", "hi dana thanks", None, None, lambda: False),
                             "Hi Dana,\n\nThanks.")
            with self.assertRaises(writer.Cancelled):
                source.rewrite("SYSTEM", "hi", None, None, lambda: True)
        self.assertEqual([m["content"] for m in sent[0]["messages"]], ["SYSTEM", "hi dana thanks"])
        self.assertEqual(sent[0]["max_tokens"], 800)
        with self.assertRaisesRegex(summarize.Error, "too long"):
            source.rewrite("SYSTEM", "x" * (summarize.MAX_CHARS // 2 + 1), None, None, lambda: False)

    def test_local_writer_refuses_a_reply_cut_off_at_the_token_limit(self):
        sent = []

        def local(request, timeout):
            sent.append(json.loads(request.data))
            return _Reply(json.dumps({"choices": [{"message": {"content": "你好"}, "finish_reason": "length"}]}).encode())
        server = summarize.Server()
        server._proc, server._port = SimpleNamespace(poll=lambda: None), 1234
        with mock.patch.object(summarize, "_local_open", local):
            with self.assertRaisesRegex(summarize.Error, "cut off"):
                summarize.LocalWriter(server).rewrite("SYSTEM", "你" * 3000, None, None, lambda: False)
            self.assertEqual(server.summarize("text", "English"), "你好")  # summaries may still end early
        self.assertEqual(sent[0]["max_tokens"], 3000)


    def test_closed_server_cannot_restart_from_a_late_prewarm(self):
        server = summarize.Server()
        server.close()
        with mock.patch.object(summarize.subprocess, "Popen") as spawn:
            with self.assertRaises(summarize.Error):
                server.prewarm()
            spawn.assert_not_called()

    def test_stop_does_not_wait_for_the_model_to_load(self):
        proc = mock.Mock()
        proc.poll.return_value = None
        server, errors = summarize.Server(), []

        def run():
            try:
                server.summarize("text", "English")
            except summarize.Error as e:
                errors.append(str(e))
        with mock.patch.object(summarize, "_server_path", return_value=Path(sys.executable)), \
                mock.patch.object(summarize, "model_path", return_value=Path(__file__)), \
                mock.patch.object(summarize.subprocess, "Popen", return_value=proc), \
                mock.patch.object(summarize, "_local_open", side_effect=OSError):
            worker = threading.Thread(target=run)
            worker.start()
            time.sleep(0.3)  # loading: /health keeps failing
            started = time.monotonic()
            server.stop()
            self.assertLess(time.monotonic() - started, 1)
            worker.join(5)
        proc.terminate.assert_called_once()
        self.assertEqual(errors, ["The Summarize model stopped while loading."])


class _App(app_summarize.Summaries):
    """The parts of core.App the mixin uses."""

    def __init__(self, status):
        self.events, self._read_id, self.settings_win = queue.Queue(), 0, None
        self.cfg, self.abouts = {"about_me": "Dana, PM", "about_me_on": True}, []
        self.notes, self.played, self.confirms, self.timers = [], [], [], []
        self.host = SimpleNamespace(notify=self.notes.append, after=lambda ms, fn: self.timers.append(fn),
                                    confirm=lambda title, message: self.confirms.append(title) or self.answer)
        self.answer = True
        self.popup = SimpleNamespace(paused=False, progress=[], summary=0)
        self.popup.set_summarize_progress = self.popup.progress.append
        self.popup.set_summary = lambda: setattr(self.popup, "summary", self.popup.summary + 1)
        self.chain = SimpleNamespace(pause=lambda: self.played.append("paused"))
        with mock.patch.object(summarize, "status", return_value=status):
            self._init_summaries()
        self.summary_server = SimpleNamespace(summarize=lambda text, language, about="": self.abouts.append(about)
                                              or f"{language}: short.",
                                              stop=lambda: self.played.append("server stopped"))

    def stop(self):
        self._read_id += 1

    def play(self, text, tag):
        self.played.append((text, tag))

    def drain(self):
        kind, *args = self.events.get(timeout=5)
        {"summary": self._summary_done, "summarize_done": self._summarize_downloaded,
         "summarize_progress": self._set_summarize_progress}[kind](*args)
        return kind


class FlowTest(unittest.TestCase):
    def test_a_ready_model_summarizes_and_reads_the_summary(self):
        app = _App("ready")
        app.summarize("שלום עולם, זה טקסט ארוך", None)
        self.assertEqual(app.popup.summary, 1)
        app.drain()
        self.assertEqual(app.played, [("Hebrew: short.", None)])
        self.assertIn("<about>\nDana, PM\n</about>", app.abouts[0])  # About you reaches the summary prompt
        app.summarize("text", "en-US")
        app.drain()
        app.timers[0]()  # the first summary's idle timer: a newer summary keeps the server
        app.timers[1]()  # 5 minutes after the latest one
        for _ in range(50):
            if "server stopped" in app.played:
                break
            time.sleep(0.02)
        self.assertEqual(app.played[1:], [("English: short.", "en-US"), "server stopped"])

    def test_stop_before_the_summary_arrives_reads_nothing_and_still_stops_the_server_later(self):
        app = _App("ready")
        app.summarize("text", "en-US")
        app.stop()
        app.drain()
        self.assertEqual(app.played, [])
        app.timers[0]()
        for _ in range(50):
            if app.played:
                break
            time.sleep(0.02)
        self.assertEqual(app.played, ["server stopped"])

    def test_a_new_summary_cancels_the_last_idle_timer_when_it_starts(self):
        app = _App("ready")
        app.summarize("text", "en-US")
        app.drain()
        app.summary_server.summarize = lambda text, language, about="": time.sleep(0.2) or "later."
        app.summarize("more", "en-US")
        app.timers[0]()  # fires while the second summary is still running
        time.sleep(0.05)
        self.assertNotIn("server stopped", app.played)

    def test_a_pause_while_summarizing_holds_the_summary(self):
        app = _App("ready")
        app.summarize("text", "en-US")
        app.popup.paused = True
        app.drain()
        self.assertEqual(app.played, [("English: short.", "en-US"), "paused"])

    def test_a_failed_summary_names_the_error_without_the_text(self):
        app = _App("ready")
        app.summary_server.summarize = lambda text, language, about: (_ for _ in ()).throw(ValueError(text))
        app.summarize("secret words", "en-US")
        app.drain()
        self.assertEqual(app.notes, ["Could not summarize. ValueError"])

    def test_a_missing_model_asks_first_and_cancel_downloads_nothing(self):
        app = _App("missing")
        app.answer = False
        with mock.patch.object(summarize, "download") as download:
            app.summarize("text", None)
        self.assertEqual(app.confirms, ["Download the Summarize model?"])
        download.assert_not_called()
        self.assertEqual(app.popup.summary, 0)

    def test_an_accepted_download_shows_progress_then_ready(self):
        app = _App("missing")

        def fake(progress, cancelled):
            progress(50, 100)
            progress(100, 100)
        with mock.patch.object(summarize, "download", fake), mock.patch.object(summarize, "status",
                                                                                 return_value="ready"):
            app.summarize("text", None)
            while app.drain() != "summarize_done":
                pass
        self.assertEqual(app.popup.progress, [0.0, 0.5, 1.0, None])
        self.assertEqual(app.summarize_state(), ("ready", None))
        self.assertEqual(app.notes, [])

    def test_settings_cancel_and_remove(self):
        app = _App("missing")
        app._summarize_progress = 0.3
        app.summarize_action()
        self.assertTrue(app._summarize_cancel.is_set())
        app._summarize_progress, app._summarize_status = None, "ready"
        with mock.patch.object(summarize, "remove") as remove, mock.patch.object(summarize, "status",
                                                                                   return_value="missing"):
            app.summarize_action()
        remove.assert_called_once()
        self.assertEqual((app.played, app.summarize_state()), (["server stopped"], ("missing", None)))

    def test_a_failed_download_says_why(self):
        app = _App("missing")
        with mock.patch.object(summarize, "status", return_value="missing"):
            app._summarize_downloaded(OSError("no route"))
            app._summarize_downloaded(summarize.Cancelled("x"))
        self.assertEqual(app.notes, ["Could not download the Summarize model. Check your connection and try again."])


if __name__ == "__main__":
    unittest.main()
