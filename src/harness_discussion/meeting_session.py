"""Meeting capture and resumable transcription, with no meeting content in logs."""
import logging
import queue
import threading
import time
from datetime import datetime

from . import meeting_audio as audio, meetings, mic, writer

FLUSH_S, PAD_S, MAX_HOURS, MIN_FREE_BYTES = 1.0, 0.2, 4, 1_000_000_000
log = logging.getLogger('harness_discussion')


class Session:
    def __init__(self, meeting, mic_factory, system_factory, post, clock=time.monotonic):
        self.meeting, self.post, self.clock = meeting, post, clock
        self._queues = (queue.Queue(), queue.Queue())
        self._mic_factory, self._system_factory = mic_factory, system_factory
        self._stop = threading.Event()
        self._thread = None
        self._mic = self._system = None
        self.system_ok = True
        self._started = None
        self._disk_error = False
        self._capture_lock = threading.Lock()

    def start(self):
        self._started = self.clock()
        tracks = []
        try:
            for name in ('you.wav', 'them.wav'):
                tracks.append(audio.TrackFile(self.meeting.folder / name))
        except OSError as exc:
            for track in tracks:
                try:
                    track.close()
                except OSError:
                    pass
            log.warning('Meeting disk error: %s', type(exc).__name__)
            self.post(('meeting_error', self.meeting.id, 'disk'))
            return
        self._tracks = tuple(tracks)
        self._mic = self._mic_factory(self._queues[0].put)
        try:
            self._system = self._system_factory(self._queues[1].put, self._system_error)
        except Exception:
            self._system = None
        self.system_ok = self.system_ok and self._system is not None
        self._thread = threading.Thread(target=self._write, daemon=True)
        self._thread.start()
        try:
            self._mic.start()
        except Exception:
            self._stop.set()
            self._thread.join()
            raise
        if self._system:
            try:
                self._system.start()
            except Exception:
                self._system_error('capture')

    def _system_error(self, _error):
        self.system_ok = False

    def level(self):
        return self._mic.level() if self._mic else 0.0

    def elapsed(self):
        return max(0.0, self.clock() - self._started) if self._started is not None else 0.0

    def _write(self):
        resampler = audio.Resampler(24000, 1, 's16')
        last_flush = self.clock()
        limited = False
        try:
            while True:
                for index, track in enumerate(self._tracks):
                    while True:
                        try:
                            data = self._queues[index].get_nowait()
                        except queue.Empty:
                            break
                        track.append(resampler.feed(data) if index == 0 else data)
                now = self.clock()
                missing = int(self.elapsed() * audio.RATE) - self._tracks[1].frames
                if missing > PAD_S * audio.RATE:
                    self._tracks[1].append(bytes(missing * 2))
                if now - last_flush >= FLUSH_S:
                    for track in self._tracks:
                        track.flush()
                    last_flush = now
                if not limited and self.elapsed() >= MAX_HOURS * 3600:
                    limited = True
                    self.post(('meeting_limit', self.meeting.id))
                if self._stop.is_set() and all(q.empty() for q in self._queues):
                    break
                self._stop.wait(0.02)
        except OSError as exc:
            self._disk_error = True
            log.warning('Meeting disk error: %s', type(exc).__name__)
            self.post(('meeting_error', self.meeting.id, 'disk'))
            threading.Thread(target=self._close_capture, daemon=True).start()
        finally:
            for track in self._tracks:
                try:
                    track.close()
                except OSError:
                    if not self._disk_error:
                        self._disk_error = True
                        self.post(('meeting_error', self.meeting.id, 'disk'))

    def _finish(self, state):
        self._stop_capture()
        self._stop.set()

        if self._thread:
            self._thread.join()
        self.meeting.ended_at = datetime.now().astimezone().isoformat()
        self.meeting.state = state
        meetings.save(self.meeting)

    def stop(self):
        self._finish('transcribing')

    def close(self):
        # Capture shutdown can block on device drivers; finish it away from the UI thread.
        self.meeting.ended_at = datetime.now().astimezone().isoformat()
        self.meeting.state = 'interrupted'
        meetings.save(self.meeting)
        self._stop.set()
        threading.Thread(target=self._close_capture, daemon=True).start()
        if self._thread:
            self._thread.join(timeout=2.5)

    def _close_capture(self):
        self._stop_capture()
        self._stop.set()

    def _stop_capture(self):
        with self._capture_lock:
            for name in ('_mic', '_system'):
                capture = getattr(self, name)
                if capture:
                    try:
                        capture.stop()
                    except Exception as exc:
                        log.warning('Meeting capture stop failed: %s', type(exc).__name__)
                    setattr(self, name, None)


