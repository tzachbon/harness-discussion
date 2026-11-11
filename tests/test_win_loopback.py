"""Windows loopback conversion and error reporting without audio hardware."""
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np

from harness_discussion.meeting_audio import rms_dbfs
from harness_discussion.win.loopback import SystemAudio


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class LoopbackTest(unittest.TestCase):
    def test_converts_frames_and_uses_startup_default(self):
        received = []
        ready = threading.Event()
        recorder = Mock()
        recorder.record.return_value = np.full((4800, 1), 0.25, dtype=np.float32)
        context = Mock()
        context.__enter__ = Mock(return_value=recorder)
        context.__exit__ = Mock(return_value=False)
        mic = Mock()
        mic.recorder.return_value = context
        soundcard = types.SimpleNamespace(default_speaker=Mock(return_value=types.SimpleNamespace(name="speaker")),
                                          get_microphone=Mock(return_value=mic))
        def on_pcm(pcm):
            received.append(pcm)
            ready.set()

        errors = Mock()
        with patch.dict(sys.modules, {"soundcard": soundcard}):
            capture = SystemAudio(on_pcm, errors)
            capture.start()
            self.assertTrue(ready.wait(5))
            capture.stop()
        soundcard.get_microphone.assert_called_once_with(id="speaker", include_loopback=True)
        mic.recorder.assert_called_once_with(samplerate=48000, channels=1)
        recorder.record.assert_called_with(numframes=4800)
        self.assertTrue(3000 <= len(received[0]) <= 3200)
        self.assertAlmostEqual(rms_dbfs(received[0]), -12.04, delta=0.2)
        errors.assert_not_called()

    def test_error_reports_only_type_once(self):
        soundcard = types.SimpleNamespace(default_speaker=Mock(side_effect=ValueError("private title")),
                                          get_microphone=Mock())
        errors = Mock()
        with patch.dict(sys.modules, {"soundcard": soundcard}):
            capture = SystemAudio(Mock(), errors)
            capture.start()
            capture.stop()
        errors.assert_called_once_with("ValueError")
