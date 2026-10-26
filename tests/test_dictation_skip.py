"""Skip on the Transcribing toast: types the raw transcript instead of waiting for tidy."""
import threading
import time
import unittest
import unittest.mock
from types import SimpleNamespace

from harness_discussion import app_writer, writer
from tests.test_app_writer import FakeRecorder, _App


class SkipTest(unittest.TestCase):
    def setUp(self):
        FakeRecorder.duration = 2.0
        self.app = _App()
        self.addCleanup(self.app._close_writer)
        self.heard = threading.Event()
        self.heard.set()
        self.app.stt_sources = [SimpleNamespace(name="Codex", status=lambda: "ready",
                                                transcribe=lambda audio, language="": self.heard.wait(5) and "i thinks so")]
        self.app.host.edit.type_at_caret = lambda text, ctx, **kw: self.app.host.edit.pasted.append(text) or True
        patcher = unittest.mock.patch.object(app_writer.mic, "Recorder", FakeRecorder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def hold(self):
        self.app._dictate_down()
        self.app.drain("recording_ready")
        self.app._dictate_up()
        self.app.drain("recording_done")

    def titles(self):
        return [title for title, _ in self.app.toast_.actions]

    def test_skip_shows_only_when_tidy_is_on(self):
        for polish, titles in ((True, ["Skip", "×"]), (False, ["×"])):
            with self.subTest(polish=polish):
                self.app.cfg["dictate_polish"] = polish
                self.hold()
                self.assertEqual(self.titles(), titles)
                self.app.drain()

    def test_skip_while_transcribing_types_the_raw_transcript_without_a_rewrite(self):
        self.heard.clear()
        self.hold()
        self.app.toast_.action("Skip")()
        self.assertEqual(self.app.toast_.shown[-1][0], "Transcribing")
        self.assertEqual(self.titles(), ["×"])  # still cancellable
        self.heard.set()
        self.app.drain()
        self.assertEqual(self.app.host.edit.pasted, ["i thinks so"])
        self.assertEqual(self.app.writer_sources[0].texts, [])
        self.assertFalse(self.app.toast_.visible)

    def test_skip_while_tidying_stops_the_rewrite_and_types_the_raw_transcript(self):
        tidying = threading.Event()

        def slow(system, text, model, effort, cancelled):
            tidying.set()
            while not cancelled():
                time.sleep(0.01)
            raise writer.Cancelled("The rewrite was cancelled.")
        self.app.writer_sources[0].rewrite = slow
        self.hold()
        self.assertTrue(tidying.wait(5))
        self.app.toast_.action("Skip")()
        self.app.drain()
        self.assertEqual(self.app.host.edit.pasted, ["i thinks so"])
        self.assertFalse(self.app.toast_.visible)

    def test_a_tidy_that_finishes_despite_skip_still_types_the_raw_transcript(self):
        skipped = threading.Event()

        def press():
            self.app.toast_.action("Skip")()
            skipped.set()

        def deaf(system, text, model, effort, cancelled):
            self.app.events.put(("call", press))
            skipped.wait(5)
            return "I think so."
        self.app.writer_sources[0].rewrite = deaf
        self.hold()
        self.app.drain()
        self.assertEqual(self.app.host.edit.pasted, ["i thinks so"])

    def test_cancel_after_skip_types_nothing(self):
        self.heard.clear()
        self.hold()
        self.app.toast_.action("Skip")()
        self.app.toast_.action("×")()
        self.heard.set()
        self.app.drain()
        self.assertEqual(self.app.host.edit.pasted, [])
        self.assertFalse(self.app.toast_.visible)
