"""Settings → Writing and Dictation on macOS, shown where the host can edit text."""
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

if sys.platform != "darwin":
    raise unittest.SkipTest("macOS only")


class WritingSettingsTest(unittest.TestCase):
    def setUp(self):
        import AppKit
        from harness_discussion import settings
        from harness_discussion.mac import settings_ui, stt_page, ui
        AppKit.NSApplication.sharedApplication()
        self.changes, self.fixed = [], []
        engines = [SimpleNamespace(name="Cursor"), SimpleNamespace(name="Codex"), SimpleNamespace(name="macOS")]
        self.app = SimpleNamespace(
            cfg=dict(settings.DEFAULTS), statuses={}, writer_ready=True, chain=SimpleNamespace(engines=engines),
            stt_error=None, check_update=lambda: None, update_state=lambda: ("", False),
            available_version=lambda: None, summarize_state=lambda: ("ready", None), summarize_action=lambda: None,
            change=lambda key, value, save=True: self.changes.append((key, value)), play=lambda *a, **k: None,
            writer_statuses={"Cursor": "ready", "Codex": "signed_out", "Local": "missing"},
            stt_statuses={"Codex": "ready", "Cursor": "ready"},
            writer_models={"Cursor": [{"id": "fast", "label": "Fast", "efforts": []}],
                           "Codex": [{"id": "gpt-5", "efforts": ["low", "medium"]}]},
            fix_writer=lambda name, stt=False: self.fixed.append((name, stt)), apply=lambda key, value: None,
            host=mock.Mock(mic_status=lambda: "not_asked"))
        for p in (mock.patch.object(ui, "show"), mock.patch.object(settings_ui.startup, "enabled", return_value=False),
                  mock.patch.object(stt_page, "stt_connection", return_value=("", ""))):
            p.start()
            self.addCleanup(p.stop)
        self.win = settings_ui.SettingsWindow(self.app, lambda: None)
        self.addCleanup(self.win.win.close)

    def click(self, control):
        control.sendAction_to_(control.action(), control.target())

    def test_writing_and_dictation_follow_summarize(self):
        self.assertEqual([t for t, _, _ in self.win.page_list][3:6], ["Summarize", "Writing", "Dictation"])
        self.assertEqual(len(self.win.pages), 8)
        self.win.nav[7].performClick_(None)
        self.assertEqual([p.isHidden() for p in self.win.pages], [True] * 7 + [False])

    def test_writer_rows_show_status_fix_and_order(self):
        rows = self.win.writer_rows
        self.assertEqual([rows[n][2].stringValue() for n in ("Cursor", "Codex", "Local")],
                         ["Ready", "Not signed in", "Not downloaded"])
        self.assertEqual((rows["Codex"][3].title(), rows["Local"][3].title()), ("Sign in…", "Download…"))
        self.assertTrue(rows["Cursor"][3].isHidden())
        rows["Local"][3].performClick_(None)
        self.assertEqual(self.fixed, [("Local", False)])
        rows["Local"][6][0].performClick_(None)
        self.assertEqual(self.changes[-1], ("writer_sources", [["Cursor", True], ["Local", True], ["Codex", True]]))
        self.assertEqual(self.win.writer_list.arrangedSubviews()[1], rows["Local"][7])
        self.assertEqual(self.win.mic_status.stringValue(), "Asks the first time you dictate")

    def test_dictation_has_the_polish_switch(self):
        self.assertEqual(self.win.polish_switch.accessibilityLabel(), "Tidy dictation after transcribing")

    def test_check_as_i_type_asks_first(self):
        switch = self.win.live_switch
        self.assertEqual(switch.accessibilityLabel(), "Check as I type")
        self.app.host.confirm = mock.Mock(return_value=False)
        switch.setState_(1)
        self.click(switch)
        self.assertEqual(self.app.host.confirm.call_args.kwargs, {"ok": "Turn On"})
        self.assertEqual(switch.state(), 0)
        self.assertNotIn(("live_check", True), self.changes)
        self.app.host.confirm.return_value = True
        switch.setState_(1)
        self.click(switch)
        self.assertIn(("live_check", True), self.changes)
        self.click(self.win.polish_switch)
        self.assertEqual(self.changes[-1][0], "dictate_polish")

    def test_models_and_efforts_save_per_source(self):
        _, _, _, _, model, effort, _, _ = self.win.writer_rows["Codex"]
        self.assertTrue(effort.isHidden())
        model.selectItemAtIndex_(1)
        self.click(model)
        self.assertEqual(self.changes[-1], ("writer_models", {"Codex": {"model": "gpt-5"}}))
        self.assertFalse(effort.isHidden())
        effort.selectItemAtIndex_(2)
        self.click(effort)
        self.assertEqual(self.changes[-1], ("writer_models", {"Codex": {"model": "gpt-5", "effort": "medium"}}))
        local = self.win.writer_rows["Local"][4]
        self.assertEqual((local.titleOfSelectedItem(), local.isEnabled()), ("Qwen3-4B", False))

    def test_add_rename_and_delete_a_mode_keeps_the_default_valid(self):
        w = self.win
        w._add_mode()
        self.assertEqual(w.mode_popup.titleOfSelectedItem(), "New mode")
        w.mode_name.setStringValue_("Slack")
        w.mode_text.setStringValue_("Short and casual.")
        w._save_mode()
        self.assertEqual(self.changes[-1], ("modes", [{"name": "Slack", "instructions": "Short and casual."}]))
        w.default_popup.selectItemWithTitle_("Slack")
        self.click(w.default_popup)
        self.assertEqual(self.changes[-1], ("default_mode", "Slack"))
        w._delete_mode()
        self.assertEqual(self.changes[-2:], [("modes", []), ("default_mode", "Fix errors")])

    def test_dictation_mode_is_independent_and_tracks_custom_mode_rename_and_delete(self):
        w = self.win
        self.assertEqual(w.dictate_popup.titleOfSelectedItem(), "Clean dictation")
        w._add_mode()
        w.mode_name.setStringValue_("Spoken steps")
        w.mode_text.setStringValue_("Make a numbered list.")
        w._save_mode()
        w.dictate_popup.selectItemWithTitle_("Spoken steps")
        self.click(w.dictate_popup)
        self.assertEqual(self.changes[-1], ("dictate_mode", "Spoken steps"))
        self.assertEqual(w.default_popup.titleOfSelectedItem(), "Fix errors")
        w.mode_name.setStringValue_("My steps")
        w._save_mode()
        self.assertEqual(w.dictate_popup.titleOfSelectedItem(), "My steps")
        self.assertEqual(w.cfg["dictate_mode"], "My steps")
        w._delete_mode()
        self.assertEqual(w.dictate_popup.titleOfSelectedItem(), "Clean dictation")
        self.assertEqual(w.cfg["dictate_mode"], "Clean dictation")
        w._polish_changed(False)
        self.assertFalse(w.dictate_popup.isEnabled())
        w._polish_changed(True)
        self.assertTrue(w.dictate_popup.isEnabled())

    def test_a_status_refresh_keeps_an_unsaved_mode_draft(self):
        w = self.win
        w.mode_text.setStringValue_("Draft")
        w.refresh_writing()
        self.assertEqual(w.mode_text.stringValue(), "Draft")

    def test_recording_shows_held_modifiers_and_takes_any_key(self):
        import AppKit
        from harness_discussion.mac import reading_page
        handlers = []

        class Fake:  # AppKit, with the key monitor handed to the test instead of the system
            NSEvent = SimpleNamespace(  # a real token, so Esc's _stop_recording sees a recorder to remove
                addLocalMonitorForEventsMatchingMask_handler_=lambda m, h: (handlers.append(h), object())[1],
                removeMonitor_=lambda m: None)
            NSNotificationCenter = SimpleNamespace(defaultCenter=mock.Mock)

            def __getattr__(self, name):
                return getattr(AppKit, name)

        def event(kind, flags, code=0):
            if kind == AppKit.NSEventTypeFlagsChanged:
                return AppKit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                    kind, (0, 0), flags, 0, 0, None, "", "", False, 59)
            return AppKit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                kind, (0, 0), flags, 0, 0, None, "e", "e", False, code)
        w, label = self.win, self.win.shortcut_labels["rewrite_hotkey"]
        real, w.win = w.win, mock.Mock(isVisible=lambda: True)
        try:
            with mock.patch.object(reading_page, "AppKit", Fake()):
                w._record_hotkey("rewrite_hotkey")
                ctrl, alt = AppKit.NSEventModifierFlagControl, AppKit.NSEventModifierFlagOption
                handlers[0](event(AppKit.NSEventTypeFlagsChanged, ctrl | alt))
                self.assertEqual(label.text, "⌃⌥…")
                handlers[0](event(AppKit.NSEventTypeKeyDown, ctrl | alt, 14))
                self.assertEqual(self.changes[-1], ("rewrite_hotkey", "<ctrl>+<alt>+e"))
                self.assertEqual(label.text, "⌃⌥E")
                w._record_hotkey("rewrite_hotkey")
                handlers[1](event(AppKit.NSEventTypeKeyDown, 0, 14))  # a key alone is a shortcut too
                self.assertEqual(self.changes[-1], ("rewrite_hotkey", "e"))
                self.assertEqual(label.text, "E")
                w._record_hotkey("rewrite_hotkey")
                saved = len(self.changes)
                handlers[2](event(AppKit.NSEventTypeKeyDown, AppKit.NSEventModifierFlagCommand, 53))  # Esc cancels
        finally:
            w.win = real
        self.assertEqual(len(self.changes), saved)
        self.assertEqual(label.text, "E")

    def test_a_shortcut_another_row_uses_is_refused(self):
        self.win._set_hotkey(self.win.cfg["hotkey"], "rewrite_hotkey")
        self.assertEqual(self.win.shortcut_labels["rewrite_hotkey"].text, "Used by Read shortcut")
        self.assertNotIn("rewrite_hotkey", [k for k, _ in self.changes])
        self.win._set_hotkey("<cmd>+<shift>+r", "rewrite_hotkey")
        self.assertEqual(self.changes[-1], ("rewrite_hotkey", "<cmd>+<shift>+r"))

    def test_warns_only_for_the_keys_macos_switches_windows_with_first(self):
        from harness_discussion.mac import window_keys
        hints = self.win.shortcut_hints
        switching = {(frozenset({"cmd"}), "key_50"), (frozenset({"cmd", "shift"}), "key_50")}
        with mock.patch.object(window_keys, "window_switch_keys", return_value=switching):
            self.win._set_hotkey("<cmd>+<key_50>")
            self.assertFalse(hints["hotkey"].isHidden())
            self.assertIn("Move focus to next window", hints["hotkey"].stringValue())
            self.win._set_hotkey("<cmd>+<alt>+r")
            self.assertTrue(hints["hotkey"].isHidden())
            self.win._set_hotkey("<shift>+<cmd>+<key_50>", "rewrite_hotkey")
            self.assertFalse(hints["rewrite_hotkey"].isHidden())
            self.assertTrue(hints["hotkey"].isHidden())
        with mock.patch.object(window_keys, "window_switch_keys", return_value=set()):
            self.win._set_hotkey("<cmd>+<key_50>")
            self.assertTrue(hints["hotkey"].isHidden())

    def test_recent_dictations_copy_the_tidied_text_and_the_original(self):
        from harness_discussion import history
        with mock.patch.object(history, "rows", return_value=[("Tidy.", "Tidy.", "words")]):
            self.win.refresh_history()
        row = self.win.history_list.arrangedSubviews()[0]
        for b in row.arrangedSubviews()[2:]:
            self.click(b)
        self.assertEqual([c.args[0] for c in self.app.host.copy_text.call_args_list], ["Tidy.", "words"])

    def test_rebuilding_recent_dictations_keeps_no_more_callbacks(self):
        from harness_discussion import history
        from harness_discussion.mac import ui
        with mock.patch.object(history, "rows", return_value=[("Tidy.", "Tidy.", "words")]):
            self.win.refresh_history()
            kept = len(ui._keep)
            for _ in range(5):
                self.win.refresh_history()
        self.assertEqual(len(ui._keep), kept)  # no transcript stays referenced after its row is gone


if __name__ == "__main__":
    unittest.main()
