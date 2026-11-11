"""Streaming 16 kHz mono PCM storage and pause-based chunking."""
import array
import math
import os
import struct
from pathlib import Path

import av

RATE = 16000
HEADER = 44


def _header(size):
    return struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 36 + size, b'WAVE', b'fmt ', 16,
                       1, 1, RATE, RATE * 2, 2, 16, b'data', size)


def _open_track(path, flags):
    if os.name != 'nt':
        return os.open(path, flags, 0o666)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    create = ctypes.WinDLL('kernel32', use_last_error=True).CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    handle = create(os.fsdecode(path), 0x40000000, 7, None, 1, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return msvcrt.open_osfhandle(handle, flags | os.O_BINARY)


class TrackFile:
    def __init__(self, path: Path):
        self._file = open(path, 'xb', opener=_open_track)
        self._file.write(_header(0))
        self.frames = 0

    def append(self, pcm: bytes) -> None:
        if len(pcm) % 2:
            raise ValueError('PCM must contain whole samples')
        self._file.write(pcm)
        self.frames += len(pcm) // 2

    def flush(self) -> None:
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        if not self._file.closed:
            self.flush()
            self._file.seek(0)
            self._file.write(_header(self.frames * 2))
            self.flush()
            self._file.close()


def fix_header(path: Path) -> int:
    with open(path, 'r+b') as f:
        size = max(0, os.fstat(f.fileno()).st_size - HEADER) & ~1
        f.truncate(HEADER + size)
        f.seek(0)
        f.write(_header(size))
        f.flush()
        os.fsync(f.fileno())
    return size // 2


def read_pcm(path: Path, start_s: float, end_s: float) -> bytes:
    with open(path, 'rb') as f:
        frames = max(0, os.fstat(f.fileno()).st_size - HEADER) // 2
        start = min(frames, max(0, int(start_s * RATE)))
        end = min(frames, max(start, int(end_s * RATE)))
        f.seek(HEADER + start * 2)
        return f.read((end - start) * 2)


class Resampler:
    def __init__(self, rate: int, channels: int, fmt: str):
        self.rate, self.channels, self.fmt = rate, channels, fmt
        self.layout = 'mono' if channels == 1 else 'stereo'
        self._resampler = av.AudioResampler(format='s16', layout='mono', rate=RATE)

    def feed(self, data: bytes) -> bytes:
        width = 2 if self.fmt == 's16' else 4
        samples = len(data) // (width * self.channels)
        if not samples:
            return b''
        frame = av.AudioFrame(format=self.fmt, layout=self.layout, samples=samples)
        frame.sample_rate = self.rate
        plane_size = samples * width if self.fmt == 'fltp' else samples * width * self.channels
        for i, plane in enumerate(frame.planes):
            block = data[i * plane_size:(i + 1) * plane_size]
            plane.update(block + bytes(plane.buffer_size - len(block)))
        return b''.join(bytes(frame.planes[0])[:frame.samples * 2]
                        for frame in self._resampler.resample(frame))


def rms_dbfs(pcm: bytes) -> float:
    samples = array.array('h')
    samples.frombytes(pcm[:len(pcm) & ~1])
    if not samples:
        return float('-inf')
    mean = sum(sample * sample for sample in samples) / len(samples)
    return 10 * math.log10(mean / (32768 ** 2)) if mean else float('-inf')


def is_silent(pcm: bytes) -> bool:
    window = RATE * 2 // 10
    return sum(rms_dbfs(pcm[i:i + window]) > Chunker.SPEECH_DBFS
               for i in range(0, len(pcm), window)) < 3


class Chunker:
    MIN_S, MAX_S, PAUSE_S, SPEECH_DBFS = 10.0, 30.0, 0.4, -45.0

    def __init__(self, start_s: float = 0.0):
        self._start = self._position = round(start_s * RATE)
        self._pending = b''
        self._pause = 0

    def feed(self, pcm: bytes) -> list[tuple[float, float]]:
        self._pending += pcm
        window = RATE * 2 // 10
        result = []
        while len(self._pending) >= window:
            block, self._pending = self._pending[:window], self._pending[window:]
            self._position += window // 2
            self._pause = self._pause + 1 if rms_dbfs(block) < self.SPEECH_DBFS else 0
            cut = None
            if self._position - self._start >= self.MAX_S * RATE:
                cut = self._start + int(self.MAX_S * RATE)
            elif (self._position - self._start >= self.MIN_S * RATE
                  and self._pause >= round(self.PAUSE_S * 10)):
                cut = self._position - self._pause * (window // 2) // 2
                if cut - self._start < self.MIN_S * RATE:
                    cut = None
            if cut is not None:
                result.append((self._start / RATE, cut / RATE))
                self._start = cut
                self._pause = 0
        return result

    def finish(self) -> list[tuple[float, float]]:
        self._position += len(self._pending) // 2
        self._pending = b''
        if self._position - self._start < RATE // 2:
            return []
        result = [(self._start / RATE, self._position / RATE)]
        self._start = self._position
        return result
