"""Audio contract tests. No audio device is opened."""
import array
import json
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

import harness_discussion.meeting_audio as audio

FIXTURES = Path(__file__).parent / "fixtures" / "meetings"


class AudioTest(unittest.TestCase):
    def test_track_header_sizes_and_idempotent_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "track.wav"
            track = audio.TrackFile(path)
            track.append(b"\x01\x00" * 100)
            self.assertEqual(track.frames, 100)
            track.close()
            track.close()
            data = path.read_bytes()
            self.assertEqual(struct.unpack_from("<I", data, 4)[0], 236)
            self.assertEqual(struct.unpack_from("<I", data, 40)[0], 200)
            with wave.open(str(path), "rb") as wav:
                self.assertEqual(wav.getnframes(), 100)

    def test_read_unpatched_and_fix_odd_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "track.wav"
            track = audio.TrackFile(path)
            pcm = b"\x01\x00" * 100
            track.append(pcm)
            track.flush()
            self.assertEqual(audio.read_pcm(path, 0, 1), pcm)
            with path.open("ab") as f:
                f.write(b"x")
            self.assertEqual(audio.fix_header(path), 100)
            with wave.open(str(path), "rb") as wav:
                self.assertEqual(wav.getnframes(), 100)
                self.assertEqual(wav.readframes(100), pcm)
            self.assertEqual(path.stat().st_size, 244)

    def test_read_pcm_clamps_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "track.wav"
            track = audio.TrackFile(path)
            pcm = b"\x02\x00" * 16000
            track.append(pcm)
            track.close()
            self.assertEqual(audio.read_pcm(path, -1, 2), pcm)
            self.assertEqual(audio.read_pcm(path, 2, 3), b"")

    def test_resample_24k_mono(self):
        out = audio.Resampler(24000, 1, "s16").feed(b"\x00\x00" * 24000)
        self.assertLessEqual(abs(len(out) // 2 - 16000), 32)

    def test_resample_48k_planar_stereo_preserves_rms(self):
        plane = array.array("f", (0.5 * math.sin(2 * math.pi * 440 * i / 48000) for i in range(48000))).tobytes()
        out = audio.Resampler(48000, 2, "fltp").feed(plane + plane)
        self.assertLessEqual(abs(len(out) // 2 - 16000), 32)
        self.assertAlmostEqual(audio.rms_dbfs(out), 20 * math.log10(0.5 / math.sqrt(2)), delta=1)

    def test_silence_and_fixture_speech(self):
        self.assertTrue(audio.is_silent(bytes(16000 * 2)))
        self.assertFalse(audio.is_silent(audio.read_pcm(FIXTURES / "them.wav", 0, 20)))

    def test_chunk_fixture_covers_timeline_and_avoids_line_starts(self):
        chunker = audio.Chunker()
        pcm = audio.read_pcm(FIXTURES / "them.wav", 0, 20)
        parts = chunker.feed(pcm) + chunker.finish()
        self.assertEqual(parts[0][0], 0)
        self.assertAlmostEqual(parts[-1][1], 20, places=2)
        starts = [entry["at"] for entry in json.loads((FIXTURES / "script.json").read_text(encoding="utf-8"))]
        for i, (start, end) in enumerate(parts):
            self.assertLessEqual(end - start, 30)
            if i:
                self.assertEqual(start, parts[i - 1][1])
                self.assertTrue(all(abs(start - line) >= 0.2 for line in starts))

    def test_constant_tone_cuts_at_max(self):
        chunker = audio.Chunker()
        tone = struct.pack("<h", 10000) * (70 * 16000)
        parts = chunker.feed(tone) + chunker.finish()
        self.assertEqual(parts, [(0, 30), (30, 60), (60, 70)])
