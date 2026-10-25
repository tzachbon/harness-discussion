"""Dictation pieces with no microphone or network: WAV, the tap threshold, the 60 s cap, and the transcript chain."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness_discussion import audio, dictation, mic, stt, writer


class FakeStream:
    def __init__(self, cb):
        self.cb, self.started, self.closed = cb, False, False

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True


class MicTest(unittest.TestCase):
    def test_stale_input_refreshes_once_and_resolves_the_selected_device_again(self):
        selected = {"name": "HyperX", "hostapi": 0, "max_input_channels": 1}
        other = {**selected, "name": "Other mic"}
        stream = Mock()
        with patch("sounddevice.RawInputStream", side_effect=[audio.sd.PortAudioError("gone"), stream]) as opening, \
                patch("sounddevice.query_devices", side_effect=[[selected, other], [other, selected]]), \
                patch("sounddevice.query_hostapis", return_value=[{"name": "MME"}]), \
                patch("sounddevice._terminate") as terminate, patch("sounddevice._initialize") as initialize:
            rec = mic.Recorder(device="HyperX, MME")
            rec.start()
            self.addCleanup(rec.stop)
        self.assertEqual([call.kwargs["device"] for call in opening.call_args_list], [0, 1])
        terminate.assert_called_once_with()
        initialize.assert_called_once_with()
        rec.stop()

    def test_selected_microphone_reaches_the_native_stream(self):
        device = "Microphone (HyperX SoloCast 2), MME"
        devices = [{"name": "Wrong mic", "hostapi": 0, "max_input_channels": 1},
                   {"name": "Microphone (HyperX SoloCast 2)", "hostapi": 0, "max_input_channels": 2}]
        with patch("sounddevice.RawInputStream") as stream, patch(
                "sounddevice.query_devices", return_value=devices), patch(
                "sounddevice.query_hostapis", return_value=[{"name": "MME"}]):
            rec = mic.Recorder(device=device)
            rec.start()
            rec.stop()
        self.assertEqual(stream.call_args.kwargs["device"], 1)

    def test_missing_or_ambiguous_microphone_never_opens_another_input(self):
        exact = {"name": "HyperX", "hostapi": 0, "max_input_channels": 1}
        for devices in ([{**exact, "name": "2- HyperX"}], [exact, exact]):
            with self.subTest(devices=devices), patch("sounddevice.RawInputStream") as stream, patch(
                    "sounddevice.query_devices", return_value=devices), patch(
                    "sounddevice.query_hostapis", return_value=[{"name": "MME"}]), \
                    patch("sounddevice._terminate") as terminate, patch("sounddevice._initialize") as initialize:
                with self.assertRaisesRegex(ValueError, "selected microphone"):
                    mic.Recorder(device="HyperX, MME").start()
                stream.assert_not_called()
                terminate.assert_called_once_with()
                initialize.assert_called_once_with()

    def test_missing_selected_device_is_resolved_again_after_refresh(self):
        selected = {"name": "HyperX", "hostapi": 0, "max_input_channels": 1}
        with patch("sounddevice.RawInputStream") as opening, \
                patch("sounddevice.query_devices", side_effect=[[], [selected]]), \
                patch("sounddevice.query_hostapis", return_value=[{"name": "MME"}]), \
                patch("sounddevice._terminate"), patch("sounddevice._initialize"):
            rec = mic.Recorder(device="HyperX, MME")
            rec.start()
            self.addCleanup(rec.stop)
            self.assertEqual(opening.call_args.kwargs["device"], 0)
            opening.assert_called_once()
            rec.stop()

    def test_persistent_native_failure_retries_only_once(self):
        with patch("sounddevice.RawInputStream", side_effect=audio.sd.PortAudioError("gone")) as opening, \
                patch("sounddevice._terminate") as terminate, patch("sounddevice._initialize") as initialize:
            with self.assertRaises(audio.sd.PortAudioError):
                mic.Recorder().start()
        self.assertEqual(opening.call_count, 2)
        terminate.assert_called_once_with()
        initialize.assert_called_once_with()

    def test_failed_native_start_is_closed_before_refresh(self):
        failed, working = Mock(), Mock()
        failed.start.side_effect = audio.sd.PortAudioError("gone")
        with patch("sounddevice.RawInputStream", side_effect=[failed, working]), \
                patch("sounddevice._terminate") as terminate, patch("sounddevice._initialize"):
            terminate.side_effect = lambda: self.assertNotIn(failed, audio._live)
            rec = mic.Recorder()
            rec.start()
            self.addCleanup(rec.stop)
        failed.close.assert_called_once_with()
        rec.stop()

    def test_no_reset_if_failed_native_start_cannot_close(self):
        failed = Mock()
        failed.start.side_effect = audio.sd.PortAudioError("gone")
        failed.close.side_effect = RuntimeError("cannot close")
        self.addCleanup(audio._live.discard, failed)
        with patch("sounddevice.RawInputStream", return_value=failed) as opening, \
                patch("sounddevice._terminate") as terminate:
            with self.assertRaises(audio.sd.PortAudioError):
                mic.Recorder().start()
        self.assertIn(failed, audio._live)
        opening.assert_called_once()
        terminate.assert_not_called()

    def test_no_reset_while_another_microphone_is_open(self):
        first = mic.Recorder(lambda cb: Mock())
        first.start()
        self.addCleanup(first.stop)
        with patch("sounddevice.RawInputStream", side_effect=audio.sd.PortAudioError("gone")) as opening, \
                patch("sounddevice._terminate") as terminate:
            with self.assertRaises(audio.sd.PortAudioError):
                mic.Recorder().start()
        opening.assert_called_once()
        terminate.assert_not_called()

    def test_microphone_stays_registered_under_the_reset_lock_until_closed(self):
        stream, operations = Mock(), []
        stream.start.side_effect = lambda: operations.append(("start", audio._live_lock.locked(), stream in audio._live))
        stream.close.side_effect = lambda: operations.append(("close", audio._live_lock.locked(), stream in audio._live))
        rec = mic.Recorder(lambda cb: stream)
        rec.start()
        self.addCleanup(rec.stop)
        rec.stop()
        self.assertEqual(operations, [("start", True, True), ("close", True, True)])
        self.assertNotIn(stream, audio._live)

    def test_microphone_close_failure_keeps_blocking_resets(self):
        stream = Mock()
        stream.close.side_effect = RuntimeError("cannot close")
        self.addCleanup(audio._live.discard, stream)
        rec = mic.Recorder(lambda cb: stream)
        rec.start()
        with self.assertRaisesRegex(RuntimeError, "cannot close"):
            rec.stop()
        with patch("sounddevice.RawInputStream", side_effect=audio.sd.PortAudioError("gone")), \
                patch("sounddevice._terminate") as terminate:
            with self.assertRaises(audio.sd.PortAudioError):
                mic.Recorder().start()
        terminate.assert_not_called()
        self.assertIn(stream, audio._live)

    def test_device_menu_uses_input_names_in_the_default_host_api(self):
        devices = [{"name": "HyperX", "hostapi": 0, "max_input_channels": 2},
                   {"name": "Speakers", "hostapi": 0, "max_input_channels": 0},
                   {"name": "HyperX", "hostapi": 1, "max_input_channels": 2}]
        with patch("sounddevice.query_devices", return_value=devices), patch(
                "sounddevice.query_hostapis", return_value={"name": "MME"}), patch(
                "sounddevice.default", SimpleNamespace(hostapi=0)):
            self.assertEqual(mic.input_devices(), ["HyperX, MME"])

    def test_input_level_follows_audio_and_resets_after_each_read(self):
        rec = mic.Recorder(FakeStream)
        rec.start()
        rec._callback(b"\x00\x00" * 240, 240, None, None)
        self.assertEqual(rec.level(), 0.0)
        rec._callback(b"\x00\x20" * 240, 240, None, None)
        self.assertGreater(rec.level(), 0.5)
        self.assertEqual(rec.level(), 0.0)
        rec.stop()

    def test_native_cffi_buffer_is_recorded_and_measured(self):
        from cffi import FFI
        ffi = FFI()
        pcm = b"\x00\x20" * 240
        storage = ffi.new("char[]", pcm)
        rec = mic.Recorder(FakeStream)
        rec.start()
        rec._callback(ffi.buffer(storage, len(pcm)), 240, None, None)
        self.assertGreater(rec.level(), 0.5)
        self.assertEqual(stt.decode_audio(rec.stop()), pcm)

    def test_start_failure_closes_the_created_stream_and_can_be_retried(self):
        stream = Mock()
        stream.start.side_effect = RuntimeError("start failed")
        rec = mic.Recorder(lambda cb: stream)
        with self.assertRaisesRegex(RuntimeError, "start failed"):
            rec.start()
        stream.close.assert_called_once_with()
        self.assertIsNone(rec._stream)
        stream.start.side_effect = None
        rec.start()
        rec.stop()
        self.assertEqual(stream.close.call_count, 2)

    def test_stop_failure_still_closes_and_detaches_stream(self):
        stream = Mock()
        rec = mic.Recorder(lambda cb: stream)
        rec.start()
        stream.stop.side_effect = RuntimeError("stop failed")
        with self.assertRaisesRegex(RuntimeError, "stop failed"):
            rec.stop()
        stream.close.assert_called_once_with()
        self.assertIsNone(rec._stream)
        self.assertEqual(rec.stop(), b"")

    def test_the_wav_is_what_stt_decodes(self):
        pcm = (b"\x10\x00\xf0\xff" * 12000)
        self.assertEqual(stt.decode_audio(mic.wav(pcm)), pcm)

    def test_tap_threshold(self):
        self.assertTrue(mic.is_tap(0.29))
        self.assertFalse(mic.is_tap(0.3))

    def test_recording_stops_at_60_seconds(self):
        streams = []
        rec = mic.Recorder(lambda cb: streams.append(FakeStream(cb)) or streams[-1])
        rec.start()
        second = b"\x01\x00" * mic.RATE
        for _ in range(61):
            streams[0].cb(second, mic.RATE, None, None)
        audio = rec.stop()
        self.assertTrue(streams[0].closed)
        self.assertEqual(len(stt.decode_audio(audio)), mic.MAX_SECONDS * mic.RATE * 2)

    def test_nothing_recorded_is_empty(self):
        rec = mic.Recorder(FakeStream)
        rec.start()
        self.assertEqual(rec.stop(), b"")

    def test_duration_is_captured_audio_and_survives_stop(self):
        rec = mic.Recorder(FakeStream)
        rec.start()
        self.assertEqual(rec.duration, 0)
        rec._stream.cb(b"\x01\x00" * (mic.RATE // 2), mic.RATE // 2, None, None)
        self.assertEqual(rec.duration, 0.5)
        rec.stop()
        self.assertEqual(rec.duration, 0.5)


class Stt:
    def __init__(self, name, reply="hello", status="ready"):
        self.name, self.reply, self._status, self.calls = name, reply, status, 0

    def status(self):
        return self._status

    def transcribe(self, audio, language=""):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class ChainTest(unittest.TestCase):
    def test_cancel_before_status_does_not_touch_any_provider(self):
        source = Mock(name="source")
        source.name = "Codex"
        with self.assertRaises(writer.Cancelled):
            dictation.transcribe(b"x", [source], [["Codex", True]], cancelled=lambda: True)
        source.status.assert_not_called()
        source.transcribe.assert_not_called()

    def test_cancel_during_status_prevents_submission(self):
        cancelled = []
        source = Stt("Codex")
        source.status = lambda: cancelled.append(True) or "ready"
        with self.assertRaises(writer.Cancelled):
            dictation.transcribe(b"x", [source], [["Codex", True]], cancelled=lambda: bool(cancelled))
        self.assertEqual(source.calls, 0)

    def test_cancellation_on_error_empty_or_success_prevents_fallback(self):
        for result in (RuntimeError("private"), "", "words"):
            with self.subTest(result=type(result).__name__):
                cancelled = []
                source, fallback = Stt("Codex"), Stt("Cursor")
                def transcribe(audio, language=""):
                    cancelled.append(True)
                    if isinstance(result, Exception):
                        raise result
                    return result
                source.transcribe = transcribe
                with self.assertRaises(writer.Cancelled):
                    dictation.transcribe(b"x", [source, fallback], [["Codex", True], ["Cursor", True]],
                                         cancelled=lambda: bool(cancelled))
                self.assertEqual(fallback.calls, 0)

    def test_cancelled_exception_is_never_a_fallback(self):
        source, fallback = Stt("Codex", writer.Cancelled()), Stt("Cursor")
        with self.assertRaises(writer.Cancelled):
            dictation.transcribe(b"x", [source, fallback], [["Codex", True], ["Cursor", True]])
        self.assertEqual(fallback.calls, 0)

    def test_status_failure_uses_next_source_without_exposing_exception(self):
        source, fallback = Stt("Codex"), Stt("Cursor", "ok")
        source.status = Mock(side_effect=RuntimeError("private"))
        self.assertEqual(dictation.transcribe(b"x", [source, fallback], [["Codex", True], ["Cursor", True]]),
                         ("ok", "Cursor"))

    def test_codex_then_cursor_in_order(self):
        codex, cursor = Stt("Codex", RuntimeError("down")), Stt("Cursor", " hi there ")
        self.assertEqual(dictation.transcribe(b"x", [codex, cursor], [["Codex", True], ["Cursor", True]]),
                         ("hi there", "Cursor"))

    def test_disabled_and_not_ready_are_skipped(self):
        codex, cursor = Stt("Codex"), Stt("Cursor", status="signed_out")
        with self.assertRaises(dictation.Error):
            dictation.transcribe(b"x", [codex, cursor], [["Codex", False], ["Cursor", True]])
        self.assertEqual((codex.calls, cursor.calls), (0, 0))

    def test_an_empty_transcript_falls_through(self):
        self.assertEqual(dictation.transcribe(b"x", [Stt("Codex", "  "), Stt("Cursor", "ok")],
                                              [["Codex", True], ["Cursor", True]]), ("ok", "Cursor"))


if __name__ == "__main__":
    unittest.main()
