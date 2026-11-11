import sys
import unittest
from unittest import mock

if sys.platform != 'darwin':
    raise unittest.SkipTest('macOS only')

import AppKit
from harness_discussion import meeting_copy, settings
from harness_discussion.mac import calendar, meetings_page


class Page(meetings_page.MeetingsPage):
    def __init__(self):
        self.cfg = dict(settings.DEFAULTS)
        self.app = mock.Mock()
        self.app.cfg = self.cfg
        self.app.calendar_status = meeting_copy.CALENDAR_NOT_SET
        self.app.host.calendar.return_value.status.return_value = 'allowed'
        self.app.host.system_audio_status.return_value = 'needs_permission'
        self._meetings_page()

    def _set(self, key, value):
        self.cfg[key] = value


class MeetingsPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        AppKit.NSApplication.sharedApplication()

    def setUp(self):
        self.page = Page()

    def test_consent(self):
        self.page._meetings_toggled(True)
        self.page.app.set_meetings.assert_called_once_with(True)

    def test_url_and_test(self):
        page = self.page
        page.url_hidden.setStringValue_('https://example.invalid/private')
        self.assertIsInstance(page.url_hidden, AppKit.NSSecureTextField)
        self.assertTrue(page.url_visible.isHidden())
        page._show_url(True)
        self.assertEqual(page.url_visible.stringValue(), 'https://example.invalid/private')
        self.assertFalse(page.url_visible.isHidden())
        page.url_show.setState_(1)
        with mock.patch.object(meetings_page.ui, 'timer'):
            page._test_calendar()
        page.app.refresh_meeting_calendar.assert_called_once_with()
        page.app.calendar_status = '● 3 events today'
        page._refresh_calendar_status()
        self.assertEqual(page.calendar_status_label.stringValue(), '● 3 events today')

    def test_storage_and_audio(self):
        self.assertEqual(self.page.storage_label.stringValue(), '0 meetings · 0 MB')
        self.page.audio_fix.performClick_(None)
        self.page.app.host.screen_permission_guide.assert_called_once_with()

    def test_eventkit_statuses(self):
        expected = ['Not allowed', 'Restricted', 'Denied', 'Connected', 'Denied']
        for raw, label in enumerate(expected):
            with self.subTest(raw=raw), mock.patch.object(calendar, 'EKEventStore') as store:
                store.authorizationStatusForEntityType_.return_value = raw
                self.page.app.host.calendar.return_value.status.return_value = calendar.status()
                self.page._refresh_meetings()
                self.assertEqual(self.page.eventkit_status.stringValue(), label)


if __name__ == '__main__':
    unittest.main()
