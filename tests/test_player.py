import array
import threading
import unittest
from unittest import mock

from harness_discussion import audio, mic
from harness_discussion.audio import PcmPlayer


def opens(*results):
    """RawOutputStream for tests: each call raises or returns the next result, whose IO has already stopped."""
    results = iter(results)

    def open_stream(**kw):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        kw["finished_callback"]()
        return result
    return open_stream


class Player(unittest.TestCase):
    def test_no_playback_reset_while_a_microphone_is_open(self):
        stream = mock.Mock()
        rec = mic.Recorder(lambda cb: stream)
        rec.start()
        self.addCleanup(rec.stop)
        with mock.patch.object(audio.sd, "RawOutputStream", side_effect=audio.sd.PortAudioError("gone")), \
                mock.patch.object(audio.sd, "_terminate") as terminate:
            with self.assertRaises(audio.sd.PortAudioError):
                PcmPlayer()
        terminate.assert_not_called()
        stream.close.assert_not_called()

    def test_no_microphone_reset_while_a_player_is_open(self):
        stream = mock.Mock()
        with mock.patch.object(audio.sd, "RawOutputStream", side_effect=opens(stream)):
            player = PcmPlayer()
        self.addCleanup(player.close)
        with mock.patch.object(audio.sd, "RawInputStream", side_effect=audio.sd.PortAudioError("gone")), \
                mock.patch.object(audio.sd, "_terminate") as terminate:
            with self.assertRaises(audio.sd.PortAudioError):
                mic.Recorder().start()
        terminate.assert_not_called()
        stream.close.assert_not_called()

    def test_unconfirmed_player_close_blocks_microphone_recovery(self):
        for fail_start in (False, True):
            with self.subTest(fail_start=fail_start):
                stream = mock.Mock()
                stream.close.side_effect = audio.sd.PortAudioError("cannot close")
                self.addCleanup(audio._live.discard, stream)
                if fail_start:
                    stream.start.side_effect = audio.sd.PortAudioError("cannot start")
                with mock.patch.object(audio.sd, "RawOutputStream", side_effect=opens(stream)), \
                        mock.patch.object(audio.sd, "_terminate") as terminate:
                    with self.assertRaises(audio.sd.PortAudioError):
                        PcmPlayer().close()
                terminate.assert_not_called()
                with mock.patch.object(audio.sd, "RawInputStream", side_effect=audio.sd.PortAudioError("gone")), \
                        mock.patch.object(audio.sd, "_terminate") as terminate:
                    with self.assertRaises(audio.sd.PortAudioError):
                        mic.Recorder().start()
                terminate.assert_not_called()
                self.assertIn(stream, audio._live)
                audio._live.discard(stream)

    def test_a_stale_device_list_is_refreshed_once(self):
        stream = mock.Mock()
        with mock.patch.object(audio.sd, "RawOutputStream", side_effect=opens(audio.sd.PortAudioError("gone"), stream)), \
                mock.patch.object(audio.sd, "_terminate") as terminate, \
                mock.patch.object(audio.sd, "_initialize") as initialize:
            p = PcmPlayer()
        self.assertIs(p._stream, stream)
        stream.start.assert_called_once()
        terminate.assert_called_once()
        initialize.assert_called_once()
        p.close()

    def test_no_reset_while_another_player_is_open_and_a_failed_start_is_closed(self):
        playing, broken = mock.Mock(), mock.Mock()
        broken.start.side_effect = audio.sd.PortAudioError("gone")
        with mock.patch.object(audio.sd, "RawOutputStream", side_effect=opens(playing, broken)), \
                mock.patch.object(audio.sd, "_terminate") as terminate:
            first = PcmPlayer()
            with self.assertRaises(audio.sd.PortAudioError):
                PcmPlayer()
        broken.close.assert_called_once()
        terminate.assert_not_called()
        playing.close.assert_not_called()
        first.close()

    def test_a_stream_stays_listed_until_it_is_closed(self):
        stream, listed = mock.Mock(), []
        stream.close.side_effect = lambda: listed.append(stream in audio._live)
        with mock.patch.object(audio.sd, "RawOutputStream", side_effect=opens(stream)):
            PcmPlayer().close()
        self.assertEqual((listed, stream in audio._live), ([True], False))

    def test_a_stream_opens_and_is_listed_under_the_reset_lock(self):
        stream, locked = mock.Mock(), []
        stream.start.side_effect = lambda: locked.append(audio._live_lock.locked())
        with mock.patch.object(audio.sd, "RawOutputStream", side_effect=opens(stream)):
            p = PcmPlayer()
        self.assertEqual((locked, stream in audio._live), ([True], True))
        p.close()

    def test_close_lets_the_callback_stop_the_stream_and_waits_for_the_stop_to_finish(self):
        stream, opened = mock.Mock(active=True), {}
        def open_stream(**kw):
            opened.update(kw)
            return stream
        with mock.patch.object(audio.sd, "RawOutputStream", side_effect=open_stream):
            p = PcmPlayer()
        def io_thread():
            while not p._closing:
                pass
            with self.assertRaises(audio.sd.CallbackAbort):
                p._fill(bytearray(8), 4, None, None)
            stream.active = False  # PortAudio reports inactive before its stop notification ends
            stream.close.assert_not_called()
            opened["finished_callback"]()
        io = threading.Thread(target=io_thread, daemon=True)
        io.start()
        p.close()
        io.join()
        stream.abort.assert_not_called()
        stream.close.assert_called_once()
        self.assertNotIn(stream, audio._live)

    def test_a_stream_that_never_finishes_stopping_is_left_open_and_listed(self):
        stream = mock.Mock(active=False)  # inactive, but the IO thread has not finished its stop notification
        with mock.patch.object(audio.sd, "RawOutputStream", return_value=stream), \
                mock.patch.object(audio, "CLOSE_WAIT_S", 0.05):
            PcmPlayer().close()
        stream.abort.assert_not_called()
        stream.close.assert_not_called()
        self.assertIn(stream, audio._live)  # so a PortAudio reset cannot close it either
        audio._live.discard(stream)

    def test_a_closing_player_writes_silence_and_aborts_even_when_paused(self):
        p = PcmPlayer.__new__(PcmPlayer)
        p._buf, p._lock, p._width, p._paused = bytearray(b"\x01\x02" * 8), threading.Lock(), 2, threading.Event()
        p._paused.set()
        p._closing, out = True, bytearray(b"\xff" * 8)
        with self.assertRaises(audio.sd.CallbackAbort):
            p._fill(out, 4, None, None)
        self.assertEqual(bytes(out), bytes(8))

    def test_drain_waits_until_the_output_start_notification_has_been_sent(self):
        p = PcmPlayer.__new__(PcmPlayer)
        p._buf, p._lock, p._width, p._paused = bytearray(), threading.Lock(), 2, threading.Event()
        pending_when_output_is_filled, heard = [], []

        class Output(bytearray):
            def __setitem__(self, key, value):
                pending_when_output_is_filled.append(p.pending)
                super().__setitem__(key, value)

        p.arm(lambda: heard.append(1))
        p.write(array.array("h", [1000] * 4).tobytes())
        p._fill(Output(8), 4, None, None)
        self.assertTrue(all(pending_when_output_is_filled))
        self.assertEqual((p.pending, heard), (False, [1]))

    def test_start_waits_for_voiced_unpaused_output_and_fires_once(self):
        p = PcmPlayer.__new__(PcmPlayer)
        p._buf, p._lock, p._width, p._paused = bytearray(), threading.Lock(), 2, threading.Event()
        heard, out = [], bytearray(8)
        p.arm(lambda: heard.append(1))
        p.write(bytes(8))  # warmup silence is not speech
        p._fill(out, 4, None, None)
        self.assertFalse(heard)
        p._paused.set()
        p.write(array.array("h", [1000] * 8).tobytes())
        p._fill(out, 4, None, None)
        self.assertEqual((heard, bytes(out), len(p._buf)), ([], bytes(8), 16))
        p._paused.clear()
        p._fill(out, 4, None, None)
        p._fill(out, 4, None, None)
        self.assertEqual(heard, [1])

    def test_paused_player_plays_silence_and_keeps_the_queue(self):
        paused = threading.Event()
        p = PcmPlayer.__new__(PcmPlayer)  # no sound device: drive the callback directly
        p._buf, p._lock, p._width, p._paused = bytearray(b"\x01\x02" * 8), threading.Lock(), 2, paused
        p._on_audio = None
        out = bytearray(8)
        paused.set()
        p._fill(out, 4, None, None)
        self.assertEqual((bytes(out), len(p._buf)), (bytes(8), 16))
        paused.clear()
        p._fill(out, 4, None, None)
        self.assertEqual((bytes(out), len(p._buf)), (b"\x01\x02" * 4, 8))


