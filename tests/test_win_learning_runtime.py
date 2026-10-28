"""Opt-in real Windows app/editor learning flow, with fixture audio and a fixture transcription source.

DICTATION_LEARNING_NATIVE_CHECK=1 uses a disposable owned editor, real guarded paste,
the app's normal poll/event loop, native settings and disk persistence. No model/network request.
"""
import os
import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

import ctypes
import json
import subprocess
import time
from ctypes import wintypes
from unittest.mock import patch

from harness_discussion import app_writer, local_stt, mic, settings
from harness_discussion.app_tour import App
from harness_discussion.win import clipboard, edit
from harness_discussion.win.host import TkHost
from tests.dictation_helpers import Devices, RECORDER
from tests.test_win_edit_native import HELPER


@unittest.skipUnless(os.environ.get("DICTATION_LEARNING_NATIVE_CHECK") == "1", "Owned native app/editor check")
class NativeLearning(unittest.TestCase):
    def test_dictation_correction_persistence_next_hint_and_native_settings(self):
        # Run with isolated APPDATA/LOCALAPPDATA, as all dictation test commands require.
        self.assertIn("harness-learning-139", settings.DIR.lower())
        saved = dict(clipboard.snapshot())  # never print clipboard contents
        cfg = {**settings.DEFAULTS, "writer": True, "live_check": False, "tour_done": True,
               "dictate_polish": False, "dictation_history": False, "auto_update": False,
               "dictation_learn": True, "dictation_words": [],
               "writer_sources": [[n, False] for n in settings.WRITER_SOURCES],
               "stt_sources": [[n, n == "Local"] for n in settings.STT_SOURCES],
               "sources": [[n, False] for n in settings.DEFAULT_SOURCES]}
        settings.save(cfg)
        hints = []

        class Source(local_stt.LocalStt):
            def status(self):
                return "ready"

            def prewarm(self):
                pass

            def transcribe(self, audio, language="", cancelled=lambda: False, prompt=""):
                hints.append(prompt)
                return "Ask Zach today" if len(hints) == 1 else "Ask Tzach again"

        helper = subprocess.Popen([sys.executable, "-c", HELPER.replace("Left old right", "")],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(helper.stdout.close)
        def stop():
            if helper.poll() is None:
                helper.terminate()
            helper.wait(timeout=5)
        self.addCleanup(stop)
        target = json.loads(helper.stdout.readline())
        u32 = ctypes.windll.user32
        u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u32.SendMessageW.restype = ctypes.c_ssize_t
        u32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        devices = Devices()
        with patch.object(TkHost, "engines", return_value=[]), \
                patch.object(app_writer.dictation, "sources", return_value=[Source()]), \
                patch.object(app_writer.mic, "Recorder", lambda device="": RECORDER(devices.factory, device=device)):
            host = TkHost()
            host.mic_permission = lambda then: then(True)
            host.history_changed = lambda: None
            app = App(host)
            self.addCleanup(app._close_writer)
            self.addCleanup(lambda: host.root.destroy() if host.root.winfo_exists() else None)
            def until(predicate, seconds=15):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    host.root.update()
                    app.pump()
                    if predicate():
                        return
                    time.sleep(0.02)
                self.fail("Native learning workflow did not reach its expected state")

            def focus():
                u32.PostThreadMessageW(target["thread"], 0x8001, target["edit"], 0)
                until(lambda: u32.GetForegroundWindow() == target["window"])

            def dictate():
                focus()
                app._dictate_down()
                until(lambda: app._recorder is not None and app._recorder.phase == "recording")
                app._dictate_up()
                until(lambda: app._recorder is None and app._dictation_job is None and app._learning is not None)
                until(lambda: app._learning.confirmed)

            try:
                dictate()
                self.assertEqual(edit.observe(app._learning.context).text, "Ask Zach today")
                # The owned editor replaces the selected misspelling, exactly as an editor edit does.
                u32.SendMessageW(target["edit"], 0x00B1, 4, 8)
                replacement = ctypes.create_unicode_buffer("Tzach")
                u32.SendMessageW(target["edit"], 0x00C2, 1, ctypes.addressof(replacement))
                until(lambda: app.cfg["dictation_words"] == ["Tzach"])
                self.assertEqual(settings.load()["dictation_words"], ["Tzach"])
                self.assertEqual(dict(clipboard.snapshot()), saved)
                # A second real insertion must pass the learned hint to its selected source.
                dictate()
                self.assertEqual(hints, ["", "Tzach"])
                app._stop_learning()
                window = host.open_settings()
                app.settings_win = window
                window.show_page(5)
                host.root.update()
                self.assertEqual(window.words_text.get("1.0", "end").strip(), "Tzach")
                self.assertTrue(window.learning_on.get())
                window.learning_check.invoke()
                self.assertFalse(app.cfg["dictation_learn"])
                window._clear_words()
                self.assertEqual(settings.load()["dictation_words"], [])
                window.close()
            finally:
                app._close_writer()
                self.assertEqual(dict(clipboard.snapshot()), saved)


if __name__ == "__main__":
    unittest.main()
