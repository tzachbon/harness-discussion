"""Check as you type: after a pause, the line at the caret goes through the Writer with the default mode, and each
change is underlined in the field. A click shows the suggestion with Apply, Dismiss, Review, and More. The rewrite
shortcut with nothing selected applies every underlined change.

`App` is core.App with this part; the platform entry points start it. A poll thread reads the field through `host.edit.observe()` (never the clipboard or keys) and asks for the
rectangles of every suggestion; the main thread draws them through `host.make_marks()`. Suggestions are derived
from the current text on each poll, so one never points at text that changed. Apply selects the exact span with
`edit.select_range` and pastes through the Writer's guarded `edit.replace`.
"""
import logging
import threading
import time

from . import core, live, writer
from .app_writer import UNDO_KEYS

log = logging.getLogger("harness_discussion")


class Live:
    """Mixed in before core.App's Writer, which owns host, cfg, edit, toast, review, and the rewrite state. Its init,
    quit, settings, events, and clicks run this part first."""
    LIVE_EVENTS = {"live_marks": "_live_marks", "live_check_start": "_live_check_start",
                   "live_check_done": "_live_check_done"}

    def _init_writer(self):
        super()._init_writer()
        self._init_live()

    def _close_writer(self):
        self._close_live()  # first: a poll in flight must not start a check on a closing source
        super()._close_writer()

    def apply(self, key, value):
        super().apply(key, value)
        if getattr(self, "live_ready", False):
            self._apply_live(key, value)

    def _handle(self, kind, *args):
        if kind in self.LIVE_EVENTS:
            getattr(self, self.LIVE_EVENTS[kind])(*args)
        elif not (kind == "press" and self._live_press(*args)):  # an underline or badge opens its card instead
            super()._handle(kind, *args)

    def rewrite_selection(self, mode_name=None, prev=None):
        if mode_name is None and prev is None and self._live_apply_all():
            return  # the shortcut, with nothing selected, applied the underlined changes
        self.live_cancel()  # a manual rewrite wins: a check in flight must not hold the source it needs
        super().rewrite_selection(mode_name, prev)

    def _dictate_down(self, *args, **kwargs):
        self.live_cancel()
        super()._dictate_down(*args, **kwargs)

    def _init_live(self):
        self.live_ready = self.writer_ready and all(
            hasattr(self.edit, f) for f in ("observe", "bounds", "select_range")) and hasattr(self.host, "make_marks")
        self.live_cache, self._live_dismissed = live.Cache(), set()
        self._live_cancel, self._live_checking = threading.Event(), None  # the line being checked, or None
        self._live_seen, self._live_text = None, ""  # (app, window, handle, digest) and text of the last poll
        self._live_failed = None  # the line every source last failed on
        self._live_changed_at = 0.0
        self._live_typed = None  # (app, window, handle, line start) of the line last typed in, while it is focused
        self._live_view, self._live_posted = None, None  # what the last live_marks event drew
        self._live_stop = threading.Event()
        self.marks = self.host.make_marks() if self.live_ready else None
        if self.live_ready:
            threading.Thread(target=self._live_loop, name="live-check", daemon=True).start()

    def _live_on(self):
        """Checking runs only when on and while no rewrite or dictation is under way."""
        busy = (self.rewriting or getattr(self, "_holding", False) or getattr(self, "_recorder", None) is not None
                or getattr(self, "_dictation_job", None) is not None)
        return self.cfg["writer"] and self.cfg.get("live_check") and not busy

    def _live_loop(self):
        while not self._live_stop.wait(live.POLL_S):
            try:
                self._live_tick()
            except Exception as e:
                log.info("live check poll failed: %s", type(e).__name__)

    def _live_post(self, field, found, placed, unplaced):
        """Posts what to draw only when it differs from the last post, so an idle field costs no main-thread work."""
        drawn = (field.digest, field.rect, tuple(s.key for s in found), tuple(placed), unplaced,
                 field.caret if unplaced else None) if found else None  # the badge reviews the caret's line
        if drawn != self._live_posted:
            self._live_posted = drawn
            self.events.put(("live_marks", field if found else None, found, placed, unplaced))

    def _live_hide(self):
        if self._live_posted is not None:
            self._live_posted = None
            self.events.put(("live_marks", None, [], [], 0))

    def _live_tick(self, now=None):
        """One poll: read the field, post what to draw, and start a check of a paused, uncached line."""
        now = time.monotonic() if now is None else now
        if not self._live_on():
            self._live_hide()
            return
        field = self.edit.observe()
        if field is None or field.app_name in self.cfg.get("live_off_apps", ()):
            self._live_hide()
            return
        seen = (field.app, field.window, field.handle, field.digest)
        start, end = live.paragraph_at(field.text, field.caret[0])
        if seen != self._live_seen:
            if self._live_seen is not None and seen[:3] == self._live_seen[:3]:  # typed in: the text changed
                a, b = live.edited(self._live_text, field.text)
                self._live_typed = (*seen[:3], start) if a <= end and start <= b else None  # not if the caret left
                self._live_failed = None
            elif self._live_typed is not None and self._live_typed[:3] != seen[:3]:
                self._live_typed = None  # another field: a line is sent only after typing in it
            self._live_seen, self._live_text, self._live_changed_at = seen, field.text, now
        mode = self._find_mode(None)
        found = live.suggestions(field.text, self.live_cache, self._live_dismissed, mode)[:live.MAX_SUGGESTIONS]
        rects = self.edit.bounds(field, [(s.start, s.end, s.old) for s in found]) if found else []
        if rects is not None:
            placed = [((s.start, *s.key), r) for s, rs in zip(found, rects) if rs for r in rs]
            self._live_post(field, found, placed, sum(rs is None for rs in rects))
        checking = self._live_checking
        if checking is not None and checking not in {line.strip() for _, line in live.lines(field.text)}:
            self._live_cancel.set()  # the line being checked is gone
        line = live.text_at(field.text, start, end).strip()
        if (checking is None and self._live_typed == (*seen[:3], start) and now - self._live_changed_at >= live.PAUSE_S
                and live.checkable(line) and line != self._live_failed and not self.live_cache.has(line, mode)
                and self._live_on()):
            self._live_check(line, mode)

    def _live_check(self, line, mode):
        self._live_checking, self._live_cancel = line, threading.Event()
        cancel = self._live_cancel
        sources = writer.chain(self.writer_sources, self.cfg["writer_sources"])
        models = self.cfg["writer_models"]
        self.events.put(("live_check_start",))

        def work():
            log.info("live check %d chars", len(line))  # before sending: proof of every send, never the text
            try:
                new, source = writer.rewrite(line, mode, sources, models=models,
                                             cancelled=lambda: cancel.is_set() or not self._live_on())
                log.info("live check done with %s", source)
                if not cancel.is_set():
                    self.live_cache.put(line, mode, new)
            except Exception as e:
                log.info("live check failed: %s", type(e).__name__)
                if not isinstance(e, writer.Cancelled):
                    self._live_failed = line  # every source failed: sent again only after more typing
            finally:
                self._live_checking = None
                self.events.put(("live_check_done",))
        threading.Thread(target=work, daemon=True).start()

    def _live_check_start(self):
        self._summary_gen += 1  # a summary's idle stop must not stop the local model under this check

    def _live_check_done(self):
        self._arm_summary_stop()

    def live_cancel(self):
        """A manual rewrite or dictation is starting: it wins over a check in flight."""
        self._live_cancel.set()

    # main thread

    def _live_marks(self, field, found, placed, unplaced):
        if not self.marks:
            return
        if field is None or not self._live_on():
            self._live_view = None
            self.marks.hide()
            return
        self._live_view = (field, {(s.start, *s.key): s for s in found})  # identical lines share s.key
        badge = None
        if unplaced:
            right, top = (field.rect[2], field.rect[1]) if field.rect else self.host.pointer()
            badge = (right, top, len(found))
        self.marks.show(placed, badge)

    def _live_press(self, x, y):
        """True when the click was on an underline or the badge; core then ignores it."""
        if not (self.marks and self._live_view) or self._ours(x, y):
            return False
        hit = self.marks.hit(x, y)
        if hit is None:
            return False
        field, by_key = self._live_view
        if hit == "badge":
            self._live_review(field)
        elif hit in by_key:
            self._live_card(field, by_key[hit], x, y)
        return True

    def _ours(self, x, y):
        """A click on the bar, the card or its menu, or Review, all of which may cover an underline."""
        return any(w is not None and (getattr(w, "menu_open", False) or w.contains(x, y))
                   for w in (self.popup, self.toast, self.review))

    def _live_card(self, field, s, x, y):
        old, new = (" ".join(t.split()) for t in (s.old, s.new))
        short = lambda t: t if len(t) <= 50 else t[:49] + "…"
        off = [(f"Turn off in {field.label}", lambda: self._live_turn_off(field), False)]
        self._toast(f"{short(old)} → {short(new)}", [("Apply", lambda: self._live_apply(field, s)),
                                                       ("Dismiss", lambda: self._live_dismiss(s)),
                                                       ("Review", lambda: self._live_review(field, s.start)),
                                                       ("More", off)], at=(x, y + 12, None))

    def _live_dismiss(self, s):
        self._live_dismissed.add(s.key)
        self.toast.hide()

    def _live_turn_off(self, field):
        self.change("live_off_apps", [*self.cfg.get("live_off_apps", ()), field.app_name])
        win = self.settings_win
        if win and win.alive and hasattr(win, "refresh_writing"):  # its copy of the list must not turn it back on
            win.cfg["live_off_apps"] = self.cfg["live_off_apps"]
            win.refresh_writing()
        self.toast.hide()
        self._live_marks(None, [], [], 0)

    def _live_paste(self, field, start, end, old, new):
        """Selects [start, end) and pastes `new` through the guarded replace; the context, or None. A paste that was
        refused before any input leaves the selection, so it is collapsed: the next key must not overwrite it."""
        ctx = self.edit.select_range(field, start, end, old)
        if ctx is None:
            return None
        if self.edit.replace(new, ctx):
            return ctx
        if hasattr(self.edit, "collapse"):
            self.edit.collapse()
        return None

    def _live_apply(self, field, s):
        mode = self._find_mode(None)
        ctx = self._live_paste(field, s.start, s.end, s.old, s.new)
        if ctx is None:
            self._toast("The text changed. Copy the suggestion?", [("Copy", lambda: self.host.copy_text(s.new))])
            return
        live.applied(self.live_cache, mode, s)
        self._last_edit = (ctx, s.old, s.new, mode.name)
        self._toast("Applied", [("Undo", self.undo_last)])

    def _live_apply_all(self):
        """The rewrite shortcut with nothing selected: every underlined change, one guarded paste per line, top
        first. False when text is selected or nothing is underlined, so the Writer rewrites as before."""
        if not (self.live_ready and self._live_on()):
            return False
        mode, field = self._find_mode(None), self.edit.observe()
        if field is None or field.caret[0] != field.caret[1] or field.app_name in self.cfg.get("live_off_apps", ()):
            return False
        found = live.suggestions(field.text, self.live_cache, self._live_dismissed, mode)
        if not found:
            return False
        done, count = [], 0  # (ctx, line, fixed line) per pasted line
        while found:
            at, core = found[0].start - found[0].key[1], found[0].key[0]
            these = [s for s in found if s.start - s.key[1] == at]
            new = live.fixed(core, these)
            ctx = self._live_paste(field, at, at + live.units(core), core, new)
            if ctx is None:
                break
            self.live_cache.put(new, mode, new)  # nothing left to underline, and never checked again
            done.append((ctx, core, new))
            count += len(these)
            field = self.edit.observe()
            if field is None or live.text_at(field.text, at, at + live.units(new)) != new:
                break  # the paste can't be seen yet: stop rather than paste the next line on a guess
            found = live.suggestions(field.text, self.live_cache, self._live_dismissed, mode)
        if not done:
            self._toast("The text changed. Nothing was applied.")
        elif len(done) == 1:  # Undo restores one paste; the app's own undo steps back through several
            self._last_edit = (*done[0], mode.name)
            self._toast("Applied" if count == 1 else f"Applied {count} changes", [("Undo", self.undo_last)])
        else:
            self._toast(f"Applied {count} changes. Use {UNDO_KEYS} to undo.")
        return True

    def _live_review(self, field, at=None):
        """The original and rewrite of the line at `at` (the caret's for the badge) in the Review panel. Replace swaps
        the whole line, guarded."""
        mode = self._find_mode(None)
        start, end = live.paragraph_at(field.text, field.caret[0] if at is None else at)
        line = live.text_at(field.text, start, end)
        core = line.strip()
        new = self.live_cache.get(core, mode)
        if new is None or new == core:
            for start, line in live.lines(field.text):  # the badge may be for another line
                core = line.strip()
                new = self.live_cache.get(core, mode) if core else None
                if new is not None and new != core:
                    end = start + live.units(line)
                    break
            else:
                return
        at = start + live.units(line[:line.index(core)])

        def replace(text):
            ctx = self._live_paste(field, at, at + live.units(core), core, text)
            if ctx is None:
                self._toast("The text changed. Copy the result?", [("Copy", lambda: self.host.copy_text(text))])
                return
            self._last_edit = (ctx, core, text, mode.name)
            self._toast("Applied", [("Undo", self.undo_last)])
        self.review.show(core, new, replace, self.host.copy_text)

    def _apply_live(self, key, value):
        if key in ("default_mode", "modes", "writer_sources", "writer_models", "live_off_apps"):
            self._live_cancel.set()  # a check in flight must not send on, or cache, under the old settings
        if key in ("default_mode", "modes", "writer_sources", "writer_models"):
            self.live_cache.clear()
        if key in ("live_check", "writer", "live_off_apps", "default_mode", "modes") and self.marks:
            self._live_marks(None, [], [], 0)

    def _close_live(self):
        if not hasattr(self, "_live_stop"):  # quit before _init_live ran
            return
        self._live_stop.set()
        self._live_cancel.set()
        if self.marks:
            self.marks.hide()


class App(Live, core.App):
    """core.App with Check as you type."""
