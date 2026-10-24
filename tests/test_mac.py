import os
import plistlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

if sys.platform != "darwin":
    raise unittest.SkipTest("macOS only")

from harness_discussion import codex_rt  # noqa: E402
from harness_discussion.mac import pasteboard, permission, selection, startup, voice  # noqa: E402


class ClipboardHostTest(unittest.TestCase):
    def test_copy_reports_the_native_pasteboard_result(self):
        from harness_discussion.mac import host
        for success in (True, False):
            with self.subTest(success=success), mock.patch.object(host.AppKit, "NSPasteboard") as pasteboard_api:
                board = pasteboard_api.generalPasteboard.return_value
                board.setString_forType_.return_value = success
                self.assertEqual(host.MacHost.copy_text(None, "dictation"), success)
                board.clearContents.assert_called_once_with()

    def test_cancelled_copy_never_clears_the_pasteboard_even_during_acquisition(self):
        from harness_discussion.mac import host
        for phase in ("before", "acquiring"):
            with self.subTest(phase=phase), mock.patch.object(host.AppKit, "NSPasteboard") as api:
                cancelled = threading.Event()
                board = api.generalPasteboard.return_value
                if phase == "before":
                    cancelled.set()
                else:
                    api.generalPasteboard.side_effect = lambda: cancelled.set() or board
                self.assertFalse(host.MacHost.copy_text(None, "dictation", cancelled=cancelled.is_set))
                board.clearContents.assert_not_called()
                board.setString_forType_.assert_not_called()


class AvRateTest(unittest.TestCase):
    def test_normal_speed_is_default_rate(self):
        self.assertAlmostEqual(voice.av_rate(1.0), voice.AVSpeechUtteranceDefaultSpeechRate)

    def test_rates_stay_in_range_and_increase(self):
        rates = [voice.av_rate(s) for s in (0.5, 0.75, 1.0, 1.5, 2.0)]
        self.assertEqual(rates, sorted(rates))
        self.assertGreaterEqual(rates[0], voice.AVSpeechUtteranceMinimumSpeechRate)
        self.assertLessEqual(rates[-1], voice.AVSpeechUtteranceMaximumSpeechRate)


