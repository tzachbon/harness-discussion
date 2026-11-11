"""Session tests use injected audio and clocks, never real capture devices."""
import audioop
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from harness_discussion import meeting_audio as audio, meeting_session as session, meetings, writer

FIXTURES = Path(__file__).parent / 'fixtures' / 'meetings'


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('worker did not finish')
        time.sleep(.005)


class Capture:
    def __init__(self, callback):
        self.callback = callback
    def start(self):
        pass
    def stop(self):
        pass
    def level(self):
        return .5


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch = patch.object(meetings, 'DIR', Path(self.temp.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.meeting = meetings.new('Private title', '', None, False)
        self.events = []

    def processor(self, transcribe, notes=lambda lines, cancelled: ('notes', 'local'), **kwargs):
        p = session.Processor(self.meeting.id, transcribe, notes, self.events.append, **kwargs)
        p.start()
        wait_for(lambda: not p.alive)
        return meetings.load(self.meeting.id)

    def tracks(self, speech=True):
        for track in ('you', 'them'):
            f = audio.TrackFile(self.meeting.folder / (track + '.wav'))
            f.append((FIXTURES / (track + '.wav')).read_bytes()[44:] if speech else bytes(32000 * 2))
            f.close()

    def test_session_padding_and_mic_stall(self):
        clock = [0.0]
        captures = []
        def factory(callback, *args):
            obj = Capture(callback)
            captures.append(obj)
            return obj
        s = session.Session(self.meeting, factory, factory, self.events.append, lambda: clock[0])
        s.start()
        clock[0] = 5
        wait_for(lambda: s._tracks[1].frames >= 5 * audio.RATE)
        captures[0].callback(bytes(24000 * 2))
        s.stop()
        with wave.open(str(self.meeting.folder / 'you.wav')) as f:
            self.assertAlmostEqual(f.getnframes() / 16000, 1, delta=.01)
        with wave.open(str(self.meeting.folder / 'them.wav')) as f:
            self.assertEqual(f.getnframes(), 5 * 16000)
            self.assertEqual(f.readframes(f.getnframes()), bytes(5 * 16000 * 2))
        self.assertEqual(meetings.load(self.meeting.id).state, 'transcribing')

    def test_close_interrupts(self):
        s = session.Session(self.meeting, lambda c: Capture(c), lambda c, e: None, self.events.append)
        s.start()
        before = time.monotonic()
        s.close()
        self.assertLess(time.monotonic() - before, 3)
        self.assertEqual(meetings.load(self.meeting.id).state, 'interrupted')

    def test_processing_failures_and_redo(self):
        self.tracks()
        calls = []
        def transcribe(wav, cancelled):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError('Private transcript')
            return 'private speech', 'local'
        with self.assertLogs('harness_discussion', level='INFO') as logs:
            m = self.processor(transcribe)
        self.assertNotIn('Private transcript', str(logs.output))
        self.assertEqual(m.state, 'done')
        self.assertTrue(m.error)
        failed = [p for p in meetings.parts(m.id) if p.source == 'failed']
        self.assertEqual(len(failed), 1)
        m = self.processor(lambda wav, cancelled: ('recovered', 'local'), redo_failed=True)
        self.assertEqual((m.state, m.error), ('done', ''))
        self.assertEqual(sum(p.track == failed[0].track and p.start == failed[0].start for p in meetings.parts(m.id)), 1)

    def test_all_failure_and_silence(self):
        self.tracks()
        def fail(wav, cancelled):
            raise ValueError('secret words')
        m = self.processor(fail, lambda *args: self.fail('notes called'))
        self.assertEqual(m.state, 'failed_transcript')
        self.assertTrue(all(p.source == 'failed' or p.silent for p in meetings.parts(m.id)))

    def test_pause_requeues_and_cancel_does_not_write(self):
        self.tracks()
        entered = threading.Event()
        paused = [False]
        calls = []
        def transcribe(wav, cancelled):
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                wait_for(cancelled)
                raise writer.Cancelled()
            return 'ok', 'local'
        p = session.Processor(self.meeting.id, transcribe, lambda *a: ('notes', 'local'), self.events.append,
                              paused=lambda: paused[0])
        p.start()
        self.assertTrue(entered.wait(2))
        paused[0] = True
        time.sleep(.04)
        self.assertEqual(meetings.parts(self.meeting.id), [])
        paused[0] = False
        wait_for(lambda: not p.alive)
        self.assertGreater(len(calls), 1)
        self.assertEqual(meetings.load(self.meeting.id).state, 'done')

        meetings.delete(self.meeting.id)
        self.meeting = meetings.new('Private title', '', None, False)
        self.tracks()
        before = (self.meeting.folder / 'meeting.json').read_bytes()
        entered.clear()
        def blocked(wav, cancelled):
            entered.set()
            wait_for(cancelled)
            raise writer.Cancelled()
        p = session.Processor(self.meeting.id, blocked, lambda *a: ('notes', 'local'), self.events.append)
        p.start()
        self.assertTrue(entered.wait(2))
        p.cancel()
        wait_for(lambda: not p.alive)
        self.assertEqual((self.meeting.folder / 'meeting.json').read_bytes(), before)
        self.assertFalse((self.meeting.folder / 'transcript.jsonl').exists())


    def test_twenty_seconds_and_system_audio(self):
        clock = [0.0]
        captures = []
        def factory(callback, *args):
            c = Capture(callback)
            captures.append(c)
            return c
        s = session.Session(self.meeting, factory, factory, self.events.append, lambda: clock[0])
        s.start()
        system = (FIXTURES / 'them.wav').read_bytes()[44:]
        you = audioop.ratecv((FIXTURES / 'you.wav').read_bytes()[44:], 2, 1, 16000, 24000, None)[0]
        for i in range(200):
            captures[0].callback(you[i * 4800:(i + 1) * 4800])
            captures[1].callback(system[i * 3200:(i + 1) * 3200])
        clock[0] = 20
        s.stop()
        for name in ('you', 'them'):
            with wave.open(str(self.meeting.folder / (name + '.wav'))) as f:
                self.assertAlmostEqual(f.getnframes() / 16000, 20, delta=.2)

    def test_silent_parts_skip_transcription_and_notes_failure_is_safe(self):
        self.tracks(speech=False)
        called = []
        def notes(lines, cancelled):
            called.append(lines)
            raise ValueError('secret notes')
        with self.assertLogs('harness_discussion', level='INFO') as logs:
            m = self.processor(lambda *a: self.fail('silent audio transcribed'), notes)
        self.assertEqual(m.state, 'failed_notes')
        self.assertEqual(called, [[]])
        self.assertNotIn('secret notes', str(logs.output) + m.error)
        self.assertTrue(all(p.silent for p in meetings.parts(m.id)))

    def test_recovery_skips_saved_parts_and_repairs_headers(self):
        self.tracks()
        part = meetings.Part('you', 0, 10, 'existing', 'local')
        meetings.add_part(self.meeting.id, part)
        self.meeting.state = 'interrupted'
        meetings.save(self.meeting)
        for track in ('you', 'them'):
            with (self.meeting.folder / (track + '.wav')).open('r+b') as f:
                f.seek(4)
                f.write(bytes(4))
        m = self.processor(lambda *args: ('new', 'local'))
        self.assertEqual(m.state, 'done')
        self.assertEqual(meetings.parts(m.id)[0], part)
        for track in ('you', 'them'):
            with wave.open(str(self.meeting.folder / (track + '.wav'))) as f:
                self.assertEqual(f.getnframes(), 320000)

    def test_disk_error_posts_safe_event(self):
        with patch.object(audio.TrackFile, 'append', side_effect=OSError('private content')):
            s = session.Session(self.meeting, lambda c: Capture(c), lambda c, e: None, self.events.append)
            with self.assertLogs('harness_discussion', level='WARNING') as logs:
                s.start()
                s._queues[0].put(bytes(4800))
                wait_for(lambda: bool(self.events))
                s._thread.join(2)
            self.assertEqual(self.events[0], ('meeting_error', self.meeting.id, 'disk'))
            self.assertNotIn('private content', str(logs.output))






    def test_close_with_blocked_capture_closes_tracks(self):
        blocked, release = threading.Event(), threading.Event()
        class SlowCapture(Capture):
            def stop(self):
                blocked.set()
                release.wait(2)
        s = session.Session(self.meeting, lambda c: SlowCapture(c), lambda c, e: None, self.events.append)
        s.start()
        s._mic.callback(bytes(4800))
        started = time.monotonic()
        try:
            s.close()
            self.assertLess(time.monotonic() - started, 3)
            wait_for(blocked.is_set)
            self.assertFalse(s._thread.is_alive())
            with wave.open(str(self.meeting.folder / 'you.wav')) as f:
                self.assertGreater(f.getnframes(), 0)
        finally:
            release.set()

    def test_failure_recorded_once_and_notes_receive_successes(self):
        self.tracks()
        calls, note_lines = [], []
        def transcribe(wav, cancelled):
            calls.append(wav)
            if len(calls) == 1:
                raise RuntimeError('Private title https://private.invalid secret transcript')
            return 'recognized words', 'local'
        def notes(lines, cancelled):
            note_lines.append(lines)
            return 'notes', 'local'
        with self.assertLogs('harness_discussion', level='INFO') as logs:
            m = self.processor(transcribe, notes)
        raw = (m.folder / 'transcript.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(sum('"source": "failed"' in line for line in raw), 1)
        self.assertEqual(len(note_lines), 1)
        self.assertTrue(all(line[2] == 'recognized words' for line in note_lines[0]))
        self.assertEqual({p.track for p in meetings.parts(m.id)}, {'you', 'them'})
        self.assertEqual(m.state, 'done')
        for secret in ('Private title', 'https://private.invalid', 'secret transcript'):
            self.assertNotIn(secret, str(logs.output))


    def test_live_processor_preserves_stop_metadata(self):
        self.tracks()
        entered, resume = threading.Event(), threading.Event()
        live = [True]
        def transcribe(wav, cancelled):
            if not entered.is_set():
                entered.set()
                if not resume.wait(2):
                    raise AssertionError('test did not release transcription')
            return 'recognized', 'local'
        p = session.Processor(self.meeting.id, transcribe, lambda *a: ('notes', 'local'), self.events.append,
                              live=lambda: live[0])
        with patch.object(p._cancel, 'wait', return_value=False):
            p.start()
            self.assertTrue(entered.wait(2))
            stopped = meetings.load(self.meeting.id)
            stopped.ended_at = '2026-11-11T10:01:00+00:00'
            stopped.state = 'transcribing'
            meetings.save(stopped)
            live[0] = False
            resume.set()
            wait_for(lambda: not p.alive)
        finished = meetings.load(self.meeting.id)
        self.assertEqual(finished.state, 'done')
        self.assertEqual(finished.ended_at, stopped.ended_at)
