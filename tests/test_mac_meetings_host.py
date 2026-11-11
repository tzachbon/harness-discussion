import sys
import unittest
from unittest import mock

if sys.platform != "darwin":
    raise unittest.SkipTest("macOS only")

from harness_discussion.mac import host, mic_use, system_audio


class MeetingHostTest(unittest.TestCase):
    def test_capture_adapters(self):
        with mock.patch.object(mic_use, "users", return_value=None):
            self.assertIsNone(host.MacHost.mic_users(None))
        with mock.patch.object(system_audio, "SystemAudio") as capture:
            self.assertIs(host.MacHost.system_audio(None, 1, 2), capture.return_value)
            capture.assert_called_once_with(1, 2)

    def test_system_audio_status(self):
        import Quartz
        with mock.patch.object(Quartz, "CGPreflightScreenCaptureAccess", return_value=False):
            self.assertEqual(host.MacHost.system_audio_status(None), "needs_permission")
        with mock.patch.object(Quartz, "CGPreflightScreenCaptureAccess", return_value=True):
            self.assertEqual(host.MacHost.system_audio_status(None), "ready")

    def test_state_refresh(self):
        subject = mock.Mock()
        subject._meeting_label = None
        with mock.patch.object(host.AppKit, "NSAccessibilityPostNotificationWithUserInfo"):
            host.MacHost.set_meeting_state(subject, "recording")
        self.assertEqual(subject._meeting_label, "recording")
        subject.refresh_menu.assert_called_once_with()
