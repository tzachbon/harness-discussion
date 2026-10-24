"""Windows host contracts with mocked hooks, editors, clipboard and providers."""
import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

from copy import deepcopy
from contextlib import contextmanager
import ctypes
import queue
from unittest.mock import Mock, call, patch

from harness_discussion import settings
from harness_discussion.win import edit, host


@contextmanager
def clipboard_api(previous_format=14):
    """Win32 boundary only: no desktop clipboard access, with real allocated test buffers."""
    native, kernel, buffers = Mock(), Mock(), []
    native.CreateWindowExW.return_value = 123
    native.OpenClipboard.return_value = native.EmptyClipboard.return_value = 1
    native.EnumClipboardFormats.side_effect = [previous_format, 0]
    native.GetClipboardData.side_effect = AssertionError("Explicit Copy must not read prior data")
    native.SetClipboardData.side_effect = lambda fmt, address: address
    native.IsClipboardFormatAvailable.return_value = False

    def allocate(flags, size):
        data = ctypes.create_string_buffer(size)
        buffers.append(data)
        return ctypes.addressof(data)

    kernel.GlobalAlloc.side_effect = allocate
    kernel.GlobalLock.side_effect = lambda address: address
    with patch.object(host.clipboard, "u32", native), patch.object(host.clipboard, "k32", kernel):
        yield native, kernel