class WordOffsetTest(unittest.TestCase):
    def test_an_emoji_counts_once_in_python(self):
        text = "Hi 👋 there. Next."
        self.assertEqual(voice.py_offset(text, text.encode("utf-16-le").index("N".encode("utf-16-le")) // 2),
                         text.index("N"))
        self.assertEqual(voice.py_offset(text, 0), 0)


class StartupTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.dict(os.environ, {"HOME": self.home.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_enable_writes_open_command_and_disable_removes_it(self):
        self.assertFalse(startup.enabled())
        startup.set_enabled(True, "/Applications/Harness Discussion.app")
        with open(startup.plist_path(), "rb") as f:
            data = plistlib.load(f)
        self.assertEqual(data["Label"], startup.LABEL)
        self.assertEqual(data["ProgramArguments"], ["/usr/bin/open", "-a", "/Applications/Harness Discussion.app"])
        self.assertTrue(data["RunAtLoad"])
        startup.set_enabled(False)
        self.assertFalse(startup.enabled())
        startup.set_enabled(False)  # already off

    def test_source_run_cannot_enable(self):
        with mock.patch.object(startup, "bundle_path", return_value=None):
            self.assertRaises(OSError, startup.set_enabled, True)

    def test_refresh_follows_a_moved_app_only_when_enabled(self):
        startup.refresh("/Applications/New.app")
        self.assertFalse(startup.enabled())
        startup.set_enabled(True, "/Users/me/Downloads/Harness Discussion.app")
        startup.refresh("/Applications/Harness Discussion.app")
        with open(startup.plist_path(), "rb") as f:
            self.assertEqual(plistlib.load(f)["ProgramArguments"][-1], "/Applications/Harness Discussion.app")

    def old_plist(self):
        return os.path.join(self.home.name, "Library/LaunchAgents/com.tzachbon.codexspeak.plist")

    def test_codex_speak_login_item_carries_over(self):
        self.assertEqual(startup.LABEL, "com.tzachbon.harnessdiscussion")
        os.makedirs(os.path.dirname(self.old_plist()))
        with open(self.old_plist(), "wb") as f:
            plistlib.dump({"Label": "com.tzachbon.codexspeak", "ProgramArguments": ["/usr/bin/open", "-a", "/Applications/Codex Speak.app"]}, f)
        with mock.patch.object(startup.subprocess, "run", side_effect=OSError) as run:  # errors are ignored
            startup.refresh("/Applications/New.app")
        self.assertEqual(run.call_args.args[0], ["/bin/launchctl", "bootout", f"gui/{os.getuid()}", self.old_plist()])
        self.assertFalse(os.path.exists(self.old_plist()))
        with open(startup.plist_path(), "rb") as f:
            self.assertEqual(plistlib.load(f)["ProgramArguments"], ["/usr/bin/open", "-a", "/Applications/New.app"])

    def test_source_run_leaves_the_codex_speak_login_item_alone(self):
        os.makedirs(os.path.dirname(self.old_plist()))
        with open(self.old_plist(), "wb") as f:
            plistlib.dump({"Label": "com.tzachbon.codexspeak"}, f)
        with mock.patch.object(startup, "bundle_path", return_value=None), \
                mock.patch.object(startup.subprocess, "run") as run:
            startup.refresh()
        run.assert_not_called()
        self.assertTrue(os.path.exists(self.old_plist()))
        self.assertFalse(startup.enabled())

    def test_no_codex_speak_login_item_adds_none(self):
        with mock.patch.object(startup.subprocess, "run") as run:
            startup.refresh("/Applications/New.app")
        run.assert_not_called()
        self.assertFalse(startup.enabled())


class PermissionTest(unittest.TestCase):
    def test_stale_only_when_another_build_was_granted(self):
        self.assertTrue(permission.is_stale("0.1.0+abc", "0.1.1+def"))
        self.assertFalse(permission.is_stale("0.1.1+def", "0.1.1+def"))
        self.assertFalse(permission.is_stale(None, "0.1.1+def"))  # never granted
        self.assertFalse(permission.is_stale("0.1.0+abc", None))  # source run has no build key

    def test_a_grant_turns_on_every_shortcut(self):
        app = mock.Mock(cfg={"hotkey": "<cmd>+<shift>+r", "rewrite_hotkey": "<cmd>+<alt>+e",
                             "dictate_hotkey": "<cmd>+<alt>+d", "ax_granted_build": "b"})
        with mock.patch.object(permission, "current_build_id", return_value="b"):
            permission.Watcher(app)._changed(True)
        self.assertEqual([c.args for c in app.apply.call_args_list],
                         [("hotkey", "<cmd>+<shift>+r"), ("rewrite_hotkey", "<cmd>+<alt>+e"),
                          ("dictate_hotkey", "<cmd>+<alt>+d")])

    def test_reset_uses_fixed_arguments(self):
        with mock.patch.object(permission.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertTrue(permission.reset_grant())
        self.assertEqual(run.call_args.args[0], ["/usr/bin/tccutil", "reset", "Accessibility", startup.LABEL])


class PasteboardTest(unittest.TestCase):
    def test_secrets_and_promises_are_never_borrowed(self):
        self.assertTrue(pasteboard.safe_to_borrow({"public.utf8-plain-text"}))
        self.assertTrue(pasteboard.safe_to_borrow(set()))
        self.assertFalse(pasteboard.safe_to_borrow({"public.utf8-plain-text", "org.nspasteboard.ConcealedType"}))
        self.assertFalse(pasteboard.safe_to_borrow({"com.agilebits.onepassword"}))
        self.assertFalse(pasteboard.safe_to_borrow({"com.apple.pasteboard.promised-file-url"}))

    def test_overlapping_reads_borrow_the_pasteboard_one_at_a_time(self):
        state = {"inside": 0, "most": 0}

        def borrow():
            state["inside"] += 1
            state["most"] = max(state["most"], state["inside"])
            time.sleep(0.05)
            state["inside"] -= 1
            return "", None

        with mock.patch.object(pasteboard, "_copy_selection", side_effect=borrow):
            reads = [threading.Thread(target=pasteboard.copy_selection) for _ in range(3)]
            for t in reads:
                t.start()
            for t in reads:
                t.join()
        self.assertEqual(state["most"], 1)



class SelectionTest(unittest.TestCase):
    def read(self, *pids, ax=" hello ", in_web=False, fallback=True,
             markup="<p>Please:</p><ol><li>A</li><li>B</li></ol>"):
        def copy(html=False):
            return ("copied", markup) if html else "copied"
        with mock.patch.object(selection.permission, "trusted", return_value=True), \
                mock.patch.object(selection, "_frontmost_pid", side_effect=pids), \
                mock.patch.object(selection, "_expose"), \
                mock.patch.object(selection, "_via_ax", return_value=(ax, in_web)), \
                mock.patch.object(selection, "secure_input", return_value=False), \
                mock.patch.object(selection.pasteboard, "copy_selection", side_effect=copy) as self.copy:
            return selection.read(fallback)

    def test_reads_the_app_that_stays_frontmost(self):
        self.assertEqual(self.read(111, 111), "hello")

    def test_drops_the_selection_when_the_user_switches_apps(self):
        self.assertEqual(self.read(111, 222), "")

    def test_clipboard_fallback_reads_the_app_that_stays_frontmost(self):
        self.assertEqual(self.read(111, 111, 111, ax=""), "copied")

    def test_drops_the_copy_when_the_user_switches_apps_during_it(self):
        self.assertEqual(self.read(111, 111, 222, ax=""), "")

    def test_web_list_numbers_come_from_the_copied_html(self):
        self.assertEqual(self.read(111, 111, 111, ax="Please:\nA\nB", in_web=True), "Please:\n1. A\n2. B")

    def test_list_numbers_need_the_clipboard_setting_and_a_web_page(self):
        self.assertEqual(self.read(111, 111, ax="Please:\nA\nB", in_web=True, fallback=False), "Please:\nA\nB")
        self.assertEqual(self.read(111, 111, ax="Please:\nA\nB"), "Please:\nA\nB")
        self.copy.assert_not_called()

    def test_text_that_has_numbers_or_no_ordered_list_stays(self):
        self.assertEqual(self.read(111, 111, ax="Please:\n1. A", in_web=True), "Please:\n1. A")
        self.copy.assert_not_called()
        self.assertEqual(self.read(111, 111, 111, ax="A\nB", in_web=True, markup="<ul><li>A</li></ul>"), "A\nB")

    def test_drops_the_html_copy_when_the_user_switches_apps_during_it(self):
        self.assertEqual(self.read(111, 111, 222, ax="Please:\nA\nB", in_web=True), "")


class CodexPathTest(unittest.TestCase):
    def test_extended_path_adds_homebrew_and_keeps_current(self):
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
            parts = codex_rt.extended_path().split(os.pathsep)
        self.assertIn("/opt/homebrew/bin", parts)
        self.assertIn("/usr/bin", parts)
        self.assertEqual(len(parts), len(set(parts)))

    def test_native_binary_behind_npm_shim(self):
        with tempfile.TemporaryDirectory() as root:
            pkg = os.path.join(root, "lib", "node_modules", "@openai", "codex")
            native = os.path.join(pkg, "node_modules", "@openai", "codex-darwin-arm64", "vendor",
                                  "aarch64-apple-darwin", "bin", "codex")
            os.makedirs(os.path.dirname(native))
            os.makedirs(os.path.join(pkg, "bin"))
            shim = os.path.join(pkg, "bin", "codex.js")
            with open(shim, "w") as f:
                f.write("#!/usr/bin/env node\n")
            open(native, "wb").close()
            link = os.path.join(root, "codex")
            os.symlink(shim, link)
            self.assertEqual(os.path.realpath(codex_rt._native_darwin(link)), os.path.realpath(native))

    def test_newest_codex_wins_over_path_order(self):
        with tempfile.TemporaryDirectory() as root:
            dirs = [os.path.join(root, name) for name in ("local-bin", "fnm", "brew")]
            for d in dirs:
                os.makedirs(d)
                exe = os.path.join(d, "codex")
                open(exe, "w").close()
                os.chmod(exe, 0o755)
            versions = {os.path.join(dirs[0], "codex"): (0, 144, 6), os.path.join(dirs[1], "codex"): (0, 163, 0),
                        os.path.join(dirs[2], "codex"): (0, 163, 0)}
            with mock.patch.object(codex_rt, "extended_path", return_value=os.pathsep.join(dirs)), \
                    mock.patch.object(codex_rt, "_native_darwin", side_effect=lambda shim: shim), \
                    mock.patch.object(codex_rt, "_version", side_effect=versions.get):
                self.assertEqual(codex_rt._newest_darwin(), os.path.join(dirs[1], "codex"))

    def test_mach_o_binary_is_used_directly(self):
        with tempfile.TemporaryDirectory() as root:
            exe = os.path.join(root, "codex")
            with open(exe, "wb") as f:
                f.write(b"\xcf\xfa\xed\xfe" + b"\0" * 12)
            self.assertEqual(codex_rt._native_darwin(exe), os.path.realpath(exe))



class SourcesSettingsTest(unittest.TestCase):
    def setUp(self):
        import AppKit
        from types import SimpleNamespace
        from harness_discussion import settings
        from harness_discussion.mac import settings_ui, stt_page, ui
        AppKit.NSApplication.sharedApplication()
        self.changes, self.played, self.summary = [], [], ["missing", None, 0]
        engines = [SimpleNamespace(name="Cursor", voices=(("eve", "Eve"), ("ara", "Ara")), default_voice="ara"),
                   SimpleNamespace(name="Codex"), SimpleNamespace(name="macOS")]
        self.app = SimpleNamespace(
            cfg=dict(settings.DEFAULTS), statuses={"Cursor": "signed_out", "Codex": "missing"}, writer_ready=False,
            chain=SimpleNamespace(engines=engines), stt_error=None, check_update=lambda: None,
            update_state=lambda: ("", False), available_version=lambda: None,
            summarize_state=lambda: tuple(self.summary[:2]),
            summarize_action=lambda: self.summary.__setitem__(2, self.summary[2] + 1),
            change=lambda key, value, save=True: self.changes.append((key, value)),
            play=lambda text, tag, only=None: self.played.append((text, only)))
        for p in (mock.patch.object(ui, "show"), mock.patch.object(settings_ui.startup, "enabled", return_value=False),
                  mock.patch.object(stt_page, "stt_connection", return_value=("", ""))):
            p.start()
            self.addCleanup(p.stop)
        self.win = settings_ui.SettingsWindow(self.app, lambda: None)
        self.addCleanup(self.win.win.close)
        self.rows = self.win.source_rows

    def test_each_status_renders_its_text(self):
        self.assertEqual([self.rows[n][1].stringValue() for n in ("Cursor", "Codex", "macOS")],
                         ["Not signed in", "Not installed", "Checking…"])
        self.app.statuses = {"Cursor": "expired", "macOS": "ready"}
        self.win.refresh_sources()
        self.assertEqual([self.rows[n][1].stringValue() for n in ("Cursor", "macOS")],
                         ["Sign-in expired", "Ready"])
        self.assertEqual((self.rows["Codex"][2], self.rows["macOS"][2]), (None, None))

    def test_the_sidebar_shows_one_page_and_marks_voices_while_a_source_needs_a_sign_in(self):
        self.assertEqual([p.isHidden() for p in self.win.pages], [False, True, True, True, True, True])
        self.assertTrue(self.win.nav[0].title().endswith("●"))  # Cursor is signed out
        self.win.nav[5].performClick_(None)
        self.assertEqual([p.isHidden() for p in self.win.pages], [True, True, True, True, True, False])
        self.app.statuses = {"Cursor": "ready", "Codex": "ready"}
        self.win.refresh_sources()
        self.assertFalse(self.win.nav[0].title().endswith("●"))

    def test_the_summarize_page_shows_each_state_and_its_button(self):
        w = self.win
        w.nav[3].performClick_(None)
        shown = lambda: (w.summary_status.stringValue(), w.summary_button.title(), w.summary_bar.isHidden())
        self.assertEqual(shown(), ("Not downloaded", "Download", True))
        self.summary[:2] = ["missing", 0.42]
        w.refresh_summarize()
        self.assertEqual(shown(), ("Downloading 42% of 2.5 GB", "Cancel", False))
        self.assertEqual(w.win.contentView().frame().size.height, w.content.fittingSize().height)
        self.summary[:2] = ["ready", None]
        w.refresh_summarize()
        self.assertEqual(shown(), ("Ready", "Remove", True))
        w.summary_button.performClick_(None)
        self.assertEqual(self.summary[2], 1)
        self.summary[:2] = ["unsupported", None]
        w.refresh_summarize()
        self.assertTrue(w.summary_button.isHidden())

    def test_the_captions_page_saves_font_size_background_and_opacity(self):
        self.assertFalse(self.win.opacity.isEnabled())  # no background by default
        self.win.font_size.setIntValue_(14)
        self.win.font_size.target().run_(self.win.font_size)
        self.win._background_changed(True)
        self.win.opacity.setDoubleValue_(35)
        self.win.opacity.target().run_(self.win.opacity)
        self.assertEqual(self.win.font_size_label.stringValue(), "14 pt")
        self.assertTrue(self.win.opacity.isEnabled())
        self.assertEqual(self.changes, [("caption_font_size", 14), ("caption_background", True),
                                        ("caption_background_opacity", 0.35)])

    def test_the_window_fits_a_status_that_wraps(self):
        for statuses in ({}, {"Cursor": "signed_out", "Codex": "signed_out", "macOS": "signed_out"}):
            self.app.statuses = statuses
            self.win.refresh_sources()
            self.assertEqual(self.win.win.contentView().frame().size.height, self.win.content.fittingSize().height)

    def test_up_down_and_enable_save_the_sources(self):
        self.assertFalse(self.rows["Cursor"][4][1].isEnabled())  # already first
        self.rows["Codex"][4][1].performClick_(None)
        self.assertEqual(self.changes[-1], ("sources", [["Codex", True], ["Cursor", True], ["Kokoro", True],
                                                        ["macOS", True]]))
        self.assertEqual(self.win.sources.arrangedSubviews()[0], self.rows["Codex"][3])
        self.rows["Codex"][4][2].performClick_(None)
        self.assertEqual([n for n, _ in self.changes[-1][1]], ["Cursor", "Codex", "Kokoro", "macOS"])
        self.rows["macOS"][0].performClick_(None)
        self.assertEqual(self.changes[-1], ("sources", [["Cursor", True], ["Codex", True], ["Kokoro", True],
                                                        ["macOS", False]]))

    def test_kokoro_offers_its_download_until_it_is_ready(self):
        fix = self.win.fix_buttons["Kokoro"]
        for state, text, title in (("not_downloaded", "Not downloaded", "Download…"),
                                   ("downloading", "Downloading…", None), ("ready", "Ready", None)):
            self.app.statuses = {"Kokoro": state}
            self.win.refresh_sources()
            self.assertEqual(self.rows["Kokoro"][1].stringValue(), text)
            self.assertEqual(fix.isHidden(), title is None)
            if title:
                self.assertEqual(fix.title(), title)

    def test_cursor_voice_saves_under_its_name_and_test_reads_one_source(self):
        voice = self.rows["Cursor"][2]
        self.assertEqual(voice.titleOfSelectedItem(), "Ara")  # Cursor's default before a choice
        voice.selectItemAtIndex_(0)
        voice.sendAction_to_(voice.action(), voice.target())
        self.assertEqual(self.changes[-1], ("voices", {"Cursor": "eve"}))
        self.win.cfg["voices"] = {"Cursor": "gone"}  # a voice Cursor no longer offers
        self.win.refresh_sources()
        self.assertEqual(voice.titleOfSelectedItem(), "Ara")
        self.rows["Codex"][4][0].performClick_(None)
        self.assertEqual(self.played, [("This is Codex.", "Codex")])

    def test_the_welcome_header_shows_above_sources_only_on_first_run(self):
        from harness_discussion.mac import settings_ui
        self.assertIsNone(self.win.welcome)
        welcome = settings_ui.SettingsWindow(self.app, lambda: None, welcome=True)
        self.addCleanup(welcome.win.close)
        views = list(welcome.sources.superview().arrangedSubviews())
        self.assertEqual(welcome.welcome.stringValue(), "Pick the voices Harness Discussion uses.")
        self.assertEqual(views.index(welcome.welcome) + 1, views.index(welcome.sources))


if __name__ == "__main__":
    unittest.main()
