import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

import gc
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from harness_discussion import settings
from harness_discussion.win import settings_ui
from harness_discussion.win.trigger import key_name

CATALOGS = {"Codex": [{"id": "gpt-5.5", "label": "GPT-5.5", "efforts": ["low", "medium", "high"],
                       "default_effort": "medium"},
                      {"id": "gpt-5.5-mini", "label": "GPT-5.5 mini", "efforts": [], "default_effort": None}]}


class WritingSettingsUi(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.changes, self.window = [], None

    def tearDown(self):
        if self.window and self.window.alive:
            self.window.close()
        self.root.destroy()
        self.window = self.root = None
        gc.collect()  # Tk cycles must release their interpreter on its owning thread.

    def make_window(self, cfg=None, **kw):
        cfg = dict(settings.DEFAULTS) if cfg is None else cfg
        with patch.object(settings_ui.settings, "startup_enabled", return_value=False), \
                patch.object(settings_ui.SettingsWindow, "focus", lambda self: None), \
                patch.object(settings_ui, "stt_connection", return_value=("", "")):
            self.window = settings_ui.SettingsWindow(
                self.root, cfg, Image.new("RGBA", (32, 32)), lambda *a: self.changes.append(a),
                lambda _text: None, **kw)
        return self.window

    def test_writing_page_sits_before_general_and_toggles_and_orders_sources(self):
        w = self.make_window()
        self.assertEqual(settings_ui.PAGES[-2:], ("Writing", "General"))
        w.nav[settings_ui.PAGES.index("Writing")].invoke()
        self.assertEqual(w.title["text"], "Writing")
        w.writer_check.invoke()
        self.assertEqual(self.changes[-1], ("writer", not settings.DEFAULTS["writer"]))
        w.writer_rows["Codex"]["toggle"].invoke()
        self.assertEqual(self.changes[-1], ("writer_sources", [["Cursor", True], ["Codex", False], ["Local", True]]))
        self.assertFalse(w.writer_rows["Codex"]["on"].get())
        w.writer_rows["Local"]["up"].invoke()
        self.assertEqual(self.changes[-1], ("writer_sources", [["Cursor", True], ["Local", True], ["Codex", False]]))
        self.assertTrue(w.writer_rows["Cursor"]["up"].instate(["disabled"]))

    def test_unsafe_reading_fallback_is_disabled_without_mutating_saved_preference(self):
        w = self.make_window({**settings.DEFAULTS, "clipboard_fallback": True})
        self.assertTrue(w.clip_check.instate(["disabled"]))
        self.assertFalse(w.clip.get())
        self.assertIn("protect your clipboard", w.clip_note["text"])
        w.clip_check.invoke()
        self.assertTrue(w.cfg["clipboard_fallback"])
        self.assertEqual(self.changes, [])

    def test_statuses_come_from_the_supplier_and_the_fix_button_calls_back(self):
        statuses, fixes = {}, []
        w = self.make_window(writer_statuses=lambda: dict(statuses), on_writer_fix=fixes.append)
        status = lambda name: w.writer_rows[name]["status"]["text"]
        self.assertEqual([status(n) for n in ("Cursor", "Codex", "Local")], ["Not checked"] * 3)
        self.assertFalse(w.writer_rows["Codex"]["fix"].winfo_manager())
        statuses.update(Codex="signed_out", Cursor="ready", Local="not_downloaded")
        w.refresh_writing()
        self.assertEqual([status(n) for n in ("Cursor", "Codex", "Local")], ["Ready", "Not signed in", "Not downloaded"])
        self.assertEqual(w.writer_rows["Codex"]["fix"]["text"], "Sign in…")
        self.assertEqual(w.writer_rows["Local"]["fix"]["text"], "Download…")
        self.assertFalse(w.writer_rows["Cursor"]["fix"].winfo_manager())
        w.writer_rows["Codex"]["fix"].invoke()
        self.assertEqual(fixes, ["Codex"])

    def pick(self, box, label):
        box.current(list(box["values"]).index(label))
        box.event_generate("<<ComboboxSelected>>")

    def test_model_and_effort_come_from_the_cached_catalog_and_save(self):
        catalogs = {}
        w = self.make_window(writer_catalogs=lambda: catalogs)
        model, effort = w.writer_rows["Codex"]["model"], w.writer_rows["Codex"]["effort"]
        self.assertEqual(model.get(), "Models not loaded")
        self.assertTrue(model.instate(["disabled"]))
        self.assertEqual(w.writer_rows["Local"]["model"].get(), "Qwen3-4B")
        self.assertTrue(w.writer_rows["Local"]["model"].instate(["disabled"]))
        catalogs.update(CATALOGS)
        w.refresh_writing()
        self.assertEqual(list(model["values"]), ["Default", "GPT-5.5", "GPT-5.5 mini"])
        self.assertEqual(model.get(), "Default")
        self.pick(model, "GPT-5.5")
        self.assertEqual(self.changes[-1], ("writer_models", {"Codex": {"model": "gpt-5.5", "effort": "medium"}}))
        self.assertEqual(list(effort["values"]), ["low", "medium", "high"])
        self.pick(effort, "high")
        self.assertEqual(self.changes[-1], ("writer_models", {"Codex": {"model": "gpt-5.5", "effort": "high"}}))
        self.pick(model, "GPT-5.5 mini")
        self.assertEqual(self.changes[-1], ("writer_models", {"Codex": {"model": "gpt-5.5-mini", "effort": None}}))
        self.assertTrue(effort.instate(["disabled"]))
        self.pick(model, "Default")
        self.assertEqual(self.changes[-1], ("writer_models", {}))

    def test_a_saved_model_shows_selected(self):
        cfg = {**settings.DEFAULTS, "writer_models": {"Codex": {"model": "gpt-5.5", "effort": "low"}}}
        w = self.make_window(cfg, writer_catalogs=lambda: CATALOGS)
        self.assertEqual((w.writer_rows["Codex"]["model"].get(), w.writer_rows["Codex"]["effort"].get()),
                         ("GPT-5.5", "low"))

    def test_published_catalog_without_labels_can_be_selected_and_saved(self):
        catalogs = {"Codex": [{"id": "gpt-6.1-sol", "efforts": ["low", "high"]},
                              {"id": "gpt-6-luna", "label": "", "efforts": []}]}
        w = self.make_window(writer_catalogs=lambda: catalogs)
        model = w.writer_rows["Codex"]["model"]
        self.assertEqual(list(model["values"]), ["Default", "gpt-6.1-sol", "gpt-6-luna"])
        self.pick(model, "gpt-6.1-sol")
        self.assertEqual(self.changes[-1],
                         ("writer_models", {"Codex": {"model": "gpt-6.1-sol", "effort": None}}))
        self.pick(w.writer_rows["Codex"]["effort"], "high")
        self.assertEqual(self.changes[-1],
                         ("writer_models", {"Codex": {"model": "gpt-6.1-sol", "effort": "high"}}))

    def test_model_choice_uses_the_catalog_shown_even_if_supplier_changes(self):
        catalogs = {"Codex": list(CATALOGS["Codex"])}
        w = self.make_window(writer_catalogs=lambda: catalogs)
        model = w.writer_rows["Codex"]["model"]
        catalogs["Codex"] = list(reversed(catalogs["Codex"]))
        self.pick(model, "GPT-5.5")
        self.assertEqual(self.changes[-1], ("writer_models", {"Codex": {"model": "gpt-5.5", "effort": "medium"}}))
        catalogs["Codex"] = []
        self.pick(model, "GPT-5.5 mini")
        self.assertEqual(self.changes[-1], ("writer_models", {"Codex": {"model": "gpt-5.5-mini", "effort": None}}))

    def edit_mode(self, w, name, instructions):
        w.mode_name.delete(0, "end")
        w.mode_name.insert(0, name)
        w.mode_text.delete("1.0", "end")
        w.mode_text.insert("1.0", instructions)
        w.mode_save.invoke()

    def test_custom_modes_add_rename_override_and_delete(self):
        w = self.make_window()
        self.assertEqual(list(w.default_mode["values"]),
                         ["Fix errors", "Message", "Email", "Improve writing", "Clean dictation"])
        self.pick(w.default_mode, "Email")
        self.assertEqual(self.changes[-1], ("default_mode", "Email"))
        self.pick(w.mode_pick, "Fix errors")
        self.assertTrue(w.mode_delete.instate(["disabled"]))  # built-ins can't be deleted
        self.assertIn("spelling", w.mode_text.get("1.0", "end"))

        w.mode_new.invoke()
        self.edit_mode(w, "  ", "Anything")
        self.assertEqual(w.mode_error["text"], "Give the mode a name.")
        self.edit_mode(w, "Tweet", "Under 280 characters.")
        self.assertEqual(self.changes[-1], ("modes", [{"name": "Tweet", "instructions": "Under 280 characters."}]))
        self.assertEqual(w.mode_error["text"], "")
        self.assertIn("Tweet", w.default_mode["values"])
        self.pick(w.default_mode, "Tweet")

        self.edit_mode(w, "Post", "Under 280 characters.")  # rename keeps the default pointing at it
        self.assertEqual(self.changes[-2], ("modes", [{"name": "Post", "instructions": "Under 280 characters."}]))
        self.assertEqual(self.changes[-1], ("default_mode", "Post"))

        self.pick(w.mode_pick, "Email")
        self.edit_mode(w, "Email", "A formal email.")  # a custom mode with a built-in's name overrides it
        self.assertEqual(self.changes[-1][1][-1], {"name": "Email", "instructions": "A formal email."})
        self.assertEqual(list(w.default_mode["values"]).count("Email"), 1)
        self.assertFalse(w.mode_delete.instate(["disabled"]))

        w.mode_new.invoke()
        self.edit_mode(w, "Post", "Again")
        self.assertEqual(w.mode_error["text"], "A mode named Post already exists.")

        self.pick(w.mode_pick, "Post")
        w.mode_delete.invoke()
        self.assertEqual(self.changes[-2], ("modes", [{"name": "Email", "instructions": "A formal email."}]))
        self.assertEqual(self.changes[-1], ("default_mode", "Fix errors"))
        self.pick(w.mode_pick, "Email")
        w.mode_delete.invoke()  # the built-in comes back
        self.assertEqual(self.changes[-1], ("modes", []))
        self.assertIn("greeting", w.mode_text.get("1.0", "end"))

    def press(self, w, keycode, state):
        w.win.event_generate("<KeyPress>", keycode=keycode, state=state, when="now")

    def test_read_shortcut_still_records_and_reports_recording(self):
        recording = []
        w = self.make_window(on_record=recording.append)
        w.nav[settings_ui.PAGES.index("Reading")].invoke()
        self.assertEqual(w.hotkey_lbl["text"], "Ctrl+Alt+R")
        w._record_hotkey()
        self.assertEqual(w.hotkey_lbl["text"], "Press keys…")
        self.press(w, ord("T"), 0x20004)  # Ctrl+Alt
        self.assertEqual(self.changes[-1], ("hotkey", "<ctrl>+<alt>+t"))
        self.assertEqual(w.hotkey_lbl["text"], "Ctrl+Alt+T")
        self.assertEqual(recording, [True, False])

    def test_any_key_records_alone_or_with_modifiers(self):
        recording = []
        w = self.make_window(on_record=recording.append)
        w._record_hotkey()
        self.press(w, 0x11, 0)  # Ctrl alone waits for the key
        self.assertEqual(w.hotkey_lbl["text"], "Press keys…")
        self.press(w, 0xC0, 0x4)  # Ctrl+`
        self.assertEqual(self.changes[-1], ("hotkey", "<ctrl>+<key_192>"))
        self.assertEqual(w.hotkey_lbl["text"], "Ctrl+" + key_name(0xC0))
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
        self.assertEqual(w.hotkey_lbl["text"], "Win+F5")

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
        self.assertEqual((rewrite["label"]["text"], dictate["label"]["text"]), ("Ctrl+Shift+E", "Ctrl+Shift+D"))
        rewrite["change"].invoke()
        self.press(w, ord("R"), 0x20004)  # the read shortcut's chord
        self.assertEqual(rewrite["label"]["text"], "Ctrl+Shift+E")
        self.assertEqual(rewrite["error"]["text"], "Read shortcut already uses Ctrl+Alt+R.")
        self.assertEqual(self.changes, [])
        rewrite["change"].invoke()
        self.assertEqual(rewrite["error"]["text"], "")
        self.press(w, ord("W"), 0x20005)  # Ctrl+Alt+Shift
        self.assertEqual(self.changes[-1], ("rewrite_hotkey", "<ctrl>+<alt>+<shift>+w"))
        w.shortcuts["hotkey"]["change"].invoke()
        dictate["off"].invoke()
        self.assertEqual(w.hotkey_lbl["text"], "Ctrl+Alt+R")
        self.assertEqual(self.changes[-1], ("dictate_hotkey", ""))
        self.assertEqual(dictate["label"]["text"], "Off")
        w.shortcuts["hotkey"]["change"].invoke()
        self.press(w, 0x1B, 0)  # Esc keeps the old one
        self.assertEqual(w.hotkey_lbl["text"], "Ctrl+Alt+R")
        self.assertEqual(recording, [True, False, True, False, True, False, True, False])  # Off resumes too, as before

    def test_dictation_orders_its_sources_and_shows_the_microphone(self):
        mic = ["Not checked"]
        w = self.make_window(dictate_status=lambda: mic[0])
        self.assertEqual(w.mic_status["text"], "Microphone: Not checked")
        self.assertTrue(w.dictation_rows["Cursor"]["up"].instate(["disabled"]))  # Cursor transcribes first
        w.dictation_rows["Codex"]["up"].invoke()
        self.assertEqual(self.changes[-1], ("stt_sources", [["Codex", True], ["Cursor", True]]))
        w.dictation_rows["Cursor"]["toggle"].invoke()
        self.assertEqual(self.changes[-1], ("stt_sources", [["Codex", True], ["Cursor", False]]))
        self.assertTrue(w.polish_on.get())
        w.polish_check.invoke()
        self.assertEqual(self.changes[-1], ("dictate_polish", False))
        mic[0] = "Allowed"
        w.refresh_writing()
        self.assertEqual(w.mic_status["text"], "Microphone: Allowed")

    def test_dictation_mode_is_independent_and_tracks_custom_mode_rename_and_delete(self):
        w = self.make_window()
        self.assertEqual(w.dictate_mode.get(), "Clean dictation")
        w.mode_new.invoke()
        self.edit_mode(w, "Spoken steps", "Make a numbered list.")
        self.pick(w.dictate_mode, "Spoken steps")
        self.assertEqual(self.changes[-1], ("dictate_mode", "Spoken steps"))
        self.assertEqual(w.default_mode.get(), "Fix errors")
        self.edit_mode(w, "My steps", "Make a numbered list.")
        self.assertEqual(w.dictate_mode.get(), "My steps")
        self.assertEqual(w.cfg["dictate_mode"], "My steps")
        w.mode_delete.invoke()
        self.assertEqual(w.dictate_mode.get(), "Clean dictation")
        self.assertEqual(w.cfg["dictate_mode"], "Clean dictation")
        w.polish_check.invoke()
        self.assertTrue(w.dictate_mode.instate(["disabled"]))
        w.polish_check.invoke()
        self.assertFalse(w.dictate_mode.instate(["disabled"]))


if __name__ == "__main__":
    unittest.main()
