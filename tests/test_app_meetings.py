"""App meeting wiring tests use fake capture and temporary meeting storage."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_discussion import core, meeting_copy, meetings, settings
from harness_discussion import writer
from tests.test_core import Engine, FakeHost as CoreHost


class Toast:
    def __init__(self):
        self.visible = False
        self.shown = []
        self.on_hide = None

    def show(self, message, x, y, actions=(), busy=False, **kwargs):
        self.visible = True
        self.shown.append((message, dict(actions), busy, kwargs))

    def hide(self):
        self.visible = False
        if self.on_hide:
            self.on_hide()

    def set_input_level(self, level):
        pass


class FakeHost(CoreHost):
    def __init__(self):
        super().__init__([Engine("macOS")])
        self.toasts, self.confirmations, self.states = [], [], []
        self.grant = False

    def make_toast(self):
        toast = Toast()
        self.toasts.append(toast)
        return toast

    def confirm(self, title, message, ok="OK"):
        self.confirmations.append((title, message, ok))
        return self.grant

    def mic_permission(self, then):
        then(True)

    def mic_users(self):
        return set()

    def system_audio(self, on_pcm, on_error):
        return None

    def system_audio_status(self):
        return "ready"

    def calendar(self):
        return None

    def set_meeting_state(self, label):
        self.states.append(label)

    def meeting_toast_xy(self):
        return (100, 200)

    def esc_watcher(self, callback):
        class Watcher:
            active = False
            def start(self): self.active = True
            def stop(self): self.active = False
        self.meeting_escape = callback
        return Watcher()

    def open_notes(self, app, meeting_id=None):
        self.opened.append(meeting_id)

    def screen_permission_guide(self):
        pass

    def open_mic_settings(self):
        pass


class MeetingAppTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name)
        path = folder / "settings.json"
        path.write_text(json.dumps(settings.DEFAULTS), encoding="utf-8")
        for p in (patch.object(meetings, "DIR", folder / "meetings"),
                  patch.object(settings, "PATH", str(path)), patch.object(settings, "save"),
                  patch.object(settings, "load", return_value={**settings.DEFAULTS, "sources": [["macOS", True]]})):
            p.start()
            self.addCleanup(p.stop)
        self.host = FakeHost()
        self.app = core.App(self.host)
        self.addCleanup(self.app._close_meetings)

    def test_detection_and_cancelled_consent(self):
        self.app.cfg.update(meetings=True, meeting_consent=False)
        for now in (0, 2, 4, 6, 8):
            self.app._meeting_poll({"zoom.exe"}, now)
        toast = self.app.meeting_toast
        self.assertIn(meeting_copy.ASK, toast.shown[-1][0])
        self.assertEqual(toast.shown[-1][3]["hide_s"], 30)
        with patch("harness_discussion.app_meetings.mic.Recorder") as recorder:
            toast.shown[-1][1][meeting_copy.TAKE_NOTES]()
            recorder.assert_not_called()
        self.assertEqual(self.host.confirmations, [(meeting_copy.CONSENT_TITLE, meeting_copy.CONSENT_MESSAGE, "OK")])
        self.assertEqual(self.host.confirmations[0][1],
                         "Meeting notes records your microphone and your computer's sound, then writes notes. In many places "
                         "everyone in a call must agree to be recorded. Tell the others and get their consent before you record. "
                         "The recording, transcript, and notes are saved on this computer until you delete them. To transcribe and "
                         "write notes, parts of the audio and the transcript go to the sources turned on in Settings → Dictation "
                         "and Settings → Writing. Only Local keeps them on this computer.")
        self.assertEqual(meetings.all(), [])

    def test_dismiss_suppresses_repeat(self):
        self.app.cfg["meetings"] = True
        for now in (0, 8):
            self.app._meeting_poll({"zoom.exe"}, now)
        toast = self.app.meeting_toast
        toast.shown[-1][1][meeting_copy.NOT_NOW]()
        count = len(toast.shown)
        self.app._meeting_poll({"zoom.exe"}, 100)
        self.assertEqual(len(toast.shown), count)

    def test_escape_and_timeout_dismiss_the_ask(self):
        self.app.cfg["meetings"] = True
        self.app._meeting_poll({"zoom.exe"}, 0)
        self.app._meeting_poll({"zoom.exe"}, 8)
        self.assertTrue(self.app._meeting_esc.active)
        self.host.meeting_escape()
        self.app.pump()
        self.assertFalse(self.app.meeting_toast.visible)
        self.assertFalse(self.app._meeting_esc.active)
        self.app._meeting_poll({"zoom.exe"}, 20)
        self.assertEqual(len(self.app.meeting_toast.shown), 1)
        self.app._meeting_poll(set(), 21)
        self.app._meeting_poll(set(), 81)
        self.app._meeting_poll({"zoom.exe"}, 82)
        self.app._meeting_poll({"zoom.exe"}, 90)
        self.app.meeting_toast.hide()
        self.app._meeting_poll({"zoom.exe"}, 100)
        self.assertEqual(len(self.app.meeting_toast.shown), 2)

    def test_stop_is_nonblocking_and_progress_is_visible(self):
        entered, release = threading.Event(), threading.Event()
        class FakeSession:
            def __init__(self, meeting, *args):
                self.meeting, self.system_ok = meeting, True
            def start(self): pass
            def elapsed(self): return 3
            def level(self): return 0
            def close(self): pass
            def stop(self):
                entered.set()
                release.wait(3)
        self.addCleanup(release.set)
        self.app.cfg["meeting_consent"] = True
        with patch("harness_discussion.app_meetings.Session", FakeSession), patch.object(self.app, "_process"):
            self.app.meeting_start(False)
            mid = self.app._meeting_session.meeting.id
            self.app.meeting_stop()
            self.assertTrue(entered.wait(1))
            self.assertIn(mid, self.app._meeting_stopping)
            release.set()
            self.app._meeting_progress(mid, 3, 9)
            self.assertEqual(self.app.meeting_progress(mid), (3, 9))
            self.assertIn("3 of 9", self.app.meeting_toast.shown[-1][0])

    def test_recovery_snapshot_and_duplicate_processor(self):
        m = meetings.new("private title", "", None, False)
        self.assertNotIn(m.id, self.app._to_recover)
        self.app._recover_meetings()
        self.assertEqual(meetings.load(m.id).state, "recording")
        class Running:
            alive = True
        self.app._processors[m.id] = Running()
        with patch("harness_discussion.app_meetings.Processor") as processor:
            self.app.redo_notes(m.id)
            self.app.redo_transcript(m.id)
            processor.assert_not_called()
        self.app._processors.clear()

    def test_recovery_skips_active_snapshot_even_before_stop(self):
        m = meetings.new("Live", "", None, False)
        path = m.folder / "you.wav"
        path.write_bytes(b"active header")
        self.app._to_recover = [m.id]
        self.app._meeting_session = type("Session", (), {"meeting": m, "close": lambda self: None})()
        with patch("harness_discussion.app_meetings.meeting_audio.fix_header") as fix, \
             patch.object(self.app, "_process") as process:
            self.app._recover_meetings()
        fix.assert_not_called()
        process.assert_not_called()
        self.assertEqual(path.read_bytes(), b"active header")
        self.assertEqual(meetings.load(m.id).state, "recording")

    def test_delete_refuses_unfinished_meetings(self):
        meeting = meetings.new("Meeting", "", None, False)
        for state in ("recording", "interrupted", "transcribing", "noting"):
            meeting.state = state
            meetings.save(meeting)
            self.assertFalse(self.app.delete_meeting(meeting.id))
        meeting.state = "done"
        meetings.save(meeting)
        self.assertTrue(self.app.delete_meeting(meeting.id))

    def test_end_prompt_returns_after_hiding_recording(self):
        m = meetings.new("Meeting", "Zoom", None, True)
        class Session:
            meeting, system_ok = m, True
            def elapsed(self): return 33
            def close(self): pass
        self.app._meeting_session, self.app._recording_app = Session(), "zoom.exe"
        self.app._meeting_hidden = True
        self.app._meeting_poll({"zoom.exe"}, 0)
        self.app._meeting_poll(set(), 2)
        self.app._meeting_poll(set(), 22)
        self.assertIn(meeting_copy.CALL_ENDED, self.app.meeting_toast.shown[-1][0])
        self.assertIn("0:33", self.app.meeting_toast.shown[-1][0])

    def test_hiding_recording_shows_explanation(self):
        m = meetings.new("Meeting", "", None, False)
        class Session:
            meeting, system_ok = m, True
            def elapsed(self): return 1
            def close(self): pass
        self.app._meeting_session = Session()
        self.app._show_recording()
        self.app.meeting_toast.shown[-1][1]["×"]()
        self.assertTrue(self.app._meeting_hidden)
        self.assertIn("Recording hidden.", self.app.meeting_toast.shown[-1][0])

    def test_manual_menu_start_requests_consent_when_off(self):
        self.host.grant = True
        self.app.cfg.update(meetings=False, meeting_consent=False)
        with patch("harness_discussion.app_meetings.mic.Recorder", side_effect=self.capture), \
             patch.object(self.app, "_process"):
            self.app._meeting_tray()
            self.assertIsNotNone(self.app._meeting_session)
            self.assertEqual(self.host.confirmations[0],
                             (meeting_copy.CONSENT_TITLE, meeting_copy.CONSENT_MESSAGE, "OK"))
            self.assertTrue(self.app.cfg["meetings"] and self.app.cfg["meeting_consent"])

    def test_call_end_waits_two_minutes_and_manual_never_stops(self):
        m = meetings.new("Meeting", "Zoom", None, True)
        class Session:
            meeting, system_ok = m, True
            def elapsed(self): return 60
            def close(self): pass
        self.app._meeting_session, self.app._recording_app = Session(), "zoom.exe"
        with patch.object(self.app, "meeting_stop") as stop:
            self.app._meeting_poll({"zoom.exe"}, 0)
            self.app._meeting_poll(set(), 2)
            self.app._meeting_poll(set(), 22)
            self.app._meeting_poll(set(), 141)
            stop.assert_not_called()
            self.app._meeting_poll(set(), 142)
            stop.assert_called_once()
            stop.reset_mock()
            self.app._recording_app = "manual"
            self.app._meeting_end_at = None
            self.app._meeting_poll(set(), 143)
            self.app._meeting_poll(set(), 500)
            stop.assert_not_called()

    def test_paused_and_update_wait_while_recording(self):
        m = meetings.new("Meeting", "", None, False)
        self.app._meeting_session = type("Session", (), {"meeting": m, "close": lambda self: None})()
        self.assertTrue(self.app.meeting_busy())
        self.app._pending_update = Path("update.exe")
        self.app._update_manual = True
        with patch("harness_discussion.app_updates.updates.install") as install:
            self.app._try_update()
        install.assert_not_called()
        self.assertTrue(any(ms == 1000 for ms, fn in self.host.timers))
        with patch("harness_discussion.app_meetings.Processor") as factory:
            factory.return_value.alive = True
            self.app._process(m.id)
            paused = factory.call_args.kwargs["paused"]
            self.assertFalse(paused())
            self.app._dictation_job = object()
            self.assertTrue(paused())
            self.app._dictation_job = None
        self.app._processors.clear()

    def test_logs_omit_private_content(self):
        self.app.cfg["meeting_ics_url"] = "https://private.invalid/secret"
        with patch("harness_discussion.app_meetings.calendar_ics.fetch", side_effect=ValueError("private transcript")):
            with self.assertLogs("harness_discussion", level="INFO") as logs:
                self.app._fetch_calendar()
        self.assertNotIn("private", " ".join(logs.output))

    def wait_for(self, condition):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.pump()
            if condition():
                return
            time.sleep(.01)
        self.fail("meeting worker did not finish")

    def capture(self, on_pcm, **kwargs):
        class Capture:
            def start(self):
                on_pcm(b"\x00\x20" * 24000)
            def stop(self):
                pass
            def level(self):
                return .5
        return Capture()

    def test_start_stop_and_done_with_fake_capture(self):
        self.app.cfg.update(meeting_consent=True, meetings=False)
        with patch("harness_discussion.app_meetings.mic.Recorder", side_effect=self.capture), \
             patch("harness_discussion.app_meetings.dictation.transcribe", return_value=("private script", "fake")), \
             patch("harness_discussion.app_meetings.meeting_notes.make", return_value=("notes", "fake")):
            self.app.meeting_start(False)
            mid = self.app._meeting_session.meeting.id
            self.app.meeting_stop()
            self.wait_for(lambda: meetings.load(mid).state == "done" and not self.app.meeting_busy(mid))
        self.assertTrue(any("Notes ready:" in item[0] for item in self.app.meeting_toast.shown))
        self.assertIsNone(self.host.states[-1])

    def test_calendar_status_counts_today_else_next_seven_days(self):
        from datetime import datetime, timedelta
        now = datetime.now().astimezone()
        later = [meetings.Event("Planning", now + timedelta(days=d), now + timedelta(days=d, hours=1), "ics")
                 for d in (2, 3)]
        self.app.cfg["meeting_ics_url"] = "https://calendar.example/private.ics"
        for found, expected in ((later, meeting_copy.EVENTS_WEEK.format(n=2)),
                                ([*later, meetings.Event("Now", now, now + timedelta(hours=1), "ics")],
                                 meeting_copy.EVENTS_TODAY.format(n=1))):
            with patch("harness_discussion.app_meetings.calendar_ics.fetch", return_value="ics"), \
                 patch("harness_discussion.app_meetings.calendar_ics.parse", return_value=found) as parse:
                self.app._fetch_calendar()
            self.assertGreaterEqual(parse.call_args.args[2] - now, timedelta(days=6))
            kind, events, status = self.app.events.get_nowait()
            self.assertEqual((kind, status), ("meeting_events", expected))

    def test_done_while_recording_notifies_instead_of_replacing_pill(self):
        m = meetings.new("Earlier sync", "", None, False)
        self.app._meeting_session = object()
        shown = len(self.app.meeting_toast.shown)
        self.app._meeting_done(m.id, None)
        self.app._meeting_session = None
        self.assertEqual(len(self.app.meeting_toast.shown), shown)
        self.assertEqual(self.host.notes[-1], "Notes ready: Earlier sync")

    def test_quit_recording_then_recover(self):
        self.app.cfg["meeting_consent"] = True
        with patch("harness_discussion.app_meetings.mic.Recorder", side_effect=self.capture):
            self.app.meeting_start(False)
            mid = self.app._meeting_session.meeting.id
            self.app._close_meetings()
        self.assertEqual(meetings.load(mid).state, "interrupted")
        self.app = core.App(FakeHost())
        self.addCleanup(self.app._close_meetings)
        with patch("harness_discussion.app_meetings.dictation.transcribe", return_value=("script", "fake")), \
             patch("harness_discussion.app_meetings.meeting_notes.make", return_value=("notes", "fake")):
            self.app._recover_meetings()
            self.wait_for(lambda: meetings.load(mid).state == "done" and not self.app.meeting_busy(mid))

    def test_quit_cancels_inflight_processor_without_failed_part(self):
        from harness_discussion import meeting_audio
        m = meetings.new("Meeting", "", None, False)
        for name in ("you", "them"):
            track = meeting_audio.TrackFile(m.folder / (name + ".wav"))
            track.append(b"\x00\x20" * 16000)
            track.close()
        m.state = "transcribing"
        meetings.save(m)
        entered = threading.Event()
        def transcribe(data, chain, order, cancelled):
            entered.set()
            while not cancelled():
                time.sleep(.005)
            raise writer.Cancelled()
        with patch("harness_discussion.app_meetings.dictation.transcribe", side_effect=transcribe):
            self.app._process(m.id)
            self.assertTrue(entered.wait(3))
            processor = self.app._processors[m.id]
            self.app._close_meetings()
            deadline = time.monotonic() + 3
            while processor.alive and time.monotonic() < deadline:
                time.sleep(.01)
        self.assertFalse(processor.alive)
        self.assertEqual(meetings.load(m.id).state, "transcribing")
        self.assertEqual(meetings.parts(m.id), [])


if __name__ == "__main__":
    unittest.main()
