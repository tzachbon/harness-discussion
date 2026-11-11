"""Capture the startup default speaker through Windows WASAPI loopback."""
import ctypes
import threading

from harness_discussion.meeting_audio import Resampler


class SystemAudio:
    def __init__(self, on_pcm, on_error):
        self._on_pcm = on_pcm
        self._on_error = on_error
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._capture, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _capture(self):
        initialized = False
        try:
            import soundcard as sc

            result = ctypes.windll.ole32.CoInitializeEx(None, 0)
            if result < 0:
                raise OSError(result, "CoInitializeEx failed")
            initialized = True
            resampler = Resampler(48000, 1, "flt")
            # ponytail: stay on the startup speaker until output switching is requested.
            mic = sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)
            with mic.recorder(samplerate=48000, channels=1) as recorder:
                while not self._stop.is_set():
                    pcm = resampler.feed(recorder.record(numframes=4800).tobytes())
                    if pcm:
                        self._on_pcm(pcm)
        except Exception as exc:
            self._on_error(type(exc).__name__)
        finally:
            if initialized:
                ctypes.windll.ole32.CoUninitialize()
