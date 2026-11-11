"""Detector tests use a monotonic clock and fixed aware events."""
import unittest
from datetime import datetime, timedelta, timezone

import harness_discussion.meeting_detect as detect
import harness_discussion.meetings as meetings


class DetectorTest(unittest.TestCase):
    def test_hold_call_and_browser(self):
        for app in ("zoom.exe", "chrome.exe"):
            d = detect.Detector()
            for t in (0, 2, 4, 6):
                self.assertIsNone(d.update(t, {app}, False, None))
            self.assertEqual(d.update(8, {app}, False, None), "ask")

    def test_other_app_requires_event(self):
        d = detect.Detector()
        for t in range(0, 12, 2):
            self.assertIsNone(d.update(t, {"editor.exe"}, False, None))
        for t in (12, 14, 16, 18):
            self.assertIsNone(d.update(t, {"editor.exe"}, True, None))
        self.assertEqual(d.update(20, {"editor.exe"}, True, None), "ask")

    def test_rearm_and_dismissal(self):
        d = detect.Detector()
        for t in (0, 2, 4, 6):
            d.update(t, {"zoom.exe"}, False, None)
        self.assertEqual(d.update(8, {"zoom.exe"}, False, None), "ask")
        d.dismissed(8)
        for t in range(10, 80, 2):
            self.assertIsNone(d.update(t, {"zoom.exe"}, False, None))
        for t in range(80, 140, 2):
            self.assertIsNone(d.update(t, set(), False, None))
        d.update(140, set(), False, None)
        for t in (142, 144, 146, 148):
            self.assertIsNone(d.update(t, {"zoom.exe"}, False, None))
        self.assertEqual(d.update(150, {"zoom.exe"}, False, None), "ask")

    def test_ended_auto_never_manual_and_kept_until_return(self):
        d = detect.Detector()
        for t in range(0, 22, 2):
            self.assertIsNone(d.update(t, set(), False, "manual"))
        d = detect.Detector()
        d.update(0, {"zoom.exe"}, False, "zoom.exe")
        for t in range(2, 22, 2):
            self.assertIsNone(d.update(t, set(), False, "zoom.exe"))
        self.assertEqual(d.update(22, set(), False, "zoom.exe"), "ended")
        d.kept(22)
        for t in range(24, 70, 2):
            self.assertIsNone(d.update(t, set(), False, "zoom.exe"))
        d.update(70, {"zoom.exe"}, False, "zoom.exe")
        for t in range(72, 92, 2):
            self.assertIsNone(d.update(t, set(), False, "zoom.exe"))
        self.assertEqual(d.update(92, set(), False, "zoom.exe"), "ended")

    def test_app_name_prefix(self):
        self.assertEqual(detect.app_name("com.google.chrome.helper"), "Chrome")

    def test_current_event_window_latest_and_duration(self):
        now = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
        def event(title, start, end):
            return meetings.Event(title, now + timedelta(minutes=start), now + timedelta(minutes=end), "ics")
        early = event("early", -20, 20)
        latest = event("latest", 5, 30)
        too_early = event("future", 6, 30)
        long = event("long", 0, 241)
        ended = event("ended", -20, 0)
        self.assertEqual(detect.current_event([early, latest, too_early, long, ended], now), latest)
        self.assertIsNone(detect.current_event([too_early, long, ended], now))
