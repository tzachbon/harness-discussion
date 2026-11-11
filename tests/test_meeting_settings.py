"""Meeting settings reject invalid saved values and copy formats platform-safe names."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness_discussion import meeting_copy, settings


class MeetingSettingsTest(unittest.TestCase):
    def load(self, values):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps(values), encoding="utf-8")
            return settings.load(path)

    def test_defaults_and_invalid_types(self):
        defaults = {"meetings": False, "meeting_ask": True, "meeting_consent": False,
                    "meeting_ics_url": "", "meeting_eventkit": False}
        for values in ({}, {key: 123 for key in defaults}):
            actual = self.load(values)
            self.assertEqual({key: actual[key] for key in defaults}, defaults)
        valid = {"meetings": True, "meeting_ask": False, "meeting_consent": True,
                 "meeting_ics_url": "https://example.com/calendar", "meeting_eventkit": True}
        actual = self.load(valid)
        self.assertEqual({key: actual[key] for key in valid}, valid)

    def test_url_length(self):
        self.assertEqual(self.load({"meeting_ics_url": "a" * 3000})["meeting_ics_url"], "")
        self.assertEqual(self.load({"meeting_ics_url": "a" * 2048})["meeting_ics_url"], "")
        self.assertEqual(self.load({"meeting_ics_url": "a" * 2047})["meeting_ics_url"], "a" * 2047)

    def test_copy_formatting(self):
        self.assertEqual(meeting_copy.elapsed(5), "0:05")
        self.assertEqual(meeting_copy.elapsed(3725), "1:02:05")
        self.assertEqual(meeting_copy.ask_lines("Weekly sync", "Zoom"), (meeting_copy.ASK, "Weekly sync · Zoom"))
        self.assertEqual(meeting_copy.ask_lines(None, "Zoom"), (meeting_copy.ASK, "Zoom"))
        self.assertEqual(meeting_copy.ask_lines(None, None), (meeting_copy.ASK, None))
        meeting = SimpleNamespace(started_at="2026-11-11T10:02:13+02:00", title='a\\/:*?"<>|b')
        self.assertEqual(meeting_copy.export_name(meeting), "2026-11-11 a---------b.md")