class HostWriter(unittest.TestCase):
    def test_dictation_target_is_native_identity_only_and_fails_closed(self):
        with patch.object(host.edit, "_foreground", return_value=(123, 456)) as foreground, \
                patch.object(host.edit, "capture") as capture, patch.object(host.os, "getpid", return_value=999):
            self.assertEqual(self.host.dictation_target(), (123, 456))
            foreground.assert_called_once_with()
            capture.assert_not_called()
            for identity in (None, (0, 456), (123, 0), (999, 456)):
                foreground.return_value = identity
                self.assertIsNone(self.host.dictation_target())
            foreground.side_effect = OSError("unavailable")
            self.assertIsNone(self.host.dictation_target())

    def test_production_host_exposes_the_guarded_editor_and_microphone_actions(self):
        self.assertIs(host.TkHost.edit, edit)
        self.assertEqual(edit.NEWLINE, "\r\n")
        for state, expected in (("Available", True), ("Blocked in Windows privacy settings", False),
                                ("No microphone available", False)):
            answers = []
            with patch.object(host.microphone, "status", return_value=state):
                self.host.mic_permission(answers.append)
            self.assertEqual(answers, [expected])
        with patch.object(host.os, "startfile") as open_settings:
            self.host.open_mic_settings()
        open_settings.assert_called_once_with("ms-settings:privacy-microphone")

    def test_microphone_settings_launch_failure_notifies_without_raising(self):
        with patch.object(host.os, "startfile", side_effect=OSError("private policy details")):
            self.host.open_mic_settings()
        self.host.icon.notify.assert_called_once_with(
            "Could not open Windows microphone settings.", "Harness Discussion")

    def setUp(self):
        with patch.object(host.tk, "Tk"), patch.object(host.pystray, "Icon"), \
                patch.object(host, "icon_image"):
            self.host = host.TkHost()
        self.app = self.host.app = Mock(cfg=deepcopy(settings.DEFAULTS), events=queue.Queue())
        self.app.chain.engines = []
        self.app.statuses = {}
        self.app.rewriting = False
        self.app.writer_models = {"Codex": [{"id": "example", "efforts": []}]}
        self.app.writer_statuses = {"Codex": "ready"}
        self.app.stt_statuses = {"Codex": "signed_out"}

    def test_recording_pauses_and_restores_all_shortcuts_without_saving(self):
        saved = deepcopy(self.app.cfg)
        with patch.object(host, "SettingsWindow") as window:
            self.host.open_settings()
            record = window.call_args.kwargs["on_record"]
            record(True)
            record(False)
        self.assertEqual(self.app.apply.call_args_list,
                         [call(key, "") for key in ("hotkey", "rewrite_hotkey", "dictate_hotkey")]
                         + [call(key, saved[key]) for key in ("hotkey", "rewrite_hotkey", "dictate_hotkey")])
        self.assertEqual(self.app.cfg, saved)

    def test_settings_reads_current_cached_writer_data_and_routes_fixes(self):
        with patch.object(host, "SettingsWindow") as window:
            self.host.open_settings()
            callbacks = window.call_args.kwargs
        self.assertEqual(callbacks["writer_catalogs"](), self.app.writer_models)
        self.assertEqual(callbacks["stt_statuses"](), self.app.stt_statuses)
        self.app.writer_statuses = {"Codex": "signed_out"}
        self.assertEqual(callbacks["writer_statuses"](), {"Codex": "signed_out"})
        callbacks["on_writer_fix"]("Codex")
        self.app.fix_writer.assert_called_once_with("Codex")
        callbacks["on_writer_fix"]("Cursor", True)
        self.app.fix_writer.assert_called_with("Cursor", True)
        self.app.refresh_writer.assert_not_called()

    def test_recording_cancels_writer_before_disabling_its_hold_listener(self):
        actions = []
        self.app.apply.side_effect = lambda key, value: actions.append(key)
        with patch.object(host, "Escape"):
            self.host.esc_watcher(lambda: actions.append("cancel"))
        with patch.object(host, "SettingsWindow") as window:
            self.host.open_settings()
            window.call_args.kwargs["on_record"](True)
        self.assertEqual(actions, ["cancel", "hotkey", "rewrite_hotkey", "dictate_hotkey"])

    def test_named_hold_shortcut_dispatches_and_replacement_stops_its_old_listener(self):
        actions = []
        with patch.object(host, "HotKey") as hook:
            self.host.set_shortcut("dictate", "<ctrl>+<shift>+d", lambda: actions.append("down"),
                                   lambda: actions.append("up"))
            hook.call_args.args[1]()
            hook.call_args.args[2]()
            self.assertEqual(actions, ["down", "up"])
            self.host.set_shortcut("dictate", "", None)
            hook.return_value.stop.assert_called_once_with()
            self.assertEqual(hook.call_count, 1)

    def test_shortcut_activity_is_forwarded_only_when_provided(self):
        down, up, activity = Mock(), Mock(), Mock()
        with patch.object(host, "HotKey") as hook:
            self.host.set_shortcut("dictate", "<ctrl>+r", down, up, on_activity=activity)
            hook.assert_called_once_with("<ctrl>+r", down, up, on_activity=activity)
            self.host.set_shortcut("rewrite", "<ctrl>+e", down)
            self.assertEqual(hook.call_args, call("<ctrl>+e", down, None))

    def test_popup_escape_is_enqueued_before_the_main_thread_callback_runs(self):
        escaped = []
        with patch.object(host, "Popup") as popup:
            self.host.make_popup(1.0, on_escape=lambda: escaped.append(True))
            popup.call_args.kwargs["on_escape"]()
        self.assertEqual(escaped, [])
        kind, callback = self.app.events.get_nowait()
        self.assertEqual(kind, "call")
        callback()
        self.assertEqual(escaped, [True])
    def test_copy_preserves_unicode_and_whitespace_and_reports_failure_without_retry(self):
        with patch.object(host, "clipboard", create=True) as clipboard:
            self.assertTrue(self.host.copy_text(" A😀\r\nB "))
            clipboard.write_text.assert_called_once_with(" A😀\r\nB ")
            clipboard.write_text.reset_mock()
            clipboard.write_text.side_effect = OSError("clipboard unavailable")
            self.assertFalse(self.host.copy_text("private result"))
            clipboard.write_text.assert_called_once()
        notification = self.host.icon.notify.call_args.args[0]
        self.assertNotIn("private result", notification)

    def test_explicit_copy_overwrites_unsupported_formats_without_reading_them(self):
        for previous, text in ((14, " A😀\r\nB "), (2, " A😀\r\nB "), (49353, " A😀\r\nB "), (14, "")):
            with self.subTest(previous_format=previous, text=text), clipboard_api(previous) as (native, kernel):
                expected = (text + "\0").encode("utf-16-le")
                self.assertTrue(self.host.copy_text(text))
                native.SetClipboardData.assert_called_once()
                fmt, address = native.SetClipboardData.call_args.args
                self.assertEqual(fmt, 13)
                self.assertEqual(ctypes.string_at(address, len(expected)), expected)
                native.EnumClipboardFormats.assert_not_called()
                native.GetClipboardData.assert_not_called()
                native.CloseClipboard.assert_called_once()
                kernel.GlobalFree.assert_not_called()

    def test_explicit_copy_failures_release_owned_memory_without_retrying_writes(self):
        for failure in ("allocate", "lock", "owner", "open", "empty", "set"):
            with self.subTest(failure=failure), clipboard_api() as (native, kernel), \
                    patch.object(host.clipboard.time, "sleep"):
                self.host.icon.notify.reset_mock()
                if failure == "allocate":
                    kernel.GlobalAlloc.side_effect = None
                    kernel.GlobalAlloc.return_value = 0
                elif failure == "lock":
                    kernel.GlobalLock.side_effect = None
                    kernel.GlobalLock.return_value = 0
                elif failure == "owner":
                    native.CreateWindowExW.return_value = 0
                elif failure == "open":
                    native.OpenClipboard.return_value = 0
                elif failure == "empty":
                    native.EmptyClipboard.return_value = 0
                else:
                    native.SetClipboardData.side_effect = None
                    native.SetClipboardData.return_value = 0
                self.assertFalse(self.host.copy_text("private result"))
                self.host.icon.notify.assert_called_once_with("Could not copy the result.", "Harness Discussion")
                native.EnumClipboardFormats.assert_not_called()
                native.GetClipboardData.assert_not_called()
                self.assertEqual(kernel.GlobalFree.call_count, int(failure != "allocate"))
                if failure in ("allocate", "lock"):
                    native.OpenClipboard.assert_not_called()
                if failure in ("empty", "set"):
                    native.EmptyClipboard.assert_called_once()
                    native.CloseClipboard.assert_called_once()
                else:
                    native.EmptyClipboard.assert_not_called()
                    native.CloseClipboard.assert_not_called()
                self.assertEqual(native.SetClipboardData.call_count, int(failure == "set"))

    def test_explicit_write_rejects_invalid_text_before_native_operations(self):
        for text in (None, 1, "first\0second", "\ud800"):
            with self.subTest(text=repr(text)), clipboard_api() as (native, kernel):
                with self.assertRaises(ValueError):
                    host.clipboard.write_text(text)
                kernel.GlobalAlloc.assert_not_called()
                native.OpenClipboard.assert_not_called()
                native.EmptyClipboard.assert_not_called()

    def test_copy_refuses_embedded_nul_before_touching_clipboard(self):
        with patch.object(host, "clipboard", create=True) as clipboard:
            self.assertFalse(self.host.copy_text("first\0second"))
            clipboard.write_text.assert_not_called()

    def test_mode_menu_callbacks_keep_their_own_names_and_current_selection(self):
        chosen = []
        with patch.object(host.tk, "Menu") as menu, patch.object(host.tk, "StringVar") as choice, \
                patch.object(self.host, "pointer", return_value=(120, 240)):
            self.host.pick_mode(["Fix errors", "Email"], "Email", chosen.append)
            self.assertEqual(choice.call_args.kwargs["value"], "Email")
            entries = menu.return_value.add_radiobutton.call_args_list
            self.assertEqual([entry.kwargs["label"] for entry in entries], ["Fix errors", "Email"])
            for entry in entries:
                entry.kwargs["command"]()
            menu.return_value.post.assert_called_once_with(120, 240)
        self.assertEqual(chosen, ["Fix errors", "Email"])

    def test_quit_stops_all_owned_hooks_before_destroying_tk(self):
        events = []
        with patch.object(host, "HotKey") as hook, patch.object(host, "Escape") as escape:
            self.host.set_hotkey("<ctrl>+<alt>+r", lambda: None)
            self.host.set_shortcut("rewrite", "<ctrl>+<shift>+e", lambda: None)
            self.host.set_shortcut("dictate", "<ctrl>+<shift>+d", lambda: None, lambda: None)
            self.host.esc_watcher(lambda: None)
            hook.return_value.stop.side_effect = lambda: events.append("hook")
            escape.return_value.stop.side_effect = lambda: events.append("escape")
            self.host.root.destroy.side_effect = lambda: events.append("destroy")
            with patch.object(host.uia_thread, "close"):
                self.host.quit()
        self.assertEqual(events, ["hook", "hook", "hook", "escape", "destroy"])

    def test_toast_escape_observes_and_rewrite_escape_suppresses_without_tk_access(self):
        escaped = []
        with patch.object(host, "Escape", create=True) as listener:
            watcher = self.host.esc_watcher(lambda: escaped.append(True))
            self.assertIs(watcher, listener.return_value)
            self.assertFalse(listener.call_args.kwargs["autostart"])
            suppress = listener.call_args.kwargs["suppress"]
            self.assertFalse(suppress())
            self.app.rewriting = True
            self.assertTrue(suppress())
            listener.call_args.args[0]()
        self.assertEqual(escaped, [])
        kind, callback = self.app.events.get_nowait()
        self.assertEqual(kind, "call")
        callback()
        self.assertEqual(escaped, [True])


