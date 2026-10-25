"""What the Mac and Windows practice boxes share. A subclass provides text(), selection(), _set(start, end, new),
_select(start, end), _rects(start, end) and screen_rect(); offsets are UTF-16 units of text(). Main thread only."""
import hashlib
import os

from . import edit_logic, live
from .practice import PRACTICE_WINDOW, PracticeCtx


class Box:
    def selected_text(self):
        s, e = self.selection()
        return live.text_at(self.text(), s, e)

    def context(self):
        s, e = self.selection()
        return PracticeCtx(self.selected_text(), os.getpid(), PRACTICE_WINDOW, True, False, field=self, caret=(s, e - s))

    def field(self):
        text = self.text()
        return edit_logic.Field(os.getpid(), PRACTICE_WINDOW, "Harness Discussion tour", "the practice box", text,
                                self.selection(), hashlib.sha256(text.encode("utf-16-le")).digest(),
                                self.screen_rect(), self)

    def replace(self, ctx, new):
        s, n = ctx.caret
        if self.selection() != (s, s + n) or self.selected_text() != ctx.text:
            return False
        self._set(s, s + n, new)
        return True

    def type_at_caret(self, new):
        self._set(*self.selection(), new)
        return True

    def select_back(self, n):
        s, e = self.selection()
        if s != e or s < n:
            return False
        self._select(s - n, s)
        return True

    def select_range(self, start, end, expected):
        if live.text_at(self.text(), start, end) != expected:
            return None
        self._select(start, end)
        return self.context()

    def bounds(self, field, spans):
        """The platform contract (mac/edit.bounds): per span a list of rects or None; None when the text changed."""
        text = self.text()
        if field.text != text:
            return None
        return [self._rects(s, e) if live.text_at(text, s, e) == expected else None for s, e, expected in spans]

    def undo(self, ctx, original, inserted):
        s = ctx.caret[0]
        end = s + live.units(inserted)
        if live.text_at(self.text(), s, end) != inserted:
            return False
        self._set(s, end, original)
        return True

    def collapse(self):
        e = self.selection()[1]
        self._select(e, e)