"""The Writer: rewrites the selection in place with a mode, then offers Undo, Review, and another mode.

Runs where the host provides `edit` (capture, same_context, replace, select_back, collapse), a toast, a review
panel, and an Esc watcher. The rewrite runs on a worker thread. Pasting happens on the main thread, and only
when the app, window, and selection are still the ones captured.
"""
import logging
import sys
import threading
import time

from . import dictation, edit_logic, hotkeys, local_stt, mic, sources, writer
from . import writer_settings as ws
from .llm_codex import CodexWriter
from .llm_cursor import CursorWriter
from .summarize import LocalWriter

log = logging.getLogger("harness_discussion")
DOUBLE_PRESS_S = 0.35
UNDO_KEYS = "Cmd+Z" if sys.platform == "darwin" else "Ctrl+Z"


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
                     "local_stt_downloaded": "_local_stt_downloaded"}

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
        self._local_stt_downloading = False
        self._writer_checking = threading.Lock()
        self._recorder, self._recorded_at, self._dictate_ctx, self._holding = None, 0.0, None, False
        self._dictate_generation = 0
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

    def _dictation_writers(self):
        chain = writer.chain(self.writer_sources, self.cfg["writer_sources"])
        if self.cfg.get("dictate_local_tidy", True):
            local = next((s for s in chain if s.name == "Local"), None)
            try:
                if local is not None and local.status() == "ready":
                    chain = [local] + [s for s in chain if s is not local]
            except Exception as e:
                log.info("Local writer status failed: %s", type(e).__name__)
        return chain

    def fix_writer(self, name, stt=False):
        """Sign in, install, or download the distinct local speech/writing model."""
        status = (self.stt_statuses if stt else self.writer_statuses).get(name)
        if name == "Local" and stt:
            if self._local_stt_downloading:
                return
            self._local_stt_downloading = True
            self.stt_statuses["Local"] = "downloading"
            self._writer_info()
            self.host.notify("Downloading Whisper Small for local dictation (486 MB).")
            def work():
                error = None
                try:
                    local_stt.download()
                except Exception as e:
                    log.info("Local dictation download failed: %s", type(e).__name__)
                    error = True
                self.events.put(("local_stt_downloaded", error))
            threading.Thread(target=work, daemon=True).start()
            self.refresh_writer()
            return
        elif name == "Local":
            self.summarize_action()
        elif found := sources.fix(name, status):
            try:
                found[1]()
            except Exception as e:
                self.host.notify(f"Could not open {name}: {e}")
                return
        for s in sources.RECHECK_S:
            self.host.after(s * 1000, self.refresh_writer)

    def _local_stt_downloaded(self, error):
        self._local_stt_downloading = False
        self.stt_statuses["Local"] = "ready" if local_stt.downloaded() else "missing"
        self._writer_info()
        self.host.notify("Could not download the local dictation model. Try again in Settings → Dictation."
                         if error else "Local dictation is ready.")
        self.refresh_writer()

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
        if not (self.writer_ready and self.cfg["writer"]) or self.rewriting:
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
            taken = hotkeys.conflict(self.cfg, key, value) is not None
            self.host.set_shortcut("dictate", "" if taken else value, lambda: put(("dictate_down",)),
                                   lambda: put(("dictate_up",)))
        elif hasattr(self, "popup"):
            self.popup.set_last_mode(value)

    def _dictate_down(self):
        """Hold: check the microphone, then record until release."""
        if not (self.writer_ready and self.cfg["writer"]) or self.rewriting or self._recorder:
            return
        self._dictate_generation += 1
        generation = self._dictate_generation
        self._dictate_ctx = self.edit.capture(False)
        self._holding = True
        self._watch_escape()
        self.host.mic_permission(lambda granted: generation == self._dictate_generation
                                 and self._holding and self._mic_answer(granted))

    def _mic_answer(self, granted):
        if not granted:
            self._holding = False
            self._toast("Harness Discussion needs the microphone.", [("Open Settings", self.host.open_mic_settings)])
            return
        if not self._holding:
            return  # released while the permission prompt was up
        try:
            recorder = mic.Recorder(device=self.cfg.get("dictate_microphone", ""))
            recorder.start()
        except Exception as e:
            self._holding = False
            log.info("microphone failed: %s", type(e).__name__)
            self._toast("The microphone could not start. Choose an input in Settings → Dictation.",
                        [("Settings", lambda: self.events.put(("settings",)))])
            return
        self._recorder, self._recorded_at = recorder, time.monotonic()
        self._summary_gen += 1  # an old idle timer must not stop a local Tidy warmup during recording
        def cancel():
            if self._recorder is recorder:
                self._stop_recording()
                self.toast.hide()
        self._toast("Listening", [("×", cancel)], busy=True)
        self._show_input_level(recorder)
        first_stt = next(iter(writer.chain(self.stt_sources, self.cfg["stt_sources"])), None)
        if first_stt is not None and hasattr(first_stt, "prewarm"):
            threading.Thread(target=first_stt.prewarm, args=(), daemon=True).start()
        if self.cfg["dictate_polish"]:  # get the rewrite ready while the user speaks
            writer_first = next(iter(self._dictation_writers()), None)
            if writer_first is not None and hasattr(writer_first, "prewarm"):
                model = (self.cfg["writer_models"].get(writer_first.name) or {}).get("model")
                threading.Thread(target=writer_first.prewarm, args=(model,), daemon=True).start()

    def _show_input_level(self, recorder):
        if self._recorder is not recorder or not self._holding:
            return
        self.toast.set_input_level(recorder.level())
        self.host.after(100, lambda: self._show_input_level(recorder))

    def _dictate_up(self):
        held = time.monotonic() - self._recorded_at
        audio = self._stop_recording()
        if audio is None:
            return
        released = time.monotonic()
        if mic.is_tap(held) or not audio:
            self.toast.hide()
            return
        name = ws.default_mode(self.cfg["dictate_mode"], self.cfg["modes"], "Clean dictation")
        mode = self._find_mode(name) if self.cfg["dictate_polish"] else None
        self._rewrite_id += 1
        rid, cancel = self._rewrite_id, threading.Event()
        self._rewrite_cancel = cancel
        self._set_rewriting(True)
        self._summary_gen += 1
        at = self._anchor()
        sources = self._dictation_writers() if mode else []
        models, order, chain = self.cfg["writer_models"], self.cfg["stt_sources"], self.stt_sources
        self._toast("Transcribing", [("×", lambda: self.cancel_rewrite() if rid == self._rewrite_id else None)],
                    busy=True, at=at)

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
            self.events.put(("dictated", rid, mode.name if mode else None, result, at, timing))
        threading.Thread(target=work, daemon=True).start()

    def _stop_recording(self):
        self._holding = False
        self._dictate_generation += 1
        recorder, self._recorder = self._recorder, None
        self._watch_escape()
        if recorder is None:
            return None
        try:
            return recorder.stop()
        except Exception as e:
            log.info("microphone stop failed: %s", type(e).__name__)
            self._toast("The recording could not finish.", [("Open Settings", self.host.open_mic_settings)])
            return None
        finally:
            self._arm_summary_stop()  # taps and Esc also need to release the warmed writing model

    def _dictated(self, rid, mode, result, at, timing):
        if rid != self._rewrite_id:
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
        ctx, now = self._dictate_ctx, self.edit.capture(False)
        copy = lambda: self.host.copy_text(text)
        can_insert = (ctx is not None and now is not None and (now.app, now.window) == (ctx.app, ctx.window)
                      and not now.secure)
        if can_insert:
            inserted = self.edit.type_at_caret(text, ctx)
            attempted = getattr(ctx, "_insertion_attempted", False)
            if inserted or attempted:
                if not inserted:
                    log.info("dictation insertion: paste request unconfirmed")
                self.toast.hide()
                return
        if copy():
            self._toast("Copied to clipboard", at=at)
        else:
            self._toast("Could not copy the dictation.", [("Copy", copy)], at=at)

    def _escape(self):
        """Esc cancels a rewrite, else hides the toast, else dismisses the bar. Both watchers can see one Esc."""
        now = time.monotonic()
        if now - self._escaped_at < 0.15:
            return
        self._escaped_at = now
        self._press_gen += 1
        if self._recorder or self._holding:
            self._stop_recording()
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
        self._stop_recording()
        self._rewrite_cancel.set()
        for source in self.writer_sources + self.stt_sources:
            close = getattr(source, "close", None)
            if close:
                if source.name == "Local" and source in self.stt_sources:
                    threading.Thread(target=close, daemon=True).start()
                else:
                    close()
        if self._esc:
            self._esc.stop()
            self.host.set_shortcut("rewrite", "", None)
            self.host.set_shortcut("dictate", "", None)
