"""The practice box as the Writer's edit module. While the tour is frontmost on a practice step, capture, observe,
reading the selection and dictation answer from the tour's box; a context or field from the box is always edited in
the box. Everything else reaches the real platform module unchanged, except that a setting the tour turned on only
for practice (the sandbox) never reaches other apps."""
import os
from dataclasses import dataclass, field

from . import edit_logic

PRACTICE_WINDOW = -1  # the window id in a practice context; real windows are never negative
HOP_S = 1.0  # longest a worker waits for the main thread to read the box


@dataclass(frozen=True)
class PracticeCtx(edit_logic.Context):
    """A Context captured from the practice box; caret is (start, length) in UTF-16 units."""
    target: object = field(default=None, compare=False)


@dataclass(frozen=True)
class PracticeField(edit_logic.Field):
    target: object = field(default=None, compare=False)


def ours(field) -> bool:
    return field is not None and field.app == os.getpid()  # the real modules never return our own process


class PracticeEdit:
    def __init__(self, real, read, target, app):
        self.real, self._read, self._target, self.app = real, read, target, app
        self.NEWLINE = real.NEWLINE

    def __getattr__(self, name):  # any call the tour has no reason to change
        return getattr(self.real, name)

    def _box(self, target, fn, default, review=False):
        """A context or field from the box: edited in the box, or default when the tour has closed."""
        def run():
            box = self.app.tour_box()
            valid = box is not None and (target.target is box.target
                                         or (review and target.target is box.review_target))
            return fn(box) if valid else default
        return self.app.on_main(run, default)

    def _done(self, kind, ok):
        if ok:
            self.app.practice_done(kind)  # writes run on the main thread already
        return ok

    def capture(self, clipboard_fallback=False):
        box = self.app.practice_box()
        if box is not None:
            return self._capture(box, box.context)
        return None if self.app.sandboxed("writer") else self.real.capture(clipboard_fallback)

    def observe(self):
        box = self.app.practice_box()
        if box is not None:  # only step 7 is checked as you type; steps 3-6 practise other features
            return self._capture(box, box.field) if self.app.tour_step() == "live" else None
        return None if self.app.sandboxed("writer", "live_check") else self.real.observe()

    def _capture(self, box, fn):
        target = box.target
        return self.app.on_main(lambda: fn() if self.app.practice_box() is box and box.target is target else None)

    def same_context(self, ctx):
        if not isinstance(ctx, PracticeCtx):
            return self.real.same_context(ctx)
        return self._box(ctx, lambda b: edit_logic.same_context(ctx, b.context()), False)

    def replace(self, text, ctx):
        if not isinstance(ctx, PracticeCtx):
            return self.real.replace(text, ctx)
        return self._done("replace", self._box(ctx, lambda b: b.replace(ctx, text), False))

    def type_at_caret(self, text, ctx, **kw):
        if not isinstance(ctx, PracticeCtx):
            return self.real.type_at_caret(text, ctx, **kw)
        return self._done("type", self._box(ctx, lambda b: not kw.get("cancelled", lambda: False)()
                                           and b.type_at_caret(text), False))

    def select_back(self, n, ctx):
        if not isinstance(ctx, PracticeCtx):
            return self.real.select_back(n, ctx)
        return self._box(ctx, lambda b: b.select_back(n), False)

    def undo(self, ctx, original, inserted):
        if isinstance(ctx, PracticeCtx):
            return self._box(ctx, lambda b: b.undo(ctx, original, inserted), False, review=True)
        if hasattr(self.real, "undo"):
            return self.real.undo(ctx, original, inserted)
        return edit_logic.undo(self.real, ctx, original, inserted, self.NEWLINE)  # what Writer did before the wrap

    def collapse(self):
        box = self.app.practice_box()
        if box is not None:
            return self.app.on_main(box.collapse)
        if hasattr(self.real, "collapse"):
            self.real.collapse()

    def bounds(self, field, spans):
        return self._box(field, lambda b: b.bounds(field, spans), None) if ours(field) else self.real.bounds(field, spans)

    def select_range(self, field, start, end, expected):
        if not ours(field):
            return self.real.select_range(field, start, end, expected)
        return self._box(field, lambda b: b.select_range(start, end, expected), None)

    def read_selection(self, clipboard_fallback):
        box = self.app.practice_box()
        return self.app.on_main(box.selected_text, "") if box is not None else self._read(clipboard_fallback)

    def dictation_target(self):
        if self.app.practice_box() is not None:
            return os.getpid(), PRACTICE_WINDOW
        return self._target() if self._target else None