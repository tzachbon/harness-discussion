"""Settings → Dictation → Recent dictations on Windows."""
import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

import gc
import tkinter as tk
from unittest.mock import patch

from PIL import Image
from harness_discussion import history, settings
from harness_discussion.win import settings_ui

class HistorySettingsUi(unittest.TestCase):
    def setUp(self):
        for p in (patch("harness_discussion.win.shortcut_row.win_down", return_value=False),
                  patch("harness_discussion.win.writing_page.mic.input_devices", return_value=[]),
                  patch.object(settings_ui.settings, "startup_enabled", return_value=False),
                  patch.object(settings_ui.SettingsWindow, "focus", lambda self: None),
                  patch.object(settings_ui, "stt_connection", return_value=("", "")),
                  patch.object(history, "rows", return_value=[("Tidy.", "Tidy.", "words")]),
                  patch.object(history, "clear")):
            p.start()
            self.addCleanup(p.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.changes, self.copied, self.refreshed = [], [], []
        self.window = settings_ui.SettingsWindow(
            self.root, dict(settings.DEFAULTS), Image.new("RGBA", (32, 32)), lambda *a: self.changes.append(a),
            lambda _text: None, on_copy=self.copied.append, on_history_changed=lambda: self.refreshed.append(1))

    def tearDown(self):
        if self.window.alive:
            self.window.close()
        self.root.destroy()
        self.window = self.root = None
        gc.collect()  # Tk cycles must release their interpreter on its owning thread.

    def test_copies_the_tidied_text_and_the_original(self):
        for row in self.window.history_card.winfo_children():
            for b in row.winfo_children():
                if b.winfo_class() == "TButton":
                    b.invoke()
        self.assertEqual(sorted(self.copied), ["Tidy.", "words"])

    def test_turning_it_off_deletes_the_saved_dictations(self):
        self.window.history_on.set(False)
        self.window._history_toggled()
        self.assertEqual(self.changes[-1], ("dictation_history", False))
        history.clear.assert_called_once()
        self.assertEqual(self.refreshed, [1])


if __name__ == "__main__":
    unittest.main()
