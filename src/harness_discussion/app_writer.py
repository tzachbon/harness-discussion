"""The Writer: rewrites the selection in place with a mode, then offers Undo, Review, and another mode.

Runs where the host provides `edit` (capture, same_context, replace, select_back, collapse), a toast, a review
panel, and an Esc watcher. The rewrite runs on a worker thread. Pasting happens on the main thread, and only
when the app, window, and selection are still the ones captured.
"""
import logging
import sys
import threading
import time
from dataclasses import dataclass, field

from . import dictation, edit_logic, hotkeys, mic, sources, writer
from . import writer_settings as ws
from .llm_codex import CodexWriter
from .llm_cursor import CursorWriter
from .summarize import LocalWriter

log = logging.getLogger("harness_discussion")
DOUBLE_PRESS_S = 0.35
START_TIMEOUT_S, STOP_TIMEOUT_S, TRANSCRIBE_TIMEOUT_S = 30, 10, 180
UNDO_KEYS = "Cmd+Z" if sys.platform == "darwin" else "Ctrl+Z"


@dataclass
class _Recording:
    generation: int
    binding: int
    target: object
    activity: threading.Event = field(default_factory=threading.Event)
    permission: threading.Event = field(default_factory=threading.Event)
    stop: threading.Event = field(default_factory=threading.Event)
    cancel: threading.Event = field(default_factory=threading.Event)
    granted: bool = False
    phase: str = "preparing"
    thread: object = None
    rid: int = 0


def _edges(text):
    """(leading, trailing) whitespace, kept around the rewrite so a selected line keeps its line break."""
    core = text.strip()
    if not core:
        return text, ""
    start = text.index(core)
    return text[:start], text[start + len(core):]