class AppWriterTk(unittest.TestCase):
    def setUp(self):
        from contextlib import ExitStack
        from harness_discussion import app_summarize, core
        from harness_discussion.edit_logic import Context
        from tests.test_popup_settings import Speech

        self.boundaries = ExitStack()
        self.addCleanup(self.boundaries.close)
        cfg = deepcopy(settings.DEFAULTS)
        cfg.update(writer=True, stt_server=False)
        self.editor = Mock(NEWLINE="\r\n")
        self.editor.capture.return_value = Context("an original", 1, 2, True, False)
        self.editor.same_context.return_value = True
        self.editor.replace.return_value = True
        voice = Speech("Codex")
        for target, name, kwargs in (
                (settings, "PATH", {"new": __file__}),
                (settings, "load", {"return_value": cfg}),
                (host.TkHost, "edit", {"new": self.editor, "create": True}),
                (host.TkHost, "engines", {"return_value": [voice]}),
                (host.pystray, "Icon", {}),
                (host, "HotKey", {}), (host, "Escape", {}),
                (app_summarize.summarize, "Server", {}),
                (app_summarize.summarize, "status", {"return_value": "missing"})):
            self.boundaries.enter_context(patch.object(target, name, **kwargs))
        self.host = host.TkHost()
        self.app = core.App(self.host)
        self.app.writer_sources = [Mock(name="test-source")]
        self.app.writer_sources[0].name = "Codex"
        self.app.writer_sources[0].status.return_value = "ready"
        self.app.writer_sources[0].rewrite.return_value = "A rewrite."
        self.copied = self.boundaries.enter_context(patch.object(self.host, "copy_text"))
        self.addCleanup(self.cleanup_tk)

    def cleanup_tk(self):
        import gc
        self.app._close_writer()
        self.app.chain.close()
        self.host.root.destroy()
        self.host.app = None
        self.app = self.host = None
        gc.collect()

    def rewrite_from_bar(self, editable=True):
        self.app.popup.show("an original", 140, 140, editable=editable)
        self.host.root.update()
        if editable:
            self.app.popup.rewrite_btn.command()
        else:
            with patch.object(self.app.popup.overflow, "post"):
                self.app.popup.more_btn.command()
            self.app.popup.mode_menu.invoke("Fix errors")
        self.assertTrue(self.app.rewriting)
        self.assertTrue(self.app.toast.visible)
        self.app._handle(*self.app.events.get(timeout=5))
        self.host.root.update()

    def test_real_app_bar_and_toast_open_review_with_the_current_result(self):
        from tests.test_win_writer_ui import click
        self.rewrite_from_bar()
        self.editor.replace.assert_called_once_with("A rewrite.", self.editor.capture.return_value)
        self.assertFalse(self.app.rewriting)
        self.assertFalse(self.app.popup.visible)
        click(self.host.root, self.app.toast.win, "Review")
        self.assertTrue(self.app.review.visible)
        self.assertEqual(self.app.review.result(), "A rewrite.")
        click(self.host.root, self.app.review.win, "Copy")
        self.copied.assert_called_once_with("A rewrite.")

    def test_changed_target_offers_copy_without_pasting(self):
        from tests.test_win_writer_ui import click
        self.editor.same_context.return_value = False
        for editable in (True, False):
            with self.subTest(editable=editable):
                self.rewrite_from_bar(editable)
                self.editor.replace.assert_not_called()
                click(self.host.root, self.app.toast.win, "Copy")
        self.assertEqual(self.copied.call_args_list, [call("A rewrite."), call("A rewrite.")])
