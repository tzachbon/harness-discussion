"""Dictation pieces with no microphone or network: WAV, the tap threshold, the 60 s cap, and the transcript chain."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness_discussion import dictation, mic, stt


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
                    "sounddevice.query_hostapis", return_value=[{"name": "MME"}]):
                with self.assertRaisesRegex(ValueError, "selected microphone"):
                    mic.Recorder(device="HyperX, MME").start()
                stream.assert_not_called()

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
