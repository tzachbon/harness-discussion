"""ScreenCaptureKit buffer and lifecycle tests."""
import sys
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(sys.platform == 'darwin', 'macOS only')
class SystemAudioTests(unittest.TestCase):
    def test_planes_and_resampler(self):
        from harness_discussion.mac import system_audio as audio
        data = bytes(range(32))
        with patch.object(audio.C, 'CMSampleBufferGetDataBuffer', return_value='block'), \
             patch.object(audio.C, 'CMBlockBufferGetDataLength', return_value=len(data)), \
             patch.object(audio.C, 'CMBlockBufferCopyDataBytes', return_value=(0, data)):
            self.assertEqual(audio._planes('sample'), [data[:16], data[16:]])
        capture = audio.SystemAudio(Mock(), Mock())
        capture._active = True
        capture._resampler = Mock()
        capture._resampler.feed.return_value = b'pcm'
        with patch.object(audio, '_planes', return_value=[b'left', b'right']):
            capture._audio('sample')
        capture._resampler.feed.assert_called_once_with(b'leftright')
        capture.on_pcm.assert_called_once_with(b'pcm')

    def test_start_does_not_wait_and_stop_before_content(self):
        from harness_discussion.mac import system_audio as audio
        capture = audio.SystemAudio(Mock(), Mock())
        with patch.object(audio, 'S') as framework:
            capture.start()
            callback = framework.SCShareableContent.getShareableContentWithCompletionHandler_.call_args.args[0]
            capture.stop()
            callback(None, None)
        capture.on_error.assert_not_called()

    def test_content_excludes_current_application(self):
        from harness_discussion.mac import system_audio as audio
        capture = audio.SystemAudio(Mock(), Mock())
        capture._active = True
        own, other = Mock(), Mock()
        own.processID.return_value = 123
        other.processID.return_value = 456
        content = Mock()
        content.displays.return_value = ['display']
        content.applications.return_value = [other, own]
        with patch.object(audio, 'S') as framework, patch.object(audio.os, 'getpid', return_value=123):
            framework.SCStream.alloc.return_value.initWithFilter_configuration_delegate_.return_value.addStreamOutput_type_sampleHandlerQueue_error_.return_value = (True, None)
            capture._content(content, None)
            framework.SCContentFilter.alloc.return_value.initWithDisplay_excludingApplications_exceptingWindows_.assert_called_once_with('display', [own], [])
            framework.SCStreamConfiguration.alloc.return_value.init.return_value.setExcludesCurrentProcessAudio_.assert_called_once_with(True)
        capture.on_error.assert_not_called()

    def test_missing_current_application_continues(self):
        from harness_discussion.mac import system_audio as audio
        capture = audio.SystemAudio(Mock(), Mock())
        capture._active = True
        content = Mock()
        content.displays.return_value = ['display']
        content.applications.return_value = []
        with patch.object(audio, 'S') as framework, self.assertLogs(audio.log, level='DEBUG'):
            framework.SCStream.alloc.return_value.initWithFilter_configuration_delegate_.return_value.addStreamOutput_type_sampleHandlerQueue_error_.return_value = (True, None)
            capture._content(content, None)
            framework.SCContentFilter.alloc.return_value.initWithDisplay_excludingApplications_exceptingWindows_.assert_called_once_with('display', [], [])
        capture.on_error.assert_not_called()
