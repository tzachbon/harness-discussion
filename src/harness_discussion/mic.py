"""Records the microphone for dictation: 24 kHz mono 16-bit, at most 60 s, returned as WAV bytes in memory."""
import io
import threading
import wave

from . import stt

RATE = stt.RATE
MAX_SECONDS = stt.MAX_SECONDS
TAP_S = 0.3  # a shorter hold is a tap, which cancels


def wav(pcm: bytes, rate: int = RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


def is_tap(seconds: float) -> bool:
    return seconds < TAP_S


class Recorder:
    def __init__(self, stream_factory=None):
        self._factory = stream_factory
        self._chunks, self._size, self._lock, self._stream = [], 0, threading.Lock(), None

    def start(self):
        factory = self._factory
        if factory is None:
            import sounddevice
            factory = lambda cb: sounddevice.RawInputStream(
                samplerate=RATE, channels=1, dtype="int16", callback=cb)
        self._chunks, self._size = [], 0
        self._stream = factory(self._callback)
        try:
            self._stream.start()
        except BaseException:
            stream, self._stream = self._stream, None
            try:
                stream.close()
            except Exception:
                pass  # preserve the startup failure
            raise

    def _callback(self, data, frames, time_info, status):
        with self._lock:
            room = MAX_SECONDS * RATE * 2 - self._size
            if room > 0:
                chunk = bytes(data)[:room]
                self._chunks.append(chunk)
                self._size += len(chunk)

    def stop(self) -> bytes:
        """The recording as WAV, or b"" when nothing was recorded."""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        with self._lock:
            pcm = b"".join(self._chunks)
        return wav(pcm) if pcm else b""

    @property
    def duration(self):
        """Seconds actually captured, excluding permission, startup and device cleanup."""
        with self._lock:
            return self._size / (RATE * 2)
