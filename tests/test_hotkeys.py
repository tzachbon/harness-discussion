"""The read shortcut's stored form: parsing, building it from keys, its label, and settings validation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_discussion import hotkeys, settings


class Parse(unittest.TestCase):
    def test_valid_shortcuts(self):
        self.assertEqual(hotkeys.parse("<ctrl>+<alt>+r"), (frozenset({"ctrl", "alt"}), "r"))
        self.assertEqual(hotkeys.parse("<cmd>+<f5>"), (frozenset({"cmd"}), "f5"))
        self.assertEqual(hotkeys.parse("r"), (frozenset(), "r"))  # no modifier needed
        self.assertEqual(hotkeys.parse("<key_50>"), (frozenset(), "key_50"))  # any other key, by its code
        self.assertEqual(hotkeys.parse("<cmd>+<key_50>"), (frozenset({"cmd"}), "key_50"))
        self.assertEqual(hotkeys.parse("<ctrl>+c"), (frozenset({"ctrl"}), "c"))
        self.assertEqual(hotkeys.parse("<ctrl>+<alt>"), (frozenset({"ctrl", "alt"}), None))  # modifiers alone

    def test_invalid_shortcuts(self):
        for spec in ("", "<ctrl>+r+s", "<ctrl>+f5", "<ctrl>+<r>", "<hyper>+r", "<ctrl>+!", "key_50",
                     "<key_x>", "<key_>", "<key_99999>", None, 3):
            self.assertIsNone(hotkeys.parse(spec), spec)

    def test_spec_orders_modifiers(self):
        self.assertEqual(hotkeys.spec({"alt", "ctrl"}, "R"), "<ctrl>+<alt>+r")
        self.assertEqual(hotkeys.spec({"shift"}, "F12"), "<shift>+<f12>")
        self.assertEqual(hotkeys.spec(set(), "r"), "r")
        self.assertEqual(hotkeys.spec({"cmd"}, "key_50"), "<cmd>+<key_50>")
        self.assertIsNone(hotkeys.spec({"ctrl"}, "Control_L"))
        self.assertEqual(hotkeys.spec({"alt", "ctrl"}), "<ctrl>+<alt>")
        self.assertIsNone(hotkeys.spec(set()))

    def test_label(self):
        with patch.object(hotkeys.sys, "platform", "win32"):
            self.assertEqual(hotkeys.label("<ctrl>+<alt>+r"), "Ctrl+Alt+R")
        with patch.object(hotkeys.sys, "platform", "darwin"):
            self.assertEqual(hotkeys.label("<cmd>+<alt>+r"), "⌥⌘R")
            self.assertEqual(hotkeys.label("<ctrl>+<alt>"), "⌃⌥")
        self.assertEqual(hotkeys.label(""), "Off")

    def test_keys_are_one_name_per_keycap(self):
        with patch.object(hotkeys.sys, "platform", "win32"):
            self.assertEqual(hotkeys.keys("<ctrl>+<alt>+r"), ["Ctrl", "Alt", "R"])
            self.assertEqual(hotkeys.keys("<cmd>+<f5>"), ["Win", "F5"])
        with patch.object(hotkeys.sys, "platform", "darwin"):
            self.assertEqual(hotkeys.keys("<cmd>+<alt>+r"), ["⌥", "⌘", "R"])
            self.assertEqual(hotkeys.keys("<ctrl>+<alt>"), ["⌃", "⌥"])
        self.assertEqual(hotkeys.keys(""), [])
        self.assertEqual(hotkeys.keys("<ctrl>+r+s"), [])


class Saved(unittest.TestCase):
    def load(self, value):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({"hotkey": value}), encoding="utf-8")
            return settings.load(str(path))["hotkey"]

    def test_off_and_valid_values_are_kept_and_invalid_ones_reset(self):
        self.assertEqual(self.load(""), "")
        self.assertEqual(self.load("<shift>+<alt>+x"), "<shift>+<alt>+x")
        self.assertEqual(self.load("r"), "r")
        self.assertEqual(self.load("<hyper>+x"), hotkeys.DEFAULT)
        self.assertEqual(self.load(5), hotkeys.DEFAULT)

    def test_a_shortcut_already_used_by_another_setting_is_a_conflict(self):
        cfg = {"hotkey": "<ctrl>+<alt>+r", "rewrite_hotkey": "<ctrl>+<alt>+e", "dictate_hotkey": ""}
        self.assertEqual(hotkeys.conflict(cfg, "rewrite_hotkey", "<alt>+<ctrl>+r"), "hotkey")
        self.assertIsNone(hotkeys.conflict(cfg, "rewrite_hotkey", "<ctrl>+<alt>+e"))
        self.assertIsNone(hotkeys.conflict(cfg, "dictate_hotkey", ""))


class WriterSettings(unittest.TestCase):
    def load(self, saved):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            if saved is not None:
                path.write_text(json.dumps(saved), encoding="utf-8")
            return settings.load(str(path))

    def test_a_fresh_file_follows_the_default_voice_order(self):
        s = self.load(None)
        self.assertEqual(s["writer_sources"], [["Cursor", True], ["Codex", True], ["Local", True]])
        self.assertEqual(s["stt_sources"], [["Local", True], ["Cursor", True], ["Codex", True]])
        self.assertIs(s["dictate_polish"], True)
        self.assertEqual((s["rewrite_hotkey"], s["dictate_hotkey"]), (hotkeys.REWRITE, hotkeys.DICTATE))

    def test_polish_must_be_a_bool(self):
        self.assertIs(self.load({"dictate_polish": False})["dictate_polish"], False)
        self.assertIs(self.load({"dictate_polish": "no"})["dictate_polish"], True)

    def test_an_existing_file_copies_the_voice_order_once(self):
        voices = [["Codex", True], ["Kokoro", True], ["Cursor", False]]
        self.assertEqual(self.load({"sources": voices})["writer_sources"],
                         [["Codex", True], ["Cursor", True], ["Local", True]])
        saved = {"sources": voices, "writer_sources": [["Local", False], ["Bogus", True], ["Local", True]]}
        self.assertEqual(self.load(saved)["writer_sources"], [["Local", False], ["Cursor", True], ["Codex", True]])

    def test_models_keep_only_known_sources_and_string_choices(self):
        saved = {"writer_models": {"Codex": {"model": "gpt", "effort": "low", "x": 1}, "Nope": {}, "Local": 3}}
        self.assertEqual(self.load(saved)["writer_models"], {"Codex": {"model": "gpt", "effort": "low"}})
        self.assertEqual(self.load({"writer_models": [], "modes": {}, "default_mode": " "})["default_mode"], "Fix errors")