class Processor:
    def __init__(self, meeting_id, transcribe, make_notes, post, live=lambda: False, paused=lambda: False,
                 redo_failed=False):
        self.id, self.transcribe, self.make_notes, self.post = meeting_id, transcribe, make_notes, post
        self.live, self.paused, self.redo_failed = live, paused, redo_failed
        self._cancel = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self):
        self._cancel.set()

    @property
    def alive(self):
        return bool(self._thread and self._thread.is_alive())

    def _check(self):
        if self._cancel.is_set():
            raise writer.Cancelled()

    def _part(self, track, start, end):
        path = meetings.load(self.id).folder / (track + '.wav')
        pcm = audio.read_pcm(path, start, end)
        if audio.is_silent(pcm):
            self._check()
            meetings.add_part(self.id, meetings.Part(track, start, end, silent=True))
            return
        while True:
            self._check()
            if self.paused():
                self._cancel.wait(0.02)
                continue
            try:
                text, source = self.transcribe(mic.wav(pcm, audio.RATE),
                                               lambda: self._cancel.is_set() or self.paused())
                self._check()
                if self.paused():
                    continue
                part = meetings.Part(track, start, end, text, source)
            except writer.Cancelled:
                self._check()
                continue
            except Exception as exc:
                self._check()
                if self.paused():
                    continue
                log.info('Meeting transcription failed: %s', type(exc).__name__)
                part = meetings.Part(track, start, end, source='failed')
            self._check()
            meetings.add_part(self.id, part)
            return

    def _run(self):
        try:
            self._process()
        except writer.Cancelled:
            return
        except Exception as exc:
            log.warning('Meeting processor failed: %s', type(exc).__name__)

    def _process(self):
        meeting = meetings.load(self.id)
        if not self.live():
            for track in ('you', 'them'):
                path = meeting.folder / (track + '.wav')
                if path.exists():
                    audio.fix_header(path)
        if self.redo_failed:
            for part in meetings.parts(self.id):
                if part.source == 'failed':
                    self._part(part.track, part.start, part.end)
        while True:
            self._check()
            live = self.live()
            work = []
            for track in ('you', 'them'):
                path = meeting.folder / (track + '.wav')
                if not path.exists():
                    continue
                start = meetings.transcribed_until(self.id, track)
                pcm = audio.read_pcm(path, start, path.stat().st_size / 2 / audio.RATE)
                chunker = audio.Chunker(start)
                chunks = chunker.feed(pcm)
                if not live:
                    chunks += chunker.finish()
                work += [(track, begin, end) for begin, end in chunks]
            for done, (track, begin, end) in enumerate(work, 1):
                self._part(track, begin, end)
                if live:
                    continue
                self.post(('meeting_progress', self.id, done, len(work)))
                meeting = meetings.load(self.id)
                if meeting.state in ('recording', 'interrupted'):
                    self._check()
                    meeting.state = 'transcribing'
                    meetings.save(meeting)
            if not live:
                break
            self._cancel.wait(2)
        self._check()
        meeting = meetings.load(self.id)
        parts = meetings.parts(self.id)
        failed = [p for p in parts if p.source == 'failed']
        speech = [p for p in parts if not p.silent]
        if speech and len(failed) == len(speech):
            meeting.state = 'failed_transcript'
            meeting.error = 'Some parts could not be transcribed'
            meetings.save(meeting)
            self.post(('meeting_done', self.id, meeting.error))
            return
        meeting.error = 'Some parts could not be transcribed' if failed else ''
        meeting.state = 'noting'
        self._check()
        meetings.save(meeting)
        try:
            md, source = self.make_notes(meetings.lines(parts), self._cancel.is_set)
            self._check()
            meetings.save_notes(self.id, md)
            meeting.state = 'done'
            meetings.save(meeting)
            self.post(('meeting_done', self.id, None))
        except writer.Cancelled:
            raise
        except Exception as exc:
            self._check()
            log.info('Meeting notes failed: %s', type(exc).__name__)
            meeting.state = 'failed_notes'
            meeting.error = 'Could not make notes'
            meetings.save(meeting)
            self.post(('meeting_done', self.id, meeting.error))
