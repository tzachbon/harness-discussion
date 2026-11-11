"""Calendar tests use fixed data and never perform network requests."""
import io
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import harness_discussion.calendar_ics as calendar_ics

FIXTURES = Path(__file__).parent / "fixtures" / "meetings"
START = datetime(2026, 11, 1, tzinfo=timezone.utc)
END = datetime(2026, 12, 1, tzinfo=timezone.utc)


class CalendarTest(unittest.TestCase):
    def test_fixtures_have_aware_events(self):
        for name in ("calendar.ics", "outlook.ics"):
            events = calendar_ics.parse((FIXTURES / name).read_bytes(), START, END)
            self.assertTrue(events, name)
            self.assertTrue(all(e.title and e.start.utcoffset() is not None and e.end.utcoffset() is not None
                                for e in events))

    def test_recurrence_override_exdate_duration_and_skips(self):
        data = b"""BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
UID:series\r
DTSTART;TZID=America/New_York:20261102T100000\r
DTEND;TZID=America/New_York:20261102T110000\r
RRULE:FREQ=WEEKLY;COUNT=4\r
EXDATE;TZID=America/New_York:20261109T100000\r
SUMMARY:Weekly\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:series\r
RECURRENCE-ID;TZID=America/New_York:20261116T100000\r
DTSTART;TZID=America/New_York:20261116T120000\r
DURATION:PT30M\r
SUMMARY:Changed\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:cancelled\r
DTSTART:20261103T100000Z\r
DTEND:20261103T110000Z\r
STATUS:CANCELLED\r
SUMMARY:Cancelled\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:all-day\r
DTSTART;VALUE=DATE:20261104\r
DTEND;VALUE=DATE:20261105\r
SUMMARY:All day\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:folded\r
DTSTART:20261105T100000Z\r
DURATION:PT45M\r
SUMMARY:Long\\, folded\r
 title\r
END:VEVENT\r
END:VCALENDAR\r
"""
        events = calendar_ics.parse(data, START, END)
        self.assertCountEqual([(e.title, e.start.isoformat(), int((e.end - e.start).total_seconds())) for e in events],
                         [("Weekly", "2026-11-02T10:00:00-05:00", 3600),
                          ("Long, foldedtitle", "2026-11-05T10:00:00+00:00", 2700),
                          ("Changed", "2026-11-16T12:00:00-05:00", 1800),
                          ("Weekly", "2026-11-23T10:00:00-05:00", 3600)])

    def test_outlook_vtimezone_fixed_offset(self):
        data = b"""BEGIN:VCALENDAR\r
BEGIN:VTIMEZONE\r
TZID:Pacific Standard Time\r
BEGIN:STANDARD\r
TZOFFSETTO:-0800\r
END:STANDARD\r
END:VTIMEZONE\r
BEGIN:VEVENT\r
UID:outlook\r
DTSTART;TZID=Pacific Standard Time:20261111T100000\r
DURATION:PT1H\r
SUMMARY:Outlook\r
END:VEVENT\r
END:VCALENDAR\r
"""
        event, = calendar_ics.parse(data, START, END)
        self.assertEqual(event.start.isoformat(), "2026-11-11T10:00:00-08:00")

    def test_normalize_url(self):
        self.assertEqual(calendar_ics.normalize_url(" webcal://example.com/feed "), "https://example.com/feed")
        for url in ("http://example.com", "file:///tmp/feed"):
            with self.assertRaises(ValueError):
                calendar_ics.normalize_url(url)

    def test_fetch_rejects_redirect_to_http(self):
        # Exercise the redirect handler installed on the opener, without contacting a host.
        import urllib.request
        original = urllib.request.build_opener
        def opener(*handlers):
            result = original(*handlers)
            request = urllib.request.Request("https://example.com/feed")
            for handler in result.handlers:
                if isinstance(handler, urllib.request.HTTPRedirectHandler):
                    handler.redirect_request(request, None, 302, "Found", {}, "http://example.com/feed")
            self.fail("No redirect handler rejected HTTP")
        with patch("urllib.request.build_opener", side_effect=opener):
            with self.assertRaises((ValueError, HTTPError)):
                calendar_ics.fetch("https://example.com/feed")

    def test_fetch_rejects_oversized_body(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
        class Opener:
            def open(self, *args, **kwargs):
                return Response(b"x" * (calendar_ics.MAX_BYTES + 1))
        with patch("urllib.request.build_opener", return_value=Opener()):
            with self.assertRaises((ValueError, OSError)):
                calendar_ics.fetch("https://example.com/feed")

