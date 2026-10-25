import asyncio
import sys
from http.client import HTTPConnection
import io
import json
import os
import queue
import tempfile
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import wave

import av

from harness_discussion import stt, stt_server, writer
from harness_discussion.codex_rt import AppServer

from stt_helpers import FakePeer, multipart, receive, wav, words


class Module(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        log = patch.object(stt, "DIAG_LOG", Path(folder.name) / "diag.log")
        log.start()
        self.addCleanup(log.stop)

    def test_decode_and_limits(self):
        self.assertEqual(len(stt.decode_audio(wav())), 4800)
        for data in (b"", b"#EXTM3U\nhttp://localhost/private", b"RIFF0000WAVEbad", wav(61)):
            with self.assertRaises(ValueError):
                stt.decode_audio(data)

    def test_webm_input(self):
        output = io.BytesIO()
        with av.open(output, "w", format="webm") as media:
            stream = media.add_stream("libopus", rate=24000)
            stream.layout = "mono"
            frame = av.AudioFrame(format="s16", layout="mono", samples=2400)
            frame.planes[0].update(b"\0" * 4800)
            frame.sample_rate = 24000
            for packet in stream.encode(frame):
                media.mux(packet)
            for packet in stream.encode(None):
                media.mux(packet)
        self.assertGreater(len(stt.decode_audio(output.getvalue())), 0)

    def test_busy_does_not_decode(self):
        stt._busy.acquire()
        try:
            with patch.object(stt, "decode_audio") as decode, self.assertRaises(stt.BusyError):
                stt.transcribe(wav())
            decode.assert_not_called()
        finally:
            stt._busy.release()

    def test_cancel_before_or_during_lock_acquisition_never_decodes(self):
        for checks in ([True, True], [False, True]):
            with self.subTest(checks=checks), patch.object(stt, "decode_audio") as decode:
                with self.assertRaises(writer.Cancelled):
                    stt.transcribe(wav(), cancelled=MagicMock(side_effect=checks))
                decode.assert_not_called()
                self.assertFalse(stt._busy.locked())

    def test_backend_failure_reaps_and_releases(self):
        with patch.object(stt, "AppServer") as server, patch.object(stt, "_receive", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                stt.transcribe(wav())
            server.return_value.close.assert_called_once()
        self.assertFalse(stt._busy.locked())

    def test_failures_are_classified_and_logged_without_content(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(stt, "DIAG_LOG", Path(folder) / "diag.log"):
            with patch.object(stt, "AppServer", side_effect=RuntimeError("thread/start: sk-secret said hello")):
                with self.assertRaises(stt.SttError) as caught:
                    stt.transcribe(wav())
            self.assertEqual((caught.exception.code, caught.exception.stage), ("backend_start_failed", "backend_start"))
            with patch.object(stt, "AppServer") as server, patch.object(stt, "_receive", side_effect=TimeoutError("hello")):
                server.return_value.close.side_effect = OSError("teardown sk-secret")
                with self.assertRaises(stt.SttError) as caught:
                    stt.transcribe(wav())
            self.assertEqual(caught.exception.code, "completion_timeout")
            self.assertFalse(stt._busy.locked())
            records = [json.loads(line) for line in (Path(folder) / "diag.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["code"] for r in records], ["backend_start_failed", "completion_timeout"])
        self.assertEqual(records[1]["id"], caught.exception.request_id)
        self.assertEqual(records[1]["audio_ms"], 100)
        text = json.dumps(records)
        self.assertNotIn("secret", text)
        self.assertNotIn("hello", text)

    def test_peer_teardown_failure_keeps_primary_error(self):
        pc = MagicMock()
        pc.createOffer, pc.setLocalDescription = AsyncMock(), AsyncMock()
        pc.close = AsyncMock(side_effect=OSError("close failed"))
        srv = MagicMock()
        srv.call.side_effect = RuntimeError("start failed")
        diag = {"started": 0, "stage": "connect", "marks": {}, "events": {}}
        with patch.object(stt, "RTCPeerConnection", return_value=pc), self.assertRaises(stt.SttError) as caught:
            asyncio.run(stt._receive(srv, b"\0\0" * 480, "", "", diag))
        self.assertEqual(caught.exception.code, "realtime_start_failed")
        self.assertEqual(diag["peer_teardown_error"], "OSError")

    def test_rejected_calls_are_logged(self):
        with self.assertRaises(ValueError):
            stt.transcribe(wav(), prompt="x" * 3000)
        stt._busy.acquire()
        try:
            with self.assertRaises(stt.BusyError):
                stt.transcribe(wav())
        finally:
            stt._busy.release()
        records = [json.loads(line) for line in stt.DIAG_LOG.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["code"] for r in records], ["invalid_input", "busy"])
        self.assertFalse(stt._busy.locked())

    def test_diagnostics_rotate_and_never_fail(self):
        stt.DIAG_LOG.write_text("x" * (1 << 20 + 1), encoding="utf-8")
        stt._write_diag({"code": "ok"})
        self.assertEqual(stt.DIAG_LOG.read_text(encoding="utf-8"), '{"code": "ok"}\n')
        self.assertTrue(stt.DIAG_LOG.with_name(stt.DIAG_LOG.name + ".1").exists())
        with patch.object(stt, "DIAG_LOG", stt.DIAG_LOG.parent):  # a directory cannot be appended to
            stt._write_diag({"code": "ok"})

    def test_concurrent_rotation_keeps_every_record(self):
        stt.DIAG_LOG.write_text("x" * (2 << 20), encoding="utf-8")
        real_replace = os.replace
        def slow_replace(*args):
            time.sleep(0.05)
            real_replace(*args)
        with patch.object(stt.os, "replace", side_effect=slow_replace):
            writers = [threading.Thread(target=stt._write_diag, args=({"n": n},)) for n in range(3)]
            for writer in writers:
                writer.start()
            for writer in writers:
                writer.join(5)
        self.assertEqual(len(stt.DIAG_LOG.read_text(encoding="utf-8").splitlines()), 3)

    def test_track_sends_ahead_with_real_time_stamps(self):
        async def send():
            track = stt._AudioTrack(bytes(2) * stt.RATE)  # One second of audio.
            track.ready, started = True, time.monotonic()
            while not track.finished_at:
                frame = await track.recv()
            return track, frame, time.monotonic() - started
        track, frame, elapsed = asyncio.run(send())
        self.assertLess(elapsed, 0.9)  # Paced at 4x (0.25 s), not 1x (1 s).
        self.assertEqual((frame.pts, track.end_ms), (stt.RATE - 480, 1000))

    def test_settle_waits_for_late_words(self):
        # Measured worst case: last word 1.2 s after audio end, 1.1 s between words.
        self.assertFalse(stt._settled(since_audio_end=2.0, since_last_word=5.0))
        self.assertFalse(stt._settled(since_audio_end=3.0, since_last_word=1.5))
        self.assertTrue(stt._settled(since_audio_end=2.5, since_last_word=2.0))
        self.assertTrue(stt._settled(since_audio_end=3.0, since_last_word=None))
        self.assertTrue(stt._settled(since_audio_end=12.0, since_last_word=0.1))
        # Sending ahead of real time leaves Codex more audio to finish, so the cap grows with length.
        self.assertFalse(stt._settled(since_audio_end=12.0, since_last_word=0.1, audio_seconds=20))
        self.assertTrue(stt._settled(since_audio_end=27.0, since_last_word=0.1, audio_seconds=20))
        # No fragment yet, or Codex far behind: wait as long as a 1x send would have.
        self.assertFalse(stt._settled(since_audio_end=17.0, since_last_word=None, audio_seconds=20))
        self.assertTrue(stt._settled(since_audio_end=17.5, since_last_word=None, audio_seconds=20))
        self.assertFalse(stt._settled(since_audio_end=5.0, since_last_word=4.0, audio_seconds=20, unheard=8.0))
        self.assertTrue(stt._settled(since_audio_end=5.0, since_last_word=11.0, audio_seconds=20, unheard=8.0))
        # Near the end, the quiet gap covers the unheard audio plus clock skew.
        self.assertFalse(stt._settled(since_audio_end=3.0, since_last_word=2.9, audio_seconds=20, unheard=0.0))
        self.assertTrue(stt._settled(since_audio_end=3.0, since_last_word=3.0, audio_seconds=20, unheard=-0.6))
        self.assertFalse(stt._settled(since_audio_end=4.0, since_last_word=3.9, audio_seconds=20, unheard=1.0))
        # A position far past the audio end is implausible, so it is not trusted.
        self.assertFalse(stt._settled(since_audio_end=9.0, since_last_word=9.0, audio_seconds=20, unheard=-5.0))

    def test_late_word_after_skewed_position_is_kept(self):
        # Codex reports a position 0.6 s past the truth, then the last word arrives 2.9 s later.
        result, diag = receive([(0.1, words("first ", lambda t: t.end_ms - 500)),
                                (2.9, words("last", lambda t: t.end_ms))])
        self.assertEqual(result, "first last")
        self.assertEqual(diag["fragments"], 2)

    def test_invalid_positions_fail_and_keep_progress(self):
        result, diag = receive([(0.0, words("x", lambda t: 10 ** 400))], seconds=0.2)
        self.assertEqual((result, diag["heard_end_ms"]), ("x", 10 ** 7))  # Untrusted, so the slow path.
        for bad in (True, -1, "100"):
            result, diag = receive([(0.0, words("first ", lambda t: 100)), (0.0, words("x", lambda t: bad))], seconds=0.2)
            self.assertEqual(result.code, "invalid_event")
            self.assertEqual((diag["fragments"], diag["heard_end_ms"]), (1, 100))
            self.assertIsNotNone(diag["stream_end_ms"])

    def test_failed_init_reaps_process(self):
        with patch("harness_discussion.codex_rt.codex_exe", return_value="codex"), patch("harness_discussion.codex_rt.subprocess.Popen") as popen, patch("harness_discussion.codex_rt.subprocess.run"), patch("harness_discussion.codex_rt.threading.Thread"), patch.object(AppServer, "call", side_effect=RuntimeError("init failed")), patch("harness_discussion.codex_rt.os.killpg", create=True) as killpg:
            popen.return_value.poll.return_value = None
            with self.assertRaises(RuntimeError):
                AppServer()
            popen.return_value.kill.assert_called_once()
            popen.return_value.wait.assert_called_with(timeout=5)
            if sys.platform != "win32":  # the whole process group, so helpers holding stdout end too
                self.assertEqual(killpg.call_args_list[0].args[0], popen.return_value.pid)

    def test_malformed_duplicate_form_fields(self):
        body = multipart(wav(), model=stt.MODEL)
        body = body.replace(b"--probe--", b'--probe\r\nContent-Disposition: form-data; name="model"\r\n\r\nother\r\n--probe--')
        with self.assertRaises(ValueError):
            stt_server.parse_form("multipart/form-data; boundary=probe", body)


if __name__ == "__main__":
    unittest.main()
