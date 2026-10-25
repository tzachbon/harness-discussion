"""Shared dictation recording, transcription and guarded delivery lifecycle."""
import logging
import threading
import time
from dataclasses import dataclass, field

from . import dictation, history, mic, writer
from . import writer_settings as ws

log = logging.getLogger("harness_discussion")
START_TIMEOUT_S, STOP_TIMEOUT_S, TRANSCRIBE_TIMEOUT_S = 30, 10, 180


@dataclass
class _Recording:
    generation: int
    binding: int
    target: object
    device: str = ""
    recorder: object = None
    activity: threading.Event = field(default_factory=threading.Event)
    permission: threading.Event = field(default_factory=threading.Event)
    stop: threading.Event = field(default_factory=threading.Event)
    cancel: threading.Event = field(default_factory=threading.Event)
    granted: bool = False
    phase: str = "preparing"
    thread: object = None
    rid: int = 0


class Dictation:
    def _dictation_current(self, session):
        return (not self._writer_closed and not session.cancel.is_set()
                and session.generation == self._dictate_generation and session.binding == self._dictate_binding)

    def _invalidate_dictation_target(self):
        """Native callbacks latch activity before the UI can process a delayed result."""
        self._dictate_activity()
        if self._dictation_target_activity is not None:
            self._dictation_target_activity.set()

    def _dictation_after(self, session, seconds, phase):
        event = ("dictation_timer", session.generation, session.binding, phase)
        self.host.after(int(seconds * 1000), lambda: self.events.put(event))

    def _dictate_down(self, binding=None, activity=None, cancel=None):
        """Reserve ownership before permission, then let one worker own all device calls."""
        if (binding is not None and binding != self._dictate_binding
                or cancel is not None and cancel.is_set()
                or not (self.writer_ready and self.cfg["writer"]) or self.rewriting or self._writer_closed):
            return
        owner = self._recorder or self._dictation_job
        if owner is not None:
            if owner.cancel.is_set():
                self._toast("The previous dictation is still finishing. Please wait.")
            return
        self._dictate_generation += 1
        self._summary_gen += 1
        self._press_gen += 1
        self._press_at = 0.0
        if hasattr(self, "_seq"):
            self._seq += 1
        if hasattr(self, "_hotkey_wait"):
            self._hotkey_wait = None
        try:
            identity = getattr(self.host, "dictation_target", lambda: None)()
        except Exception:
            identity = None
        session = _Recording(self._dictate_generation, self._dictate_binding, identity,
                             device=self.cfg.get("dictate_microphone", ""),
                             activity=activity if activity is not None else threading.Event(),
                             cancel=cancel if cancel is not None else threading.Event())
        self._dictation_target_activity = session.activity
        self._recorder, self._holding, self._dictate_ctx = session, True, None
        self._recording_toast(session, "Preparing microphone")
        self._dictation_after(session, START_TIMEOUT_S, "preparing")
        session.thread = threading.Thread(target=self._record, args=(session,), daemon=True)
        session.thread.start()
        event = ("mic_answer", session.generation, session.binding)
        try:
            self.host.mic_permission(lambda granted: self.events.put((*event, bool(granted))))
        except Exception:
            self.events.put((*event, False))

    def _mic_answer(self, generation, binding, granted):
        session = self._recorder
        if (session is None or (generation, binding) != (session.generation, session.binding)
                or not self._dictation_current(session) or session.permission.is_set()):
            return
        session.granted = granted
        session.permission.set()

    def _record(self, session):
        """Only this worker may start, stop or close the native stream."""
        audio, duration, ctx, error = b"", 0.0, None, None
        generation, binding = session.generation, session.binding
        stage = "permission"
        try:
            session.permission.wait()
            if session.stop.is_set():
                return
            if not session.granted:
                raise PermissionError()
            stage = "target capture"
            ctx = self.edit.capture(False)
            if ctx is not None and ctx.secure:
                raise PermissionError()
            # Never adopt a different window if worker-side capture was delayed.
            if ctx is not None and (ctx.app, ctx.window) != session.target:
                ctx = None
            if session.stop.is_set():
                return
            recorder = mic.Recorder(device=session.device)
            session.recorder = recorder
            stage = "start"
            recorder.start()
            ready = not session.stop.is_set()
            try:
                if ready:
                    self.events.put(("recording_ready", generation, binding, time.monotonic()))
                    session.stop.wait(mic.MAX_SECONDS)
            finally:
                session.stop.set()
                self.events.put(("recording_stopping", generation, binding))
                stage = "stop"
                audio = recorder.stop()
                duration = recorder.duration
                if not ready:
                    audio, duration = b"", 0.0
        except Exception as e:
            log.info("dictation recording failed during %s: %s", stage, type(e).__name__)
            error = "The microphone could not finish the recording."
        finally:
            self.events.put(("recording_done", generation, binding, audio, duration, ctx, error))

    def _recording_ready(self, generation, binding, started):
        session = self._recorder
        if (session is None or (generation, binding) != (session.generation, session.binding)
                or not self._dictation_current(session) or session.stop.is_set()):
            return
        self._recorded_at = started
        session.phase = "listening"
        self._recording_toast(session, "Listening")
        self._show_input_level(session)
        first_stt = next(iter(writer.chain(self.stt_sources, self.cfg["stt_sources"])), None)
        if first_stt is not None and hasattr(first_stt, "prewarm"):
            threading.Thread(target=first_stt.prewarm, args=(), daemon=True).start()
        if self.cfg["dictate_polish"]:
            first_writer = next(iter(self._dictation_writers()), None)
            if first_writer is not None and hasattr(first_writer, "prewarm"):
                model = (self.cfg["writer_models"].get(first_writer.name) or {}).get("model")
                threading.Thread(target=first_writer.prewarm, args=(model,), daemon=True).start()
        self._dictation_after(session, mic.MAX_SECONDS, "limit")

    def _recording_toast(self, session, message):
        def cancel():
            if self._recorder is session:
                self._cancel_dictation()
                self.toast.hide()
        self._toast(message, [("×", cancel)], busy=True)

    def _show_input_level(self, session):
        if (self._recorder is not session or not self._holding or session.phase != "listening"
                or session.stop.is_set() or not self._dictation_current(session)):
            return
        self.toast.set_input_level(session.recorder.level())
        self.host.after(100, lambda: self._show_input_level(session))

    def _recording_stopping(self, generation, binding):
        session = self._recorder
        if (session is None or (generation, binding) != (session.generation, session.binding)
                or not self._dictation_current(session) or session.phase == "stopping"):
            return
        self._holding = False
        session.phase = "stopping"
        self._recording_toast(session, "Stopping microphone")
        self._dictation_after(session, STOP_TIMEOUT_S, "stopping")

    def _dictate_up(self, binding=None, activity=None):
        if binding is not None and binding != self._dictate_binding:
            return
        if activity is not None and self._recorder is not None and activity is not self._recorder.activity:
            return
        self._stop_recording()

    def _stop_recording(self):
        """Signal only. Ownership remains until the worker has completed stop and close."""
        self._holding = False
        session = self._recorder
        if session is not None:
            session.stop.set()
            session.permission.set()
            self._recording_stopping(session.generation, session.binding)
        self._watch_escape()

    def _cancel_dictation(self):
        self._dictate_generation += 1
        session = self._recorder
        if session is not None:
            session.cancel.set()
        self._stop_recording()
        if self._dictation_job is not None:
            self.cancel_rewrite()
        if self.toast and (session is not None or self._dictation_job is not None):
            self.toast.hide()

    def _dictation_timer(self, generation, binding, phase):
        session = self._dictation_job if phase == "transcribing" else self._recorder
        if (session is None or (generation, binding) != (session.generation, session.binding)
                or not self._dictation_current(session)):
            return
        if phase == "limit":
            self._stop_recording()
        elif phase == session.phase:
            self._cancel_dictation()
            self._toast("The dictation timed out. Waiting for cleanup.")

    def _worker_exited(self, session, event):
        if session.thread.is_alive():
            # A completion event can reach the UI just before the worker returns.
            self.host.after(10, lambda: self.events.put(event))
            return False
        return True

    def _recording_done(self, generation, binding, audio, duration, ctx, error):
        session = self._recorder
        if session is None or (generation, binding) != (session.generation, session.binding):
            return
        event = ("recording_done", generation, binding, audio, duration, ctx, error)
        if not self._worker_exited(session, event):
            return
        self._dictation_delivery = session
        try:
            self._recorder, self._holding = None, False
            self._arm_summary_stop()
            self._watch_escape()
            if not self._dictation_current(session):
                self.toast.hide()
                return
            if error:
                self._toast(error, [("Open Settings", self.host.open_mic_settings)])
            elif mic.is_tap(duration) or not audio:
                self.toast.hide()
            else:
                self._dictate_ctx = None if session.activity.is_set() else ctx
                self._transcribe(session, audio)
        finally:
            if self._dictation_delivery is session:
                self._dictation_delivery = None
                if session.cancel.is_set() and self._recorder is None and self._dictation_job is None:
                    self.toast.hide()

    def _transcribe(self, session, audio):
        released = time.monotonic()
        name = ws.default_mode(self.cfg["dictate_mode"], self.cfg["modes"], "Clean dictation")
        mode = self._find_mode(name) if self.cfg["dictate_polish"] else None
        self._rewrite_id += 1
        rid, cancel = self._rewrite_id, session.cancel
        session.rid, session.phase = rid, "transcribing"
        self._dictation_job = session
        self._rewrite_cancel = cancel
        self._set_rewriting(True)
        self._summary_gen += 1
        at = self._anchor()
        sources = self._dictation_writers() if mode else []
        models, order, chain = self.cfg["writer_models"], self.cfg["stt_sources"], self.stt_sources
        about = writer.about(self.cfg)
        keep = lambda: self.cfg.get("dictation_history")  # read live: turning it off mid-dictation saves nothing
        self._toast("Transcribing", [("×", lambda: self.cancel_rewrite() if rid == self._rewrite_id else None)],
                    busy=True, at=at)
        self._dictation_after(session, TRANSCRIBE_TIMEOUT_S, "transcribing")

        def work():
            stt_s = rewrite_s = stt_name = None
            try:
                text, stt_name = dictation.transcribe(audio, chain, order, cancelled=cancel.is_set)
                stt_s = time.monotonic() - released
                if cancel.is_set():
                    raise writer.Cancelled("The dictation was cancelled.")
                kept = _keep(text, keep)
                if mode:
                    started = time.monotonic()
                    try:
                        text, source = writer.rewrite(text, mode, sources, models=models, cancelled=cancel.is_set,
                                                      about=about)
                    finally:
                        rewrite_s = time.monotonic() - started
                    if kept is not None:
                        _keep_tidied(kept, text, keep)
                    result = (text, stt_name, source)
                else:
                    result = (text, stt_name, None)
            except Exception as e:
                result = e
            timing = (released, stt_s, rewrite_s, stt_name)
            self.events.put(("dictated", rid, session.generation, session.binding,
                             mode.name if mode else None, result, at, timing))
        session.thread = threading.Thread(target=work, daemon=True)
        session.thread.start()

    def history_changed(self):
        getattr(self.host, "history_changed", lambda: None)()
        win = getattr(self, "settings_win", None)
        if win and win.alive and hasattr(win, "refresh_history"):
            win.refresh_history()

    def _dictated(self, rid, generation, binding, mode, result, at, timing):
        session = self._dictation_job
        if session is None or (rid, generation, binding) != (session.rid, session.generation, session.binding):
            return
        event = ("dictated", rid, generation, binding, mode, result, at, timing)
        if not self._worker_exited(session, event):
            return
        self._dictation_delivery = session
        try:
            self._dictation_job = None
            self.history_changed()
            if rid != self._rewrite_id:
                return
            self._set_rewriting(False)
            self._arm_summary_stop()
            if not self._dictation_current(session):
                self.toast.hide()
                return
            released, stt_s, rewrite_s, stt_name = timing
            secs = lambda s: f"{s:.1f}s" if s is not None else "-"
            if isinstance(result, writer.Cancelled):
                self.toast.hide()
                return
            if isinstance(result, Exception):
                log.info("dictation failed: %s after %.1fs (transcribe %s with %s, rewrite %s)", type(result).__name__,
                         time.monotonic() - released, secs(stt_s), stt_name or "-", secs(rewrite_s))
                safe = isinstance(result, (writer.Error, dictation.Error))
                self._toast(str(result) if safe else "Could not finish the dictation.", at=at)
                return
            text, stt_name, source = result
            log.info("dictated %d chars with %s%s in %.1fs (transcribe %.1fs, rewrite %s)", len(text), stt_name,
                     f" then {source}" if source else "", time.monotonic() - released, stt_s, secs(rewrite_s))
            current = lambda: self._dictation_current(session) and rid == self._rewrite_id
            copy = lambda: self.host.copy_text(text)
            ctx = self._dictate_ctx
            try:
                now = self.edit.capture(False) if ctx is not None and not session.activity.is_set() else None
            except Exception:
                now = None
            if not current():
                self.toast.hide()
                return
            can_insert = (not session.activity.is_set() and ctx is not None and not ctx.secure
                          and now is not None and not now.secure
                          and (now.app, now.window) == (ctx.app, ctx.window))
            if can_insert:
                try:
                    placed = self.edit.type_at_caret(text, ctx, cancelled=lambda: session.activity.is_set() or not current())
                    attempted = getattr(ctx, "_insertion_attempted", False)
                except Exception:
                    placed = False
                    # An adapter without attempt metadata cannot prove a native exception sent no input.
                    attempted = getattr(ctx, "_insertion_attempted", True)
                if not current() or placed or attempted:
                    self.toast.hide()
                    return
            if not current():
                self.toast.hide()
                return
            try:
                copied = self.host.copy_text(text, cancelled=lambda: not current())
            except Exception:
                copied = False
            if not current():
                self.toast.hide()
                return
            if copied:
                self._toast("Copied to clipboard", at=at)
            else:
                self._toast("Could not copy the dictation.", [("Copy", copy)], at=at)
        finally:
            if self._dictation_delivery is session:
                self._dictation_delivery = None
                if session.cancel.is_set() and self._recorder is None and self._dictation_job is None:
                    self.toast.hide()

def _keep(raw, keep):
    try:
        return history.add(raw, keep)
    except OSError as e:  # history is a backup: never lose the dictation over it
        log.info("dictation history not saved: %s", type(e).__name__)

def _keep_tidied(at, text, keep):
    try:
        history.finish(at, text, keep)
    except OSError as e:
        log.info("dictation history not saved: %s", type(e).__name__)