def _pcm(level, samples=4):
    return array.array("h", [level] * samples).tobytes()


class JitterCushion(unittest.TestCase):
    def player(self, hold, holding=False):
        p = PcmPlayer.__new__(PcmPlayer)  # no sound device: drive the callback directly
        p._buf, p._lock, p._width, p._paused = bytearray(), threading.Lock(), 2, threading.Event()
        p._on_audio, p._hold, p._holding = None, hold, holding
        return p

    def at(self, now, call, *args):
        with mock.patch.object(audio.time, "monotonic", return_value=now):
            return call(*args)

    def fill(self, p, now):
        out = bytearray(8)
        self.at(now, p._fill, out, 4, None, None)
        return bytes(out)

    def test_waits_for_a_cushion_then_plays(self):
        p = self.player(16, holding=True)
        self.at(10.0, p.write, _pcm(1000))
        self.assertEqual(self.fill(p, 10.01), bytes(8))
        self.at(10.02, p.write, _pcm(1000))
        self.assertEqual(self.fill(p, 10.03), _pcm(1000))

    def test_a_callback_before_the_first_write_keeps_the_cushion(self):
        p = self.player(16, holding=True)
        self.assertEqual(self.fill(p, 10.0), bytes(8))
        self.at(10.01, p.write, _pcm(1000))
        self.assertEqual(self.fill(p, 10.02), bytes(8))

    def test_plays_the_tail_once_the_writes_end(self):
        p = self.player(16, holding=True)
        self.at(10.0, p.write, _pcm(1000))
        self.assertEqual(self.fill(p, 10.0 + audio.JITTER), _pcm(1000))

    def test_lengthens_pauses_but_not_speech_to_rebuild_the_cushion(self):
        p = self.player(32)
        self.at(10.0, p.write, _pcm(10) + _pcm(1000))
        self.assertEqual((self.fill(p, 10.01), len(p._buf)), (bytes(8), 16))
        del p._buf[:8]
        self.assertEqual((self.fill(p, 10.02), len(p._buf)), (_pcm(1000), 0))

    def test_running_dry_waits_for_a_new_cushion(self):
        p = self.player(16)
        self.at(10.0, p.write, _pcm(1000))
        self.assertEqual(self.fill(p, 10.01), _pcm(1000))
        self.assertEqual(self.fill(p, 10.02), bytes(8))  # a late packet
        self.at(10.03, p.write, _pcm(1000))
        self.assertEqual(self.fill(p, 10.04), bytes(8))


if __name__ == "__main__":
    unittest.main()
