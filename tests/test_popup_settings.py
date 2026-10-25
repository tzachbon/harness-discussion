"""Native button-to-Settings checks without speech services or persistent changes."""
import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

import gc
import json
import tempfile
import threading
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from harness_discussion import core, settings
from harness_discussion.audio import NoAudioError
from harness_discussion.win import host as win_host, popup as win_popup, settings_ui


class Speech:
    speed = 1.0

    def __init__(self, name):
        self.name, self.paused = name, threading.Event()

    def speak(self, text, tag, done, heard, on_caption=None):
        heard()
        if on_caption:
            on_caption(text)  # Codex captions follow its transcript

    def stop(self):
        self.paused.clear()

    def pause(self):
        self.paused.set()

    def resume(self):
        self.paused.clear()

    close = stop


class SignedOut(Speech):
    """Cursor with no sign-in, as on CI: it fails before audio and the chain moves on."""

    def speak(self, text, tag, done, heard, on_caption=None):
        done(NoAudioError("signed out"))


class PopupSettings(unittest.TestCase):
    def setUp(self):
        self.boundaries = ExitStack()
        self.addCleanup(self.boundaries.close)
        directory = self.boundaries.enter_context(tempfile.TemporaryDirectory())
        path = Path(directory) / "settings.json"
        path.write_text(json.dumps(settings.DEFAULTS), encoding="utf-8")
        self.boundaries.enter_context(patch.object(settings, "PATH", str(path)))
        self.boundaries.enter_context(patch.object(settings, "load", return_value=dict(settings.DEFAULTS)))
        self.boundaries.enter_context(patch.object(settings, "startup_enabled", return_value=False))
        self.boundaries.enter_context(patch.object(settings_ui, "stt_connection", return_value=("", "")))
        self.boundaries.enter_context(patch.object(core, "CAPTION_DELAY_S", 0))
        for factory, name in (("CodexEngine", "Codex"), ("EdgeEngine", "Edge"), ("SapiEngine", "Windows")):
            self.boundaries.enter_context(patch.object(win_host, factory, return_value=Speech(name)))
        self.boundaries.enter_context(patch.object(win_host, "CursorEngine", return_value=SignedOut("Cursor")))
        self.host = win_host.TkHost()
        self.app = core.App(self.host)
        self.app.popup.show("A sentence to read.", 140, 140)
        self.host.root.update()

    def tearDown(self):
        if self.app.settings_win and self.app.settings_win.alive:
            self.app.settings_win.close()
        self.app.chain.close()
        self.app.popup.hide()
        self.host.icon.stop()
        self.host.stop_trigger()
        self.host.root.destroy()
        self.app = None
        gc.collect()

    def pick_overflow(self, label):
        popup = self.app.popup
        with patch.object(popup.overflow, "post"):
            popup.more_btn.command()
        popup.overflow.invoke(label)
        while not self.app.events.empty():
            self.app._handle(*self.app.events.get_nowait())
        self.host.root.update()

    def speaking(self):
        self.assertEqual(self.app.chain.active_name, "Codex")
        return self.app.chain._active

    def click(self, button):
        label = button.parts[1]
        label.event_generate("<ButtonPress-1>", x=2, y=2)
        label.event_generate("<ButtonRelease-1>", x=2, y=2)
        while not self.app.events.empty():
            self.app._handle(*self.app.events.get_nowait())
        self.host.root.update()

    def test_settings_pauses_speech_reuses_window_and_play_resumes_after_close(self):
        popup = self.app.popup
        self.click(popup.btn)
        self.assertTrue(popup.playing)
        self.assertFalse(popup.paused)
        read_id = self.app._read_id
        self.pick_overflow("Settings…")
        window = self.app.settings_win
        self.assertTrue(window.win.winfo_viewable())
        self.assertTrue(popup.paused)
        self.assertTrue(self.speaking().paused.is_set())
        self.assertEqual(popup.caption.text, "A sentence to read.")
        self.assertEqual(self.app._read_id, read_id)
        self.pick_overflow("Settings…")
        self.assertIs(self.app.settings_win, window)
        self.assertTrue(popup.paused)
        window.close()
        self.assertTrue(popup.paused)
        self.click(popup.btn)
        self.assertFalse(popup.paused)
        self.assertFalse(self.speaking().paused.is_set())
        self.assertEqual(self.app._read_id, read_id)

    def test_idle_settings_does_not_start_speech_and_closed_window_reopens(self):
        popup = self.app.popup
        self.pick_overflow("Settings…")
        window = self.app.settings_win
        self.assertTrue(window.win.winfo_viewable())
        self.assertFalse(popup.playing)
        self.assertIsNone(self.app.chain._active)
        window.close()
        self.pick_overflow("Settings…")
        self.assertIsNot(self.app.settings_win, window)
        self.assertTrue(self.app.settings_win.win.winfo_viewable())
        self.assertFalse(popup.playing)

    def test_source_label_follows_set_source_and_the_menu_moves_a_pick_first(self):
        popup = self.app.popup
        self.assertEqual(popup.source_btn.parts[1]["text"], "Cursor")  # first enabled, not yet checked
        popup.set_source("Edge")
        self.assertEqual(popup.source_btn.parts[1]["text"], "Edge")
        self.app.statuses = {"Cursor": "signed_out"}
        with patch.object(popup, "_post_menu"):
            popup._source_menu()
        menu = popup.sources_menu
        self.assertEqual([menu.type(i) == "separator" or menu.entrycget(i, "label")
                          for i in range(menu.index("end") + 1)],
                         ["Codex", "Kokoro", "Edge", "Windows", True, "Cursor: Sign in…"])
        with patch.object(settings, "save") as save:  # its default path is the real profile
            menu.invoke(3)
        save.assert_called_once()
        self.assertEqual([n for n, _ in self.app.cfg["sources"]], ["Windows", "Cursor", "Codex", "Kokoro", "Edge"])
        self.assertEqual(popup.source_btn.parts[1]["text"], "Windows")

    def test_summarize_reads_the_summary_and_settings_shows_the_model(self):
        popup, app = self.app.popup, self.app
        app._summarize_status = "ready"
        app.summary_server = type("Fake", (), {"summarize": lambda self, text, language: "A short summary.",
                                               "stop": lambda self: None})()
        label = popup.ai_btn.parts[1]
        label.event_generate("<ButtonPress-1>", x=2, y=2)
        label.event_generate("<ButtonRelease-1>", x=2, y=2)
        self.assertTrue(popup.playing and popup.loading)
        self.assertEqual(label["fg"], win_popup.HUD_ACCENT)
        app._handle(*app.events.get(timeout=5))  # the summary, from the worker thread
        for _ in range(2):  # speech events, then the caption they schedule
            while not app.events.empty():
                app._handle(*app.events.get_nowait())
            self.host.root.update()
        self.assertEqual(self.speaking().name, "Codex")
        self.assertEqual(popup.caption.text, "A short summary.")
        popup.set_summarize_progress(0.42)
        self.assertEqual(label["text"], "42%")
        popup.set_summarize_progress(None)
        self.pick_overflow("Settings…")
        self.app.settings_win.nav[3].invoke()
        self.assertEqual(self.app.settings_win.summary_status["text"], "Ready")

    def test_opening_settings_while_already_paused_does_not_resume(self):
        popup = self.app.popup
        self.click(popup.btn)
        self.click(popup.btn)
        self.assertTrue(popup.paused)
        self.pick_overflow("Settings…")
        self.assertTrue(self.app.settings_win.win.winfo_viewable())
        self.assertTrue(popup.paused)
        self.assertTrue(self.speaking().paused.is_set())


if __name__ == "__main__":
    unittest.main()
