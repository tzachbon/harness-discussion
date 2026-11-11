"""Windows meeting settings exercise the real Tk controls without recording audio."""
import gc
import queue
import sys
import tempfile
import tkinter as tk
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from tkinter import ttk

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

from PIL import Image
from harness_discussion import app_meetings, meeting_copy as copy, meetings, settings
from harness_discussion.win import host, settings_ui


class MeetingsSettings(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir_patch = patch.object(meetings, "DIR", Path(self.temp.name))
        self.dir_patch.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.calls = []
        self.app = SimpleNamespace(summarize_state=lambda: ("missing", None), summarize_action=lambda: None,
                                   calendar_status=copy.CALENDAR_NOT_SET,
                                   set_meetings=lambda value: self.calls.append(value) or True,
                                   refresh_meeting_calendar=lambda: self.calls.append("refresh"),
                                   _meeting_open=lambda: None)
        with patch.object(settings_ui.settings, "startup_enabled", return_value=False), \
             patch.object(settings_ui.SettingsWindow, "focus", lambda self: None), \
             patch.object(settings_ui, "stt_connection", return_value=("", "")):
            self.window = settings_ui.SettingsWindow(self.root, dict(settings.DEFAULTS), Image.new("RGBA", (32, 32)),
                                                     lambda *args: None, lambda *_: None, summarizer=self.app)

    def tearDown(self):
        self.window.close()
        self.root.destroy()
        self.window = self.root = None
        gc.collect()
        self.dir_patch.stop()
        self.temp.cleanup()

    def test_consent_and_denial(self):
        self.window.meetings_check.invoke()
        self.assertEqual(self.calls, [True])
        self.assertTrue(self.window.cfg["meetings"])
        self.app.set_meetings = lambda value: False
        self.window.meetings_check.invoke()
        self.assertTrue(self.window.meetings_on.get())

    def test_calendar_mask_refresh_and_storage(self):
        w = self.window
        self.assertEqual(w.meeting_url_entry.cget("show"), "•")
        show = next(child for child in w.meeting_url_entry.master.winfo_children()
                    if isinstance(child, ttk.Checkbutton))
        show.invoke()
        self.assertEqual(w.meeting_url_entry.cget("show"), "")
        show.invoke()
        self.assertEqual(w.meeting_url_entry.cget("show"), "•")
        w.meeting_url.set("https://example.test/private")
        w.meeting_test.invoke()
        self.assertEqual(self.calls, ["refresh"])
        self.assertEqual(w.meeting_calendar_status.cget("text"), copy.CHECKING)
        self.app.calendar_status = copy.EVENTS_TODAY.format(n=3)
        w.refresh_meetings()
        self.assertEqual(w.meeting_calendar_status.cget("text"), copy.EVENTS_TODAY.format(n=3))
        self.assertEqual(w.meeting_storage.cget("text"), "0 meetings · 0 MB")

    def test_merged_app_consent_and_calendar_error_hide_url(self):
        app = app_meetings.Meetings()
        app.cfg = dict(settings.DEFAULTS)
        app.change = lambda key, value: app.cfg.__setitem__(key, value)
        app.host = host.TkHost.__new__(host.TkHost)
        app.host.root = self.root
        app._meeting_session = None
        app.events = queue.Queue()
        app.calendar_status = copy.CALENDAR_NOT_SET
        self.window.meeting_app = app
        self.window.on_change = app.change
        with patch.object(host.messagebox, "askokcancel", return_value=True) as confirm:
            self.window.meetings_check.invoke()
        self.assertTrue(confirm.called)
        self.assertTrue(app.cfg["meeting_consent"])
        secret = "https://example.test/private-calendar-secret"
        self.window.meeting_url.set(secret)
        with patch.object(app_meetings.calendar_ics, "fetch", side_effect=ValueError(secret)), \
             self.assertLogs("harness_discussion", level="INFO") as logs:
            self.window.meeting_test.invoke()
            event = app.events.get(timeout=5)
        app._meeting_events(*event[1:])
        self.window.refresh_meetings()
        self.assertEqual(self.window.meeting_calendar_status.cget("text"), copy.CALENDAR_ERROR)
        self.assertNotIn(secret, " ".join(logs.output))
        events = [SimpleNamespace(start=datetime.now().astimezone()) for _ in range(3)]
        with patch.object(app_meetings.calendar_ics, "fetch", return_value=b""), \
             patch.object(app_meetings.calendar_ics, "parse", return_value=events):
            self.window.meeting_test.invoke()
            event = app.events.get(timeout=5)
        app._meeting_events(*event[1:])
        self.window.refresh_meetings()
        self.assertEqual(self.window.meeting_calendar_status.cget("text"), copy.EVENTS_TODAY.format(n=3))


if __name__ == "__main__":
    unittest.main()
