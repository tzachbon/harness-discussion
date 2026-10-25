"""Microphone availability without opening a user's recording device."""
import sys
import unittest
from unittest.mock import MagicMock, patch

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

from harness_discussion.win import microphone


class MicrophoneTest(unittest.TestCase):
    def test_any_global_or_desktop_denial_prevents_device_access(self):
        for denied in range(3):
            with self.subTest(denied=denied), patch.object(microphone.winreg, "OpenKey", MagicMock()), \
                    patch.object(microphone.winreg, "QueryValueEx", side_effect=[("Allow", 1)] * denied + [("Deny", 1)]), \
                    patch("sounddevice.query_devices") as devices:
                self.assertIn("Blocked", microphone.status())
                devices.assert_not_called()

    def test_missing_consent_is_not_denial_but_missing_input_is_unavailable(self):
        with patch.object(microphone.winreg, "OpenKey", side_effect=FileNotFoundError), \
                patch("sounddevice.query_devices", return_value={"max_input_channels": 1}) as devices:
            self.assertEqual(microphone.status(), "Available")
            devices.assert_called_once_with(kind="input")
            devices.side_effect = RuntimeError("private device details")
            self.assertEqual(microphone.status(), "No microphone available")

    def test_unreadable_consent_does_not_claim_availability(self):
        with patch.object(microphone.winreg, "OpenKey", side_effect=PermissionError), \
                patch("sounddevice.query_devices") as devices:
            self.assertIn("Could not check", microphone.status())
            devices.assert_not_called()
