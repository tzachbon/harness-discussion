"""Meeting lifecycle orchestration; audio and meeting content never enter logs."""
import logging
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta

from . import calendar_ics, dictation, meeting_audio, meeting_copy as copy, meeting_detect, meeting_notes, meetings, mic, writer
from .meeting_session import MIN_FREE_BYTES, Processor, Session

log = logging.getLogger("harness_discussion")


class Meetings:
    MEETING_EVENTS = {"meeting_poll": "_meeting_poll", "meeting_events": "_meeting_events",
                      "meeting_progress": "_meeting_progress", "meeting_done": "_meeting_done",
                      "meeting_stopped": "_meeting_stopped", "meeting_error": "_meeting_error",
                      "meeting_limit": "_meeting_limit", "meeting_tray": "_meeting_tray",
                      "meeting_open": "_meeting_open"}

    def _init_meetings(self):
        self._to_recover = [m.id for m in meetings.unfinished()]
        self._processors, self._meeting_progress_values = {}, {}
        self._meeting_session = None
        self._meeting_stopping = set()
        self._meeting_detector = meeting_detect.Detector()
        self._calendar_events, self.calendar_status = [], copy.CALENDAR_NOT_SET
        self._meeting_shutdown = threading.Event()
        self._meeting_ask_id = self._meeting_end_at = None
        self._meeting_hidden = False
        self.meeting_toast = self.host.make_toast() if hasattr(self.host, "make_toast") else None
        self._meeting_esc = (self.host.esc_watcher(lambda: self.events.put(("call", self._dismiss_meeting_ask)))
                             if self.meeting_toast and hasattr(self.host, "esc_watcher") else None)
        if self.meeting_toast:
            self.meeting_toast.on_hide = self._meeting_toast_hidden

    def _meeting_toast_hidden(self):
        if self._meeting_ask_id is not None:
            self._meeting_detector.dismissed(time.monotonic())
            self._meeting_ask_id = None
        if self._meeting_esc:
            self._meeting_esc.stop()

    def _dismiss_meeting_ask(self):
        if self._meeting_ask_id is not None and self.meeting_toast:
            self.meeting_toast.hide()
            self._meeting_toast_hidden()

    def _start_meetings(self):
        self.host.after(10_000, self._recover_meetings)
        if not hasattr(self.host, "mic_users"):
            return
        def poll():
            while not self._meeting_shutdown.is_set():
                if ((self.cfg.get("meetings") and self.cfg.get("meeting_ask")) or self._meeting_session):
                    try:
                        users = self.host.mic_users()
                        self.events.put(("meeting_poll", users, time.monotonic()))
                    except Exception as exc:
                        log.warning("Meeting detection failed: %s", type(exc).__name__)
                self._meeting_shutdown.wait(meeting_detect.POLL_S)
        def calendar_loop():
            while not self._meeting_shutdown.is_set():
                if self.cfg.get("meetings"):
                    self._fetch_calendar()
                self._meeting_shutdown.wait(900)
        threading.Thread(target=poll, daemon=True).start()
        threading.Thread(target=calendar_loop, daemon=True).start()

    def refresh_meeting_calendar(self):
        threading.Thread(target=self._fetch_calendar, daemon=True).start()

    def _fetch_calendar(self):
        now = datetime.now().astimezone()
        start, end = now - timedelta(hours=1), now + timedelta(hours=24)
        result, status = [], copy.CALENDAR_NOT_SET
        try:
            url = self.cfg.get("meeting_ics_url", "")
            if url:
                result.extend(calendar_ics.parse(calendar_ics.fetch(url), start, end))
                status = copy.EVENTS_TODAY.format(n=sum(e.start.astimezone().date() == now.date() for e in result))
            cal = self.host.calendar()
            if cal and self.cfg.get("meeting_eventkit") and cal.status() == "allowed":
                result.extend(cal.events(start, end))
                status = copy.EVENTS_TODAY.format(n=sum(e.start.astimezone().date() == now.date() for e in result))
        except Exception as exc:
            log.info("Meeting calendar failed: %s", type(exc).__name__)
            status = copy.CALENDAR_ERROR
        self.events.put(("meeting_events", result, status))

    def _meeting_events(self, events, status):
        self._calendar_events, self.calendar_status = events, status

    def _show_meeting_toast(self, message, actions=(), busy=False, hide_s=None):
        if self.meeting_toast:
            x, y = self.host.meeting_toast_xy()
            self.meeting_toast.show(message, x, y, actions, busy, hide_s=hide_s)

    def _meeting_poll(self, users, now):
        if users is None:
            return
        event = meeting_detect.current_event(self._calendar_events, datetime.now().astimezone())
        session = self._meeting_session
        recording = self._recording_app if session else None
        signal = self._meeting_detector.update(now, users, bool(event), recording)
        if self._meeting_end_at is not None and session:
            if self._recording_app in users:
                self._meeting_end_at = None
                self._show_recording()
            elif now - self._meeting_end_at >= meeting_detect.END_WAIT_S:
                self.meeting_stop()
                return
        if signal == "ask" and not session and self.cfg.get("meetings") and self.cfg.get("meeting_ask"):
            app_id = self._meeting_detector.app
            app = meeting_detect.app_name(app_id)
            line1, line2 = copy.ask_lines(event.title if event else None, app)
            self._meeting_ask_id = app_id
            def dismiss():
                self._meeting_ask_id = None
                self._meeting_detector.dismissed(time.monotonic())
            self._show_meeting_toast("\n".join(filter(None, (line1, line2))),
                                     [(copy.TAKE_NOTES, lambda: self.meeting_start(True, app_id)),
                                      (copy.NOT_NOW, dismiss), ("×", dismiss)], hide_s=30)
            if self._meeting_esc:
                self._meeting_esc.start()
        elif signal == "ended" and session:
            self._meeting_end_at = now
            self._meeting_hidden = False
            self._show_recording(ended=True)

    def meeting_consent(self):
        if self.cfg.get("meeting_consent"):
            return True
        if not self.host.confirm(copy.CONSENT_TITLE, copy.CONSENT_MESSAGE, ok=copy.OK):
            return False
        self.change("meeting_consent", True)
        self.change("meetings", True)
        return True

    def set_meetings(self, enabled):
        if enabled:
            if not self.meeting_consent():
                return False
            if not self.cfg.get("meetings"):
                self.change("meetings", True)
        else:
            if self._meeting_session:
                if not self.host.confirm(copy.MEETINGS, copy.STOP_IN_PROGRESS, ok=copy.STOP):
                    return False
                self.meeting_stop()
            self.change("meetings", False)
        return True

    def meeting_start(self, auto=False, app_id=None):
        if self._meeting_session or self._meeting_stopping or not self.meeting_consent():
            return
        self._meeting_ask_id = None
        self.host.mic_permission(lambda granted: self._meeting_mic_answer(granted, auto, app_id))

    def _meeting_mic_answer(self, granted, auto, app_id):
        if self._closing or self._meeting_session or self._meeting_stopping:
            return
        if not granted:
            self._show_meeting_toast(copy.MIC_OFF, [(copy.OPEN_SETTINGS, self.host.open_mic_settings)], hide_s=10)
            return
        try:
            meetings.DIR.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(meetings.DIR).free < MIN_FREE_BYTES:
                self.host.notify("Not enough free space to record a meeting.")
                return
            if self.host.system_audio_status() == "needs_permission":
                self.host.screen_permission_guide()
            event = meeting_detect.current_event(self._calendar_events, datetime.now().astimezone())
            app = meeting_detect.app_name(app_id) if app_id else None
            meeting = meetings.new(event.title if event else app or "Meeting", app or "", event, auto)
            session = Session(meeting, lambda on_pcm: mic.Recorder(device=self.cfg["dictate_microphone"], on_pcm=on_pcm),
                              self.host.system_audio, self.events.put)
            self._meeting_session, self._recording_app = session, app_id if auto and app_id else "manual"
            self._meeting_end_at, self._meeting_hidden = None, False
            session.start()
            self._show_recording()
            self._process(meeting.id)
            self._refresh_meeting_pill(session)
        except Exception as exc:
            log.warning("Meeting start failed: %s", type(exc).__name__)
            self.host.notify("Could not start meeting recording.")
            if self._meeting_session:
                self._meeting_session.close()
                self._meeting_session = None

    def _show_recording(self, ended=False):
        session = self._meeting_session
        if not session or self._meeting_hidden:
            return
        elapsed = copy.elapsed(session.elapsed())
        message = f"{copy.CALL_ENDED}  {elapsed}" if ended else f"{copy.RECORDING}  {elapsed}\n{session.meeting.title}"
        if not session.system_ok:
            message += "\n" + copy.MIC_ONLY
        def keep():
            self._meeting_end_at = None
            self._meeting_detector.kept(time.monotonic())
            self._show_recording()
        def hide():
            self._meeting_hidden = True
            self._show_meeting_toast(copy.RECORDING_HIDDEN_MAC if sys.platform == "darwin" else copy.RECORDING_HIDDEN,
                                     [("×", lambda: None)], hide_s=10)
        actions = [(copy.STOP, self.meeting_stop)]
        actions += [(copy.KEEP_RECORDING, keep)] if ended else [("×", hide)]
        self._show_meeting_toast(message, actions, busy=True)

    def _refresh_meeting_pill(self, session):
        if self._meeting_session is not session or self._closing:
            return
        self.host.set_meeting_state(f"● Recording {copy.elapsed(session.elapsed())}")
        if self.meeting_toast and self.meeting_toast.visible and not self._meeting_hidden:
            self._show_recording(self._meeting_end_at is not None)
            self.meeting_toast.set_input_level(session.level())
        self.host.after(1000, lambda: self._refresh_meeting_pill(session))

    def meeting_stop(self):
        session = self._meeting_session
        if not session:
            return
        self._meeting_stopping.add(session.meeting.id)
        self._meeting_session = None
        self._meeting_end_at = None
        if self.meeting_toast:
            self.meeting_toast.hide()
        self.host.set_meeting_state(None)
        def stop():
            try:
                session.stop()
            except Exception as exc:
                log.warning("Meeting stop failed: %s", type(exc).__name__)
                session.close()
            finally:
                self.events.put(("meeting_stopped", session.meeting.id))
        threading.Thread(target=stop, daemon=True).start()

    def _meeting_stopped(self, meeting_id):
        self._meeting_stopping.discard(meeting_id)
        if meeting_id not in self._processors:
            self._process(meeting_id)
        if not self._meeting_session:
            self._show_meeting_toast(copy.STATE_MAKING, [(copy.OPEN, lambda: self._meeting_open(meeting_id))], busy=True)

    def _process(self, meeting_id, redo_failed=False):
        old = self._processors.get(meeting_id)
        if old and old.alive:
            return
        meeting = meetings.load(meeting_id)
        def transcribe(data, cancelled):
            return dictation.transcribe(data, self.stt_sources, self.cfg["stt_sources"], cancelled=cancelled)
        def make_notes(lines, cancelled):
            ready = threading.Event()
            def arm():
                self._summary_gen += 1
                ready.set()
            self.events.put(("call", arm))
            while not ready.wait(0.05):
                if cancelled():
                    raise writer.Cancelled()
            try:
                return meeting_notes.make(lines, writer.chain(self.writer_sources, self.cfg["writer_sources"]),
                                          models=self.cfg["writer_models"], cancelled=cancelled,
                                          about=writer.about(self.cfg), title=meeting.title,
                                          event_title=(meeting.event or {}).get("title", ""))
            finally:
                self.events.put(("call", self._arm_summary_stop))
        def live():
            session = self._meeting_session
            return bool((session and session.meeting.id == meeting_id) or meeting_id in self._meeting_stopping)
        processor = Processor(meeting_id, transcribe, make_notes, self.events.put, live=live,
                              paused=lambda: bool(self._recorder or self._dictation_job), redo_failed=redo_failed)
        self._processors[meeting_id] = processor
        processor.start()

    def _meeting_progress(self, meeting_id, done, total):
        self._meeting_progress_values[meeting_id] = (done, total)
        if not self._meeting_session:
            self._show_meeting_toast(copy.MAKING_PARTS.format(done=done, total=total),
                                     [(copy.OPEN, lambda: self._meeting_open(meeting_id))], busy=True)

    def meeting_progress(self, meeting_id):
        return self._meeting_progress_values.get(meeting_id, (0, 0))

    def _meeting_done(self, meeting_id, error):
        self._processors.pop(meeting_id, None)
        self._meeting_progress_values.pop(meeting_id, None)
        message = copy.NOTES_FAILED_TOAST if error else copy.NOTES_READY.format(title=meetings.load(meeting_id).title)
        if self._meeting_session:  # the pill owns the meeting toast while recording: tell the user through the system
            self.host.notify(message)
            return
        self._show_meeting_toast(message, [(copy.OPEN, lambda: self._meeting_open(meeting_id)), ("×", lambda: None)], hide_s=10)

    def _meeting_error(self, meeting_id, error):
        if self._meeting_session and self._meeting_session.meeting.id == meeting_id:
            self.meeting_stop()
        self.host.notify("Meeting recording stopped because of a disk error.")

    def _meeting_limit(self, meeting_id):
        if self._meeting_session and self._meeting_session.meeting.id == meeting_id:
            self.meeting_stop()
            self.host.notify(copy.MAX_LENGTH)

    def _meeting_tray(self):
        self.meeting_stop() if self._meeting_session else self.meeting_start(False)

    def _meeting_open(self, meeting_id=None):
        self.host.open_notes(self, meeting_id)

    def meeting_busy(self, meeting_id=None):
        session = getattr(self, "_meeting_session", None)
        active = session and session.meeting.id
        ids = set(getattr(self, "_meeting_stopping", ())) | {key for key, p in getattr(self, "_processors", {}).items() if p.alive}
        if active:
            ids.add(active)
        return meeting_id in ids if meeting_id else bool(ids)

    def delete_meeting(self, meeting_id):
        if self.meeting_busy(meeting_id) or meetings.load(meeting_id).state not in ("done", "failed_transcript", "failed_notes"):
            return False
        meetings.delete(meeting_id)
        return True

    def redo_transcript(self, meeting_id):
        self._process(meeting_id, redo_failed=True)

    def redo_notes(self, meeting_id):
        self._process(meeting_id)

    def _recover_meetings(self):
        if self._meeting_shutdown.is_set():
            return
        pending, self._to_recover = self._to_recover, []
        for meeting_id in pending:
            if self.meeting_busy(meeting_id):
                continue
            try:
                meeting = meetings.load(meeting_id)
                if meeting.state not in ("recording", "interrupted", "transcribing", "noting"):
                    continue
                if meeting.state == "recording":
                    for name in ("you.wav", "them.wav"):
                        path = meeting.folder / name
                        if path.exists():
                            meeting_audio.fix_header(path)
                    meeting.state = "interrupted"
                    meetings.save(meeting)
                self._process(meeting_id)
            except (meetings.Error, OSError) as exc:
                log.warning("Meeting recovery failed: %s", type(exc).__name__)

    def _close_meetings(self):
        if not hasattr(self, "_meeting_shutdown"):
            return
        self._meeting_shutdown.set()
        if self._meeting_esc:
            self._meeting_esc.stop()
        for processor in self._processors.values():
            processor.cancel()
        if self._meeting_session:
            self._meeting_session.close()
            self._meeting_session = None
