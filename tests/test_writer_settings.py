"""Settings → Writing and Dictation changes, and that each survives a save and load."""
import json
import tempfile
import unittest
from pathlib import Path

from harness_discussion import settings
from harness_discussion import writer_settings as ws

MODELS = {"Codex": [{"id": "gpt-5", "efforts": ["low", "medium"]}, {"id": "mini", "efforts": []}],
          "Cursor": [{"id": "fast", "label": "Fast"}]}


class OrderTest(unittest.TestCase):
    def test_move_and_toggle(self):
        order = [["Cursor", True], ["Codex", True], ["Local", True]]
        self.assertEqual(ws.move(order, "Codex", -1), [["Codex", True], ["Cursor", True], ["Local", True]])
        self.assertEqual(ws.move(order, "Local", 1), order)
        self.assertEqual(ws.toggle(order, "Local", False)[2], ["Local", False])
        self.assertEqual(order[2], ["Local", True])


class ModelTest(unittest.TestCase):
    def test_choices(self):
        self.assertEqual(ws.model_choices("Local", MODELS), [("", "Qwen3-4B")])
        self.assertEqual(ws.model_choices("Cursor", MODELS), [("", "Default"), ("fast", "Fast")])
        self.assertEqual(ws.effort_choices("Codex", MODELS, "gpt-5"), ["low", "medium"])
        self.assertEqual(ws.effort_choices("Codex", MODELS, "gone"), [])

    def test_an_effort_the_model_does_not_take_is_dropped(self):
        chosen = ws.set_model({}, "Codex", "gpt-5", "low", MODELS)
        self.assertEqual(chosen, {"Codex": {"model": "gpt-5", "effort": "low"}})
        self.assertEqual(ws.set_model(chosen, "Codex", "mini", "low", MODELS), {"Codex": {"model": "mini"}})
        self.assertEqual(ws.set_model(chosen, "Codex", "", "low", MODELS), {})
        self.assertEqual(ws.chosen_model(chosen, "Codex"), ("gpt-5", "low"))


class ModeTest(unittest.TestCase):
    def test_add_picks_a_free_name(self):
        modes, name = ws.add_mode([])
        self.assertEqual(name, "New mode")
        self.assertEqual(ws.add_mode(modes)[1], "New mode 2")

    def test_rename_and_edit_a_custom_mode(self):
        modes, _ = ws.add_mode([])
        modes, name, error = ws.save_mode(modes, "New mode", " Slack ", "Short and casual.")
        self.assertEqual((modes, name, error), ([{"name": "Slack", "instructions": "Short and casual."}], "Slack", None))

    def test_a_builtin_keeps_its_name_and_reset_restores_it(self):
        modes, name, _ = ws.save_mode([], "Email", "Renamed", "Formal.")
        self.assertEqual((modes, name), ([{"name": "Email", "instructions": "Formal."}], "Email"))
        self.assertEqual(ws.delete_mode(modes, "Email"), [])

    def test_errors_keep_the_modes(self):
        self.assertEqual(ws.save_mode([], "Email", "Email", " ")[2], "A mode needs a name and instructions.")
        modes, _ = ws.add_mode([])
        self.assertEqual(ws.save_mode(modes, "New mode", "Message", "x")[2], "There is already a mode called Message.")

    def test_the_default_falls_back_when_its_mode_is_gone(self):
        self.assertEqual(ws.default_mode("Slack", []), "Fix errors")
        self.assertEqual(ws.default_mode("Slack", [{"name": "Slack", "instructions": "x"}]), "Slack")
        self.assertEqual(ws.default_mode("Deleted", [], "Clean dictation"), "Clean dictation")


class RoundTripTest(unittest.TestCase):
    def test_every_writer_key_survives_save_and_load(self):
        s = dict(settings.DEFAULTS)
        s["writer_sources"] = ws.toggle(ws.move(s["writer_sources"], "Local", -2), "Cursor", False)
        s["stt_sources"] = ws.move(s["stt_sources"], "Cursor", -1)
        s["writer_models"] = ws.set_model({}, "Codex", "gpt-5", "medium", MODELS)
        s["modes"], s["default_mode"], _ = ws.save_mode(ws.add_mode([])[0], "New mode", "Slack", "Casual.")
        s["dictate_mode"] = "Slack"
        s["default_mode"] = "Email"
        s["rewrite_hotkey"], s["dictate_hotkey"] = "<cmd>+<shift>+r", "<ctrl>+<alt>+<f5>"
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "settings.json")
            settings.save(s, path)
            loaded = settings.load(path)
            self.assertEqual(json.loads(Path(path).read_text(encoding="utf-8"))["modes"], s["modes"])
        for key in ("writer_sources", "stt_sources", "writer_models", "modes", "default_mode", "dictate_mode",
                    "rewrite_hotkey", "dictate_hotkey"):
            self.assertEqual(loaded[key], s[key], key)

    def test_missing_or_invalid_dictation_mode_gets_its_own_default(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            for value in (None, [], 3, "", "  "):
                path.write_text(json.dumps({"dictate_mode": value, "default_mode": "Email"}), encoding="utf-8")
                self.assertEqual(settings.load(str(path))["dictate_mode"], "Clean dictation")
            path.write_text('{"default_mode": "Email", "dictate_polish": false}', encoding="utf-8")
            loaded = settings.load(str(path))
            self.assertEqual(loaded["dictate_mode"], "Clean dictation")
            self.assertEqual(loaded["default_mode"], "Email")
            self.assertFalse(loaded["dictate_polish"])


if __name__ == "__main__":
    unittest.main()
