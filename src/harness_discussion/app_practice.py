"""The Practice mixin: install the tour's edit adapter, guard reads and dictation outside the box, substitute the
canned results while no source is ready, and record which steps were practised. `Practice` is mixed in after `Tour`
(T4) and before app_live.App, and reads `self.tour` and `self.tour_step()` from it."""
import logging
import threading

from . import practice, tour, tour_copy, writer

log = logging.getLogger("harness_discussion")


class Practice:
    def _init_writer(self):
        super()._init_writer()  # Writer, then Live (which checks the real module's observe/bounds/select_range)
        self._main = threading.get_ident()  # App is built on the thread that runs pump (mac/main.py, win/main.py)
        if self.edit is not None:
            h = self.host
            self.edit = practice.PracticeEdit(self.edit, h.read_selection, getattr(h, "dictation_target", None), self)
            h.read_selection, h.dictation_target = self.edit.read_selection, self.edit.dictation_target  # core._read,
            # app_skip._hotkey_read and Writer._dictate_down look these up on each call

    def on_main(self, fn, default=None):
        """fn() on the main thread, or default after practice.HOP_S or when fn fails (the tour may have closed).
        Only reads hop: writes already run on the main thread."""
        def safe():
            try:
                return fn()
            except Exception as e:
                log.info("practice box read failed: %s", type(e).__name__)
                return default
        if threading.get_ident() == self._main:
            return safe()
        done, out = threading.Event(), [default]

        def run():
            out[0] = safe()
            done.set()
        self.events.put(("call", run))
        return out[0] if done.wait(practice.HOP_S) else default  # read at call time, so tests can shorten it

    def tour_box(self):
        t = getattr(self, "tour", None)  # read once: the main thread may close the tour meanwhile
        return t.box if t is not None and t.alive else None

    def practice_box(self):
        """The box while the tour is frontmost on a practice step. Any thread."""
        t = getattr(self, "tour", None)
        box = t.box if t is not None and t.alive else None
        return box if box is not None and self.tour_step() in tour.PRACTICE_STEPS and t.frontmost() else None

    def _read(self, seq, x, y):  # worker thread
        t = getattr(self, "tour", None)
        if t is not None and self.practice_box() is not None and self.on_main(lambda: t.hit(x, y)) == "window":
            return  # a click on the tour's own buttons is not a selection (Windows' global hook sees it)
        super()._read(seq, x, y)

    def _dictate_down(self, *args, **kwargs):
        if self.sandboxed("writer") and self.practice_box() is None:
            return  # Writing is on only for practice: the dictation shortcut stays off in other apps
        super()._dictate_down(*args, **kwargs)

    def _rewrite_done(self, rid, ctx, mode, result, prev, at):
        text = prev[1] if prev else getattr(ctx, "text", "")
        if (rid == self._rewrite_id and isinstance(ctx, practice.PracticeCtx) and isinstance(result, Exception)
                and not isinstance(result, writer.Cancelled) and text.strip()):  # stale events are left to super
            result = (tour_copy.sample_fix(text), tour_copy.SAMPLE)
            self.tour_sample("rewrite")
        super()._rewrite_done(rid, ctx, mode, result, prev, at)

    def _dictated(self, rid, generation, binding, mode, result, at, timing):
        job = self._dictation_job  # the same identity check Writer._dictated makes before delivering
        current = job is not None and (rid, generation, binding) == (job.rid, job.generation, job.binding)
        if (current and isinstance(getattr(self, "_dictate_ctx", None), practice.PracticeCtx)
                and isinstance(result, Exception) and not isinstance(result, writer.Cancelled)):
            result = (tour_copy.DICTATED, tour_copy.SAMPLE, None)
            self.tour_sample("dictate")
        super()._dictated(rid, generation, binding, mode, result, at, timing)

    def _live_check_done(self):
        super()._live_check_done()
        mode = self._find_mode(None)
        if (self.tour_step() == "live" and self.tour_box() and self.tour_writer_source() is None
                and not self.live_cache.has(tour_copy.LIVE, mode)):  # a cancelled check with a ready source is not seeded
            self.live_cache.put(tour_copy.LIVE, mode, tour_copy.sample_fix(tour_copy.LIVE))
            self.tour_sample("live")

    def _live_turn_off(self, field):
        if practice.ours(field):  # "Turn off in the practice box" must not save our own name in the off list
            self.toast.hide()
            self._live_marks(None, [], [], 0)
            return
        super()._live_turn_off(field)

    def play(self, text, lang_tag, only=None, start=0):
        super().play(text, lang_tag, only, start)
        if self.tour_step() == "read" and self.practice_box() is not None:
            self.tour_mark("read")

    def test_voice(self, text):
        super().test_voice(text)
        self._tour_voice_rid = self._read_id if self.tour_step() == "voice" else None

    def open_review(self):
        super().open_review()
        if self._last_edit and isinstance(self._last_edit[0], practice.PracticeCtx):
            self.tour_mark("review")

    def practice_done(self, kind):  # main thread, from PracticeEdit
        step = self.tour_step()
        if kind == "replace" and step in ("rewrite", "review"):
            self.tour_mark("rewrite")
        elif (kind, step) in (("type", "dictate"), ("replace", "live")):
            self.tour_mark(step)