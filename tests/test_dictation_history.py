"""Recent dictations: the raw transcript is kept even when tidy fails."""
import os
import tempfile
import time
from unittest.mock import Mock, patch

from harness_discussion import history, writer
from tests.dictation_helpers import DictationCase

class HistoryTest(DictationCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "h.json")
        patcher = patch.object(history, "PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def dictate(self, keep=True, rewrite=None):
        app, _ = self.make_app()
        self.app = app
        app.cfg["dictation_history"] = keep
        app.settings_win = self.win = Mock(alive=True)
        with patch.object(writer, "rewrite", rewrite or (lambda text, *a, **kw: ("Tidy.", "Cursor"))):
            self.start(app)
            self.finish(app)

    def test_keeps_raw_and_tidied(self):
        self.dictate()
        self.assertEqual([(i["raw"], i["text"]) for i in history.load()], [("words", "Tidy.")])
        self.assertEqual(history.entries(), [("Tidy.", "Tidy."), ("words", "    Original: words")])
        self.assertEqual(history.rows(), [("Tidy.", "Tidy.", "words")])
        self.win.refresh_history.assert_called()  # an open Settings list shows it at once

    def test_menu_entries_copy_the_full_text_not_the_preview(self):
        history.add("line one\n  line two")
        self.assertEqual(history.entries(), [("line one\n  line two", "line one line two")])

    def test_keeps_raw_when_tidy_fails(self):
        def broken(*_a, **_kw):
            raise writer.Error("No source could rewrite.")
        self.dictate(rewrite=broken)
        self.assertEqual(history.entries(), [("words", "words")])

    def test_skipping_tidy_keeps_only_raw(self):
        def skipped(text, *_a, cancelled, **_kw):
            self.app.events.put(("call", self.app.toast_.action("Skip")))
            while not cancelled():
                time.sleep(0.01)
            raise writer.Cancelled("The rewrite was cancelled.")
        self.dictate(rewrite=skipped)
        self.assertEqual([(i["raw"], i["text"]) for i in history.load()], [("words", None)])
        self.app.host.edit.type_at_caret.assert_called_once()
        self.assertEqual(self.app.host.edit.type_at_caret.call_args[0][0], "words")

    def test_turning_it_off_mid_dictation_keeps_nothing(self):
        def off_while_tidying(text, *_a, **_kw):
            self.app.cfg["dictation_history"] = False
            history.clear()
            return "Tidy.", "Cursor"
        self.dictate(rewrite=off_while_tidying)
        self.assertEqual(history.load(), [])

    def test_off_saves_nothing_and_clear_deletes(self):
        self.dictate(keep=False)
        self.assertEqual(history.load(), [])
        self.assertEqual(history.load(), [])
        for _ in range(history.LIMIT + 2):
            history.add("x")
        self.assertEqual(len(history.load()), history.LIMIT)
        open(self.path + ".tmp", "w").close()  # left by a save that failed mid-write
        history.clear()
        self.assertEqual(history.load(), [])
        self.assertFalse(os.path.exists(self.path + ".tmp"))
