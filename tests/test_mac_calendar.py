import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


@unittest.skipUnless(sys.platform == 'darwin', 'Mac only')
class CalendarTests(unittest.TestCase):
    def test_status_mapping(self):
        from harness_discussion.mac import calendar
        for value, expected in enumerate(('not_determined', 'restricted', 'denied', 'allowed', 'denied')):
            with self.subTest(value=value), patch.object(calendar, 'EKEventStore') as store:
                store.authorizationStatusForEntityType_.return_value = value
                self.assertEqual(calendar.status(), expected)

    def test_events_returns_timed_events(self):
        from harness_discussion.mac import calendar
        from harness_discussion.meetings import Event
        from unittest.mock import MagicMock

        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        event = MagicMock()
        event.isAllDay.return_value = False
        event.title.return_value = 'Meeting'
        event.startDate.return_value.timeIntervalSince1970.return_value = start.timestamp()
        event.endDate.return_value.timeIntervalSince1970.return_value = start.timestamp() + 3600
        all_day = MagicMock()
        all_day.isAllDay.return_value = True
        with patch.object(calendar, 'EKEventStore') as cls:
            store = cls.alloc.return_value.init.return_value
            store.eventsMatchingPredicate_.return_value = [event, all_day]
            result = calendar.events(start, start)
        self.assertEqual(result, [Event('Meeting', start, datetime.fromtimestamp(start.timestamp() + 3600, timezone.utc), 'eventkit')])
