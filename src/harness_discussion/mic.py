"""Records the microphone for dictation: 24 kHz mono 16-bit, at most 60 s, returned as WAV bytes in memory."""
import io
import logging
import math
import threading
import wave
from array import array

from . import stt

RATE = stt.RATE
MAX_SECONDS = stt.MAX_SECONDS
TAP_S = 0.3  # a shorter hold is a tap, which cancels
log = logging.getLogger("harness_discussion")


def input_devices():
    """Stable sounddevice queries, without Windows' duplicate entries from other host APIs."""
    import sounddevice
    try:
        host = sounddevice.default.hostapi
        api = sounddevice.query_hostapis(host)["name"]
        return [f"{d['name']}, {api}" for d in sounddevice.query_devices()
                if d["hostapi"] == host and d["max_input_channels"] > 0]
    except (OSError, sounddevice.PortAudioError):
        return []


def _input_device(name):
    """Resolve at each hold. Sounddevice's substring matching can select a different microphone."""
    if not name:
        return None
    import sounddevice
    apis = sounddevice.query_hostapis()
    matches = [i for i, d in enumerate(sounddevice.query_devices()) if d["max_input_channels"] > 0
               and f"{d['name']}, {apis[d['hostapi']]['name']}" == name]
    if len(matches) != 1:
        raise ValueError("The selected microphone is unavailable or ambiguous.")
    return matches[0]


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
    def __init__(self, stream_factory=None, device=""):
        self._factory = stream_factory
        self.device = device
        self._chunks, self._size, self._lock, self._stream = [], 0, threading.Lock(), None
        self._power, self._samples = 0, 0

    def start(self):
        factory = self._factory
        if factory is None:
            import sounddevice
            factory = lambda cb: sounddevice.RawInputStream(
                samplerate=RATE, channels=1, dtype="int16", callback=cb, device=_input_device(self.device))
        self._chunks, self._size = [], 0
        self._power, self._samples = 0, 0
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
        if self._factory is None:
            log.info("dictation microphone: %s", self.device or "System default")

    def _callback(self, data, frames, time_info, status):
        pcm = bytes(data)  # PortAudio passes a CFFI buffer, not Python bytes.
        samples = array("h", pcm)
        power = sum(s * s for s in samples)
        with self._lock:
            self._power += power
            self._samples += len(samples)
            room = MAX_SECONDS * RATE * 2 - self._size
            if room > 0:
                chunk = pcm[:room]
                self._chunks.append(chunk)
                self._size += len(chunk)

    def level(self):
        """Recent RMS input on a 0–1 meter, with silence below -60 dBFS. Never stores audio separately."""
        with self._lock:
            power, count = self._power, self._samples
            self._power, self._samples = 0, 0
        rms = math.sqrt(power / count) / 32768 if count else 0
        return max(0.0, min(1.0, (20 * math.log10(rms) + 60) / 60)) if rms else 0.0

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