class Writer:
    rewriting, writer_ready = False, False
    WRITER_EVENTS = {"rewrite": "_rewrite_done", "rewrite_hotkey": "_rewrite_hotkey", "dictate_down": "_dictate_down",
                     "dictate_up": "_dictate_up", "dictated": "_dictated", "writer_info": "_writer_info",
                     "mic_answer": "_mic_answer", "recording_ready": "_recording_ready",
                     "recording_stopping": "_recording_stopping", "recording_done": "_recording_done",
                     "dictation_timer": "_dictation_timer"}

    def _init_writer(self):
        self.edit = getattr(self.host, "edit", None)
        self.writer_ready = self.edit is not None
        self.writer_sources = [CursorWriter(), CodexWriter(), LocalWriter(self.summary_server)]
        self._rewrite_id, self._rewrite_cancel, self.rewriting = 0, threading.Event(), False
        self._last_edit = None  # (ctx, original, inserted, mode) of the latest in-place rewrite
        self._press_at, self._press_gen, self._escaped_at = 0.0, 0, 0.0
        self.toast = self.review = self._esc = None
        self.stt_sources = dictation.sources()
        self.writer_statuses, self.stt_statuses, self.writer_models = {}, {}, {}
        self._writer_checking = threading.Lock()
        self._recorder, self._recorded_at, self._dictate_ctx, self._holding = None, 0.0, None, False
        self._dictate_generation = 0
        self._dictate_binding, self._writer_closed, self._dictation_job = 0, False, None
        self._dictate_activity = lambda: None
        self._dictation_target_activity = None
        if self.writer_ready:
            self.toast, self.review = self.host.make_toast(), self.host.make_review()
            self.toast.on_hide = self._watch_escape
            self._esc = self.host.esc_watcher(self._escape)

    def refresh_writer(self):
        """Settings shows cached statuses and model lists. Both can take seconds, so a worker fills them."""
        if not self._writer_checking.acquire(blocking=False):
            return

        def check(source):
            try:
                return source.status()
            except Exception as e:
                log.info("%s status failed: %s", source.name, type(e).__name__)
                return "missing"

        def work():
            try:
                statuses = {s.name: check(s) for s in self.writer_sources}
                models = dict(self.writer_models)
                for s in self.writer_sources:
                    if statuses[s.name] == "ready" and hasattr(s, "models"):
                        try:
                            models[s.name] = s.models()
                        except Exception as e:
                            log.info("%s models failed: %s", s.name, type(e).__name__)
                self.writer_statuses, self.writer_models = statuses, models
                self.stt_statuses = {s.name: check(s) for s in self.stt_sources}
            finally:
                self._writer_checking.release()
            self.events.put(("writer_info",))
        threading.Thread(target=work, daemon=True).start()

    def _writer_info(self):
        if self.settings_win and self.settings_win.alive and hasattr(self.settings_win, "refresh_writing"):
            self.settings_win.refresh_writing()

    def fix_writer(self, name, stt=False):
        """Sign in or install for Codex and Cursor. Local downloads its model from the Summarize page."""
        status = (self.stt_statuses if stt else self.writer_statuses).get(name)
        if name == "Local":
            self.summarize_action()
        elif found := sources.fix(name, status):
            try:
                found[1]()
            except Exception as e:
                self.host.notify(f"Could not open {name}: {e}")
                return
        for s in sources.RECHECK_S:
            self.host.after(s * 1000, self.refresh_writer)

    def mode_names(self):
        return [m.name for m in writer.all_modes(self.cfg["modes"])]

    def _find_mode(self, name):
        modes = writer.all_modes(self.cfg["modes"])
        return writer.find_mode(name or self.cfg["default_mode"], modes)

    def _anchor(self):
        """Where a toast goes: under a visible bar, which stays up showing "Rewriting…", or by the pointer.
        The third value is the bar's top, for the toast to sit above the bar when there is no room under it."""
        if self.popup.visible:
            x, y, h = self.popup.anchor()
            return x, y + h + 6, y
        x, y = self.host.pointer()
        return x + 12, y + 16, None

    def _toast(self, message, actions=(), busy=False, at=None):
        x, y, above = at or self._anchor()
        self.toast.show(message, x, y, actions, busy, above=above)
        self._watch_escape()

    def _watch_escape(self):
        if self._esc:
            self._esc.start() if self.rewriting or self._holding or self.toast.visible else self._esc.stop()

    def rewrite_from_bar(self, text, mode_name):
        self.rewrite_selection(mode_name)

    def rewrite_selection(self, mode_name=None, prev=None):
        """Captures the selection, rewrites it on a worker, and hands the result to `_rewrite_done`.
        prev: (ctx, original, inserted) to rewrite the original again in place of the inserted text."""
        if (not (self.writer_ready and self.cfg["writer"]) or self.rewriting
                or self._recorder is not None or self._dictation_job is not None or self._writer_closed):
            return
        mode = self._find_mode(mode_name)
        at = self._anchor()
        self._rewrite_id += 1
        rid, cancel = self._rewrite_id, threading.Event()
        self._rewrite_cancel = cancel
        self._set_rewriting(True)
        self.popup.set_last_mode(mode.name)
        self._summary_gen += 1  # a summary's idle stop must not stop the local model under this rewrite
        sources = writer.chain(self.writer_sources, self.cfg["writer_sources"])
        models, clipboard = self.cfg["writer_models"], self.cfg["clipboard_fallback"]
        self._toast(f"Rewriting · {mode.name}", [("Cancel", self.cancel_rewrite)], busy=True, at=at)

        def work():
            ctx = prev[0] if prev else None
            try:
                if prev is None:
                    ctx = self.edit.capture(clipboard)
                    if ctx is not None and ctx.secure:
                        raise writer.Error("Password fields are never rewritten.")
                    if ctx is None or not ctx.text.strip():
                        raise writer.Error("Select text to rewrite.")
                text = prev[1] if prev else ctx.text
                result = writer.rewrite(text, mode, sources, models=models, cancelled=cancel.is_set)
            except Exception as e:
                result = e
            self.events.put(("rewrite", rid, ctx, mode.name, result, prev, at))
        threading.Thread(target=work, daemon=True).start()

    def _set_rewriting(self, on):
        self.rewriting = on
        self.popup.set_rewriting(on)
        self._watch_escape()

    def _rewrite_done(self, rid, ctx, mode, result, prev, at):
        if rid != self._rewrite_id:
            return
        self._set_rewriting(False)
        self._arm_summary_stop()
        if isinstance(result, writer.Cancelled):
            self.toast.hide()
            return
        if isinstance(result, Exception):
            message = str(result) if isinstance(result, writer.Error) else "Could not rewrite this text."
            log.info("rewrite failed: %s", type(result).__name__)
            self._toast(message, at=at)
            return
        new, source = result
        original = prev[1] if prev else ctx.text
        lead, trail = _edges(original)
        inserted = lead + new + trail
        builtin = mode in {m.name for m in writer.BUILTIN_MODES}
        log.info("rewrote %d chars with %s in mode %s", len(original), source, mode if builtin else "custom")
        copy = lambda: self.host.copy_text(inserted)
        review_copy = lambda: self.review.show(original, inserted, None, self.host.copy_text)
        self.popup.hide()
        if prev:
            placed = edit_logic.undo(self.edit, ctx, inserted, prev[2], self.edit.NEWLINE)
        elif not self.edit.same_context(ctx):
            self._toast("Text changed. Copy the rewrite?", [("Copy", copy), ("Review", review_copy)], at=at)
            return
        else:
            placed = self.edit.replace(inserted, ctx)
        if not placed:
            self._toast("Couldn't paste here.", [("Copy", copy), ("Review", review_copy)], at=at)
            return
        self._last_edit = (ctx, original, inserted, mode)
        modes = [(m, lambda m=m: self._rewrite_again(m), m == mode) for m in self.mode_names()]
        self._toast(f"{mode} · {source}", [("Undo", self.undo_last), ("Review", self.open_review),
                                           ("Mode", modes)], at=at)

    def _rewrite_again(self, mode_name):
        if self._last_edit:
            ctx, original, inserted, _ = self._last_edit
            self.rewrite_selection(mode_name, prev=(ctx, original, inserted))

    def undo_last(self):
        if not self._last_edit:
            return
        ctx, original, inserted, _ = self._last_edit
        if edit_logic.undo(self.edit, ctx, original, inserted, self.edit.NEWLINE):
            self._last_edit = None
            self._toast("Original restored.")
        else:
            self._toast(f"The text changed. Use {UNDO_KEYS}.")

    def open_review(self):
        if not self._last_edit:
            return
        ctx, original, inserted, mode = self._last_edit

        def replace(text):
            nonlocal inserted
            if edit_logic.undo(self.edit, ctx, text, inserted, self.edit.NEWLINE):
                self._last_edit = (ctx, original, text, mode)
                inserted = text
            else:
                self._toast("The text changed. Copy the result?", [("Copy", lambda: self.host.copy_text(text))])
        self.review.show(original, inserted, replace, self.host.copy_text)

    def _rewrite_hotkey(self):
        """One press rewrites with the default mode. A second press within 350 ms shows the mode menu."""
        if self._recorder is not None or self._dictation_job is not None or self._writer_closed:
            return
        now = time.monotonic()
        self._press_gen += 1
        gen = self._press_gen
        if now - self._press_at < DOUBLE_PRESS_S:
            self._press_at = 0.0
            self.host.pick_mode(self.mode_names(), self.cfg["default_mode"], self.rewrite_selection)
            return
        self._press_at = now
        self.host.after(int(DOUBLE_PRESS_S * 1000), lambda: gen == self._press_gen and self.rewrite_selection())

    def _apply_writer(self, key, value):
        put = self.events.put
        if key == "rewrite_hotkey":
            taken = hotkeys.conflict(self.cfg, key, value) == "hotkey"  # the read shortcut wins a tie
            self.host.set_shortcut("rewrite", "" if taken else value, lambda: put(("rewrite_hotkey",)))
        elif key == "dictate_hotkey":
            self._dictate_binding += 1
            binding = self._dictate_binding
            self._cancel_dictation()
            taken = hotkeys.conflict(self.cfg, key, value) is not None
            edge_lock, current = threading.Lock(), [None]

            def down():
                with edge_lock:
                    if current[0] is not None:
                        current[0].set()
                    current[0] = threading.Event()
                    put(("dictate_down", binding, current[0]))

            def up():
                with edge_lock:
                    put(("dictate_up", binding, current[0]))

            def activity():
                with edge_lock:
                    if current[0] is not None:
                        current[0].set()

            self._dictate_activity = activity
            self.host.set_shortcut("dictate", "" if taken else value, down, up, on_activity=activity)
        elif hasattr(self, "popup"):
            self.popup.set_last_mode(value)

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

    def _dictate_down(self, binding=None, activity=None):
        """Reserve ownership before permission, then let one worker own all device calls."""
        if (binding is not None and binding != self._dictate_binding
                or not (self.writer_ready and self.cfg["writer"]) or self.rewriting or self._writer_closed):
            return
        owner = self._recorder or self._dictation_job
        if owner is not None:
            if owner.cancel.is_set():
                self._toast("The previous dictation is still finishing. Please wait.")
            return
        self._dictate_generation += 1
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
                             activity=activity if activity is not None else threading.Event())
        self._dictation_target_activity = session.activity
        self._recorder, self._holding, self._dictate_ctx = session, True, None
        self._toast("Preparing microphone", [("Cancel", self._cancel_dictation)], busy=True)
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
        """No device method, including failure cleanup, may escape this worker."""
        audio, duration, ctx, error = b"", 0.0, None, None
        generation, binding = session.generation, session.binding
        try:
            session.permission.wait()
            if session.stop.is_set():
                return
            if not session.granted:
                raise PermissionError()
            ctx = self.edit.capture(False)
            if ctx is not None and ctx.secure:
                raise PermissionError()
            # Never adopt a different window if worker-side capture was delayed.
            if ctx is not None and (ctx.app, ctx.window) != session.target:
                ctx = None
            if session.stop.is_set():
                return
            recorder = mic.Recorder()
            recorder.start()
            ready = not session.stop.is_set()
            try:
                if ready:
                    self.events.put(("recording_ready", generation, binding, time.monotonic()))
                    session.stop.wait(mic.MAX_SECONDS)
            finally:
                session.stop.set()
                self.events.put(("recording_stopping", generation, binding))
                audio = recorder.stop()
                duration = recorder.duration
                if not ready:
                    audio, duration = b"", 0.0
        except Exception:
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
        first = next((n for n, on in self.cfg["stt_sources"] if on), "Cursor")
        self._toast(f"Listening · {first}", [("Cancel", self._cancel_dictation)], busy=True)
        self._dictation_after(session, mic.MAX_SECONDS, "limit")

    def _recording_stopping(self, generation, binding):
        session = self._recorder
        if (session is None or (generation, binding) != (session.generation, session.binding)
                or not self._dictation_current(session) or session.phase == "stopping"):
            return
        self._holding = False
        session.phase = "stopping"
        self._toast("Stopping microphone", [("Cancel", self._cancel_dictation)], busy=True)
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
        self._recorder, self._holding = None, False
        self._watch_escape()
        if not self._dictation_current(session):
            return
        if error:
            self._toast(error, [("Open Settings", self.host.open_mic_settings)])
        elif mic.is_tap(duration) or not audio:
            self.toast.hide()
        else:
            self._dictate_ctx = None if session.activity.is_set() else ctx
            self._transcribe(session, audio)

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
        sources = writer.chain(self.writer_sources, self.cfg["writer_sources"])
        models, order, chain = self.cfg["writer_models"], self.cfg["stt_sources"], self.stt_sources
        self._toast(f"Transcribing · {mode.name}" if mode else "Transcribing", [("Cancel", self.cancel_rewrite)],
                    busy=True, at=at)
        self._dictation_after(session, TRANSCRIBE_TIMEOUT_S, "transcribing")

        def work():
            stt_s = rewrite_s = stt_name = None
            try:
                text, stt_name = dictation.transcribe(audio, chain, order, cancelled=cancel.is_set)
                stt_s = time.monotonic() - released
                if cancel.is_set():
                    raise writer.Cancelled("The dictation was cancelled.")
                if mode:
                    started = time.monotonic()
                    try:
                        text, source = writer.rewrite(text, mode, sources, models=models, cancelled=cancel.is_set)
                    finally:
                        rewrite_s = time.monotonic() - started
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

    def _dictated(self, rid, generation, binding, mode, result, at, timing):
        session = self._dictation_job
        if session is None or (rid, generation, binding) != (session.rid, session.generation, session.binding):
            return
        event = ("dictated", rid, generation, binding, mode, result, at, timing)
        if not self._worker_exited(session, event):
            return
        self._dictation_job = None
        if rid != self._rewrite_id or not self._dictation_current(session):
            return
        released, stt_s, rewrite_s, stt_name = timing
        secs = lambda s: f"{s:.1f}s" if s is not None else "-"
        self._set_rewriting(False)
        self._arm_summary_stop()
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
        copy = lambda: self.host.copy_text(text)
        changed = lambda: self._toast("The text field changed. Copy the text?", [("Copy", copy)], at=at)
        if session.activity.is_set():
            changed()
            return
        ctx = self._dictate_ctx
        try:
            now = self.edit.capture(False) if ctx is not None else None
        except Exception:
            self._toast("Could not check the text field. Copy the text?", [("Copy", copy)], at=at)
            return
        if session.activity.is_set():
            changed()
            return
        if ctx is None or ctx.secure or now is None or (now.app, now.window) != (ctx.app, ctx.window) or now.secure:
            self._toast("The window changed. Copy the text?", [("Copy", copy)], at=at)
            return
        try:
            cancelled = lambda: (session.activity.is_set() or not self._dictation_current(session)
                                 or rid != self._rewrite_id)
            placed = self.edit.type_at_caret(text, ctx, cancelled=cancelled)
        except Exception:
            placed = False
        if not placed:
            self._toast("Couldn't type here.", [("Copy", copy)], at=at)
            return
        self._toast(f"Dictated · {mode} · {source}" if mode else f"Dictated · {stt_name}", [("Copy", copy)], at=at)

    def _escape(self):
        """Esc cancels a rewrite, else hides the toast, else dismisses the bar. Both watchers can see one Esc."""
        now = time.monotonic()
        if now - self._escaped_at < 0.15:
            return
        self._escaped_at = now
        self._press_gen += 1
        if self._recorder or self._holding:
            self._cancel_dictation()
            self.toast.hide()
        elif self.rewriting:
            self.cancel_rewrite()
        elif self.toast and self.toast.visible:
            self.toast.hide()
        elif self.popup.visible:
            self.popup.close()

    def cancel_rewrite(self):
        """Esc or the busy toast's Cancel: stop the rewrite or dictation and drop its late result."""
        if not self.rewriting:
            return
        self._rewrite_cancel.set()
        self._rewrite_id += 1
        self._set_rewriting(False)
        self._arm_summary_stop()
        self.toast.hide()

    def _close_writer(self):
        if not hasattr(self, "_rewrite_cancel"):  # quit before _init_writer ran
            return
        self._press_gen += 1
        self._writer_closed = True
        self._dictate_binding += 1
        self._cancel_dictation()
        self._rewrite_id += 1
        self._rewrite_cancel.set()
        for source in self.writer_sources:
            close = getattr(source, "close", None)
            if close:
                close()
        if self._esc:
            self._esc.stop()
            self.host.set_shortcut("rewrite", "", None)
            self.host.set_shortcut("dictate", "", None)
