"""ScreenCaptureKit audio capture; start and stop never block the AppKit run loop."""
import logging
import os
import threading

import CoreMedia as C
import Foundation as F
import Quartz
import ScreenCaptureKit as S

from harness_discussion.meeting_audio import Resampler

log = logging.getLogger(__name__)

SETTINGS_URL = 'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture'


def granted():
    return Quartz.CGPreflightScreenCaptureAccess()


def request():
    return Quartz.CGRequestScreenCaptureAccess()


def _planes(sample_buffer):
    block = C.CMSampleBufferGetDataBuffer(sample_buffer)
    length = C.CMBlockBufferGetDataLength(block)
    status, data = C.CMBlockBufferCopyDataBytes(block, 0, length, None)
    if status:
        raise RuntimeError(f'CMBlockBufferCopyDataBytes: {status}')
    if length % 8:
        raise ValueError('Invalid stereo float buffer length')
    return [data[:length // 2], data[length // 2:]]


class _Output(F.NSObject):
    def initWithOwner_(self, owner):
        self = self.init()
        if self is not None:
            self.owner = owner
        return self

    def stream_didOutputSampleBuffer_ofType_(self, stream, sample, kind):
        if kind == S.SCStreamOutputTypeAudio:
            self.owner._audio(sample)

    def stream_didStopWithError_(self, stream, error):
        self.owner._error(error)


class SystemAudio:
    def __init__(self, on_pcm, on_error):
        self.on_pcm, self.on_error = on_pcm, on_error
        self._lock = threading.RLock()
        self._active = False
        self._stream = None
        self._output = None
        self._resampler = None

    def start(self):
        with self._lock:
            if self._active:
                return
            self._active = True
            self._resampler = Resampler(48000, 2, 'fltp')
        try:
            S.SCShareableContent.getShareableContentWithCompletionHandler_(self._content)
        except Exception as error:
            self._error(error)

    def _content(self, content, error):
        with self._lock:
            if not self._active:
                return
            try:
                if error is not None:
                    raise RuntimeError(str(error))
                displays = content.displays() if content is not None else []
                if not displays:
                    raise RuntimeError('No shareable display')
                apps = [app for app in content.applications() if app.processID() == os.getpid()]
                # ponytail: unbundled Python may be absent, verify exclusion in the packaged app at T20.
                if not apps:
                    log.debug('Current process is absent from shareable applications')
                filt = S.SCContentFilter.alloc().initWithDisplay_excludingApplications_exceptingWindows_(displays[0], apps, [])
                config = S.SCStreamConfiguration.alloc().init()
                config.setCapturesAudio_(True)
                config.setExcludesCurrentProcessAudio_(True)
                config.setSampleRate_(48000)
                config.setChannelCount_(2)
                config.setWidth_(2)
                config.setHeight_(2)
                config.setMinimumFrameInterval_(C.CMTimeMake(1, 1))
                self._output = _Output.alloc().initWithOwner_(self)
                self._stream = S.SCStream.alloc().initWithFilter_configuration_delegate_(filt, config, self._output)
                ok, err = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(self._output, S.SCStreamOutputTypeAudio, None, None)
                if not ok:
                    raise RuntimeError(str(err))
                self._stream.startCaptureWithCompletionHandler_(self._started)
            except Exception as exc:
                self._error(exc)

    def _started(self, error):
        if error is not None:
            self._error(error)

    def _audio(self, sample):
        with self._lock:
            if not self._active:
                return
            try:
                pcm = self._resampler.feed(b''.join(_planes(sample)))
                if pcm:
                    self.on_pcm(pcm)
            except Exception as error:
                self._error(error)

    def _error(self, error):
        with self._lock:
            if not self._active:
                return
            self._active = False
            self.on_error(str(error))

    def stop(self):
        with self._lock:
            self._active = False
            stream, self._stream = self._stream, None
            if stream is not None:
                stream.stopCaptureWithCompletionHandler_(lambda error: None)
