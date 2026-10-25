"""Shortcut recording uses physical modifiers and ends cleanly on release or cancellation."""
import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness_discussion import hotkeys, settings
from harness_discussion.win.shortcut_row import ShortcutRow


class Keys:  # keycaps.Keys without Tk: what the row reads as
    text = ""

    def show(self, spec, after=""):
        self.text = hotkeys.label(spec) + after if hotkeys.keys(spec) else "Off"

    def say(self, text):
        self.text = text

class Recorder(ShortcutRow):
    def __init__(self):
        self.cfg = dict(settings.DEFAULTS)
        self.win, self.on_record = Mock(), Mock()
        self.shortcuts = {key: {"label": Keys(), "error": {"text": ""}} for key in hotkeys.SHORTCUTS}

    def _set(self, key, value):
        self.cfg[key] = value


class ShortcutRecording(unittest.TestCase):
    def setUp(self):
        self.row = Recorder()
        self.win_down = patch("harness_discussion.win.shortcut_row.win_down", return_value=False)
        self.win_down.start()
        self.addCleanup(self.win_down.stop)
        key_state = patch("harness_discussion.win.shortcut_row.ctypes.windll.user32.GetKeyState", return_value=0)
        key_state.start()
        self.addCleanup(key_state.stop)

    def press(self, code, state=0):
        self.row._hotkey_key(SimpleNamespace(keycode=code, state=state, keysym="Escape" if code == 27 else ""))

    def release(self, code):
        self.row._hotkey_release(SimpleNamespace(keycode=code))

    def test_requested_chords_for_each_action(self):
        for action in hotkeys.SHORTCUTS:
            for codes, expected in (((0x11, 0x5B), "<ctrl>+<cmd>"),
                                    ((0x11, 0x10, 0x41), "<ctrl>+<shift>+a"),
                                    ((0x11, 0x12, 0x41), "<ctrl>+<alt>+a")):
                with self.subTest(action=action, codes=codes):
                    self.row.cfg = {**settings.DEFAULTS, **dict.fromkeys(hotkeys.SHORTCUTS, "")}
                    self.row._record_hotkey(action)
                    for code in codes:
                        self.press(code)
                    if len(codes) == 2:
                        self.assertEqual(self.row.cfg[action], "")
                        self.release(codes[-1])
                        self.assertEqual(self.row.cfg[action], "")
                        self.release(codes[0])
                    self.assertEqual(self.row.cfg[action], expected)
                    self.assertIsNone(self.row._recording)
                    self.row.win.unbind.assert_any_call("<KeyRelease>")
                    self.row.on_record.assert_called_with(False)

    def test_two_physical_control_keys_wait_until_both_are_up(self):
        self.row._record_hotkey()
        for code in (0xA2, 0xA3, 0x5C):
            self.press(code)
        self.release(0xA2)
        self.release(0x5C)
        self.assertEqual(self.row._recording, "hotkey")
        self.release(0xA3)
        self.assertEqual(self.row.cfg["hotkey"], "<ctrl>+<cmd>")

    def test_generic_modifier_keycodes_wait_until_both_sides_are_up(self):
        for code, left, right, expected in ((0x11, 0xA2, 0xA3, "<ctrl>"),
                                            (0x10, 0xA0, 0xA1, "<shift>"),
                                            (0x12, 0xA4, 0xA5, "<alt>")):
            with self.subTest(code=code):
                self.row._record_hotkey()
                self.press(code)
                self.press(code)  # Tk reports the same code for either side.
                with patch("harness_discussion.win.shortcut_row.ctypes.windll.user32.GetKeyState",
                           side_effect=lambda vk: 0x8000 if vk == right else 0):
                    self.release(code)
                self.assertEqual(self.row._recording, "hotkey")
                self.release(code)
                self.assertIsNone(self.row._recording)
                self.assertEqual(self.row.cfg["hotkey"], expected)

    def test_latest_equal_size_modifier_chord_is_saved(self):
        self.row._record_hotkey()
        self.press(0x11)
        self.press(0x10)
        self.release(0x10)
        self.press(0x12)
        self.assertEqual(self.row.shortcuts["hotkey"]["label"].text, "Ctrl+Alt…")
        self.release(0x12)
        self.release(0x11)
        self.assertEqual(self.row.cfg["hotkey"], "<ctrl>+<alt>")

    def test_escape_and_another_row_discard_partial_chords(self):
        old = dict(self.row.cfg)
        self.row._record_hotkey()
        self.press(0x11)
        self.press(27)
        self.assertEqual(self.row.cfg, old)
        self.row._record_hotkey()
        self.press(0x11)
        self.row._record_hotkey("rewrite_hotkey")
        self.press(0x41)
        self.assertEqual(self.row.cfg["rewrite_hotkey"], "a")
        self.assertEqual(self.row.shortcuts["hotkey"]["label"].text, hotkeys.label(old["hotkey"]))

    def test_modifier_only_duplicate_names_the_other_action(self):
        self.row.cfg["dictate_hotkey"] = "<ctrl>+<cmd>"
        old = self.row.cfg["rewrite_hotkey"]
        self.row._record_hotkey("rewrite_hotkey")
        self.press(0x11)
        self.press(0x5B)
        self.release(0x11)
        self.release(0x5B)
        self.assertEqual(self.row.cfg["rewrite_hotkey"], old)
        self.assertIn("Hold to dictate already uses Ctrl+Win", self.row.shortcuts["rewrite_hotkey"]["error"]["text"])
