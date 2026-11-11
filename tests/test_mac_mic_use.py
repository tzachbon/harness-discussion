import os
import sys
import unittest
from unittest.mock import patch


@unittest.skipUnless(sys.platform == 'darwin', 'macOS only')
class MicUseTest(unittest.TestCase):
    def test_excludes_own_pid_and_normalizes(self):
        from harness_discussion.mac import mic_use
        with patch.object(mic_use, '_processes', return_value=[
            (os.getpid(), 'Own', 1), (123, 'Com.Example.Helper', 1), (456, 'Inactive', 0)
        ]):
            self.assertEqual(mic_use.users(), {'com.example.helper'})
            self.assertEqual(mic_use.users({123}), set())

    def test_missing_selector(self):
        from harness_discussion.mac import mic_use
        with patch.object(mic_use, '_processes', side_effect=OSError('prs#')):
            self.assertIsNone(mic_use.users())
