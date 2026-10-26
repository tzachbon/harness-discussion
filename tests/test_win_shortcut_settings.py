"""Settings shortcut rows on Windows: recording, keycaps, and refusing a chord another row uses."""
import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

from unittest.mock import patch

from harness_discussion import settings
from harness_discussion.win import settings_ui
from harness_discussion.win.trigger import key_name
from test_win_writing_settings import SettingsWindowCase

class ShortcutSettings(SettingsWindowCase):
    def press(self, w, keycode, state):
        w.win.event_generate("<KeyPress>", keycode=keycode, state=state, when="now")

    def test_read_shortcut_still_records_and_reports_recording(self):
        recording = []
        w = self.make_window(on_record=recording.append)
        w.nav[settings_ui.PAGES.index("Reading")].invoke()
        self.assertEqual(w.hotkey_lbl.text, "Ctrl+Alt+R")
        w._record_hotkey()
        self.assertEqual(w.hotkey_lbl.text, "Press keys…")
        self.press(w, ord("T"), 0x20004)  # Ctrl+Alt
        self.assertEqual(self.changes[-1], ("hotkey", "<ctrl>+<alt>+t"))
        self.assertEqual(w.hotkey_lbl.text, "Ctrl+Alt+T")
        self.assertEqual(recording, [True, False])

    def test_any_key_records_alone_or_with_modifiers(self):
        recording = []
        w = self.make_window(on_record=recording.append)
        w._record_hotkey()
        self.press(w, 0x11, 0)  # Ctrl alone waits for the key
        self.assertEqual(w.hotkey_lbl.text, "Ctrl…")
        self.press(w, 0xC0, 0x4)  # Ctrl+`
        self.assertEqual(self.changes[-1], ("hotkey", "<ctrl>+<key_192>"))
        self.assertEqual(w.hotkey_lbl.text, "Ctrl+" + key_name(0xC0))
        for keycode, state, spec in ((0x7C, 0, "<key_124>"), (ord("C"), 0x4, "<ctrl>+c"), (0x20, 0, "<key_32>")):
            w._record_hotkey()
            self.press(w, keycode, state)  # F13, Ctrl+C, and Space alone
            self.assertEqual(self.changes[-1], ("hotkey", spec))
        self.assertIsNone(w._recording)
        self.assertEqual(recording[-1], False)
        with patch("harness_discussion.win.shortcut_row.win_down", return_value=True):
            w._record_hotkey()
            self.press(w, 0x74, 0)  # Win+F5: Tk's state has no Win bit
        self.assertEqual(self.changes[-1], ("hotkey", "<cmd>+<f5>"))
        self.assertEqual(w.hotkey_lbl.text, "Win+F5")

    def test_legacy_setter_still_targets_read_after_rewrite_recording_is_cancelled(self):
        w = self.make_window()
        for key in ("rewrite_hotkey", "dictate_hotkey"):
            w.shortcuts[key]["change"].invoke()
            self.press(w, 0x1B, 0)
            w._set_hotkey("")
            self.assertEqual(w.cfg[key], settings.DEFAULTS[key])
            self.assertEqual(w.cfg["hotkey"], "")

    def test_rewrite_and_dictate_shortcuts_record_and_refuse_a_used_chord(self):
        recording = []
        w = self.make_window(on_record=recording.append)
        rewrite, dictate = w.shortcuts["rewrite_hotkey"], w.shortcuts["dictate_hotkey"]
        self.assertEqual((rewrite["label"].text, dictate["label"].text), ("Ctrl+Shift+E", "Ctrl+Shift+D"))
        rewrite["change"].invoke()
        self.press(w, ord("R"), 0x20004)  # the read shortcut's chord
        self.assertEqual(rewrite["label"].text, "Ctrl+Shift+E")
        self.assertEqual(rewrite["error"]["text"], "Read shortcut already uses Ctrl+Alt+R.")
        self.assertEqual(self.changes, [])
        rewrite["change"].invoke()
        self.assertEqual(rewrite["error"]["text"], "")
        self.press(w, ord("W"), 0x20005)  # Ctrl+Alt+Shift
        self.assertEqual(self.changes[-1], ("rewrite_hotkey", "<ctrl>+<alt>+<shift>+w"))
        w.shortcuts["hotkey"]["change"].invoke()
        dictate["off"].invoke()
        self.assertEqual(w.hotkey_lbl.text, "Ctrl+Alt+R")
        self.assertEqual(self.changes[-1], ("dictate_hotkey", ""))
        self.assertEqual(dictate["label"].text, "Off")
        w.shortcuts["hotkey"]["change"].invoke()
        self.press(w, 0x1B, 0)  # Esc keeps the old one
        self.assertEqual(w.hotkey_lbl.text, "Ctrl+Alt+R")
        self.assertEqual(recording, [True, False, True, False, True, False, True, False])  # Off resumes too, as before

if __name__ == "__main__":
    unittest.main()
