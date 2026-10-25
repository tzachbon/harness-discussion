"""Recent dictations: the raw transcript is kept even when tidy fails."""
import os
import tempfile
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
        app.cfg["dictation_history"] = keep
        app.settings_win = self.win = Mock(alive=True)
        with patch.object(writer, "rewrite", rewrite or (lambda text, *a, **kw: ("Tidy.", "Cursor"))):
            self.start(app)
            self.finish(app)

    def test_keeps_raw_and_tidied(self):
        self.dictate()
        self.assertEqual([(i["raw"], i["text"]) for i in history.load()], [("words", "Tidy.")])
        self.assertEqual(history.entries(), [("Tidy.", "Tidy."), ("    Original: words", "words")])
        self.assertEqual(history.rows(), [("Tidy.", "Tidy.", "words")])
        self.win.refresh_history.assert_called()  # an open Settings list shows it at once

    def test_keeps_raw_when_tidy_fails(self):
        def broken(*_a, **_kw):
            raise writer.Error("No source could rewrite.")
        self.dictate(rewrite=broken)
        self.assertEqual(history.entries(), [("words", "words")])

    def test_off_saves_nothing_and_clear_deletes(self):
        self.dictate(keep=False)
        self.assertEqual(history.load(), [])
        for _ in range(history.LIMIT + 2):
            history.add("x")
        self.assertEqual(len(history.load()), history.LIMIT)
        history.clear()
        self.assertEqual(history.load(), [])
