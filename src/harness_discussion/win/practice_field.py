"""The Windows practice box: a tk.Text with the practice_box.Box primitives. Offsets are UTF-16 units of text()."""
import tkinter as tk

from .. import live
from ..practice_box import Box
from .bar_parts import fonts

BG, FG, SELECTION = "#151517", "#ffffff", "#264f78"
# D1: Tk 8.6.12 and 9.0 both step "+N chars" over a non-BMP character as one char (Tk 8.6's line.col columns
# still count it as two, so indexes are never built from columns). Set True if the D1 test sees Tk count two.
TK_UTF16 = False


def tk_index(text, offset):
    """The Tk index of a UTF-16 offset in text."""
    return f"1.0 + {offset if TK_UTF16 else len(live.text_at(text, 0, offset))} chars"


class PracticeField(Box):
    def __init__(self, master, spec):
        _, face = fonts(master)
        self.widget = tk.Text(master, bg=BG, fg=FG, insertbackground=FG, selectbackground=SELECTION,
                              inactiveselectbackground=SELECTION, selectforeground=FG, relief="flat",
                              borderwidth=0, highlightthickness=0, undo=False, wrap="word", font=(face, 11),
                              width=1, height=4, padx=12, pady=10)
        self.widget.insert("1.0", spec["text"])
        end, select = live.units(spec["text"]), spec["select"]
        self._select(end if select == "end" else 0, 0 if select is None else end)

    def text(self):
        return self.widget.get("1.0", "end-1c")

    def _offset(self, index):
        return live.units(self.widget.get("1.0", index))

    def selection(self):
        ranges = self.widget.tag_ranges("sel")
        first, last = (str(ranges[0]), str(ranges[-1])) if ranges else ("insert", "insert")
        return self._offset(first), self._offset(last)

    def _set(self, start, end, new):
        w, text = self.widget, self.text()
        w.delete(tk_index(text, start), tk_index(text, end))
        w.insert(tk_index(text, start), new)
        w.tag_remove("sel", "1.0", "end")
        w.mark_set("insert", tk_index(self.text(), start + live.units(new)))
        w.see("insert")

    def _select(self, start, end):
        w, text = self.widget, self.text()
        w.tag_remove("sel", "1.0", "end")
        w.tag_add("sel", tk_index(text, start), tk_index(text, end))
        w.mark_set("insert", tk_index(text, end))
        w.see("insert")

    def _rects(self, start, end):
        w, text = self.widget, self.text()
        first, last = w.bbox(tk_index(text, start)), w.bbox(tk_index(text, max(start, end - 1)))
        if first is None or last is None:
            return []  # scrolled out of view
        if first[1] != last[1]:
            return None  # shortcut: a wrapped span gets no underline, split it by display line if a sample wraps
        x, y = w.winfo_rootx(), w.winfo_rooty()
        return [(x + first[0], y + first[1], x + last[0] + last[2], y + max(first[1] + first[3], last[1] + last[3]))]

    def screen_rect(self):
        w = self.widget
        x, y = w.winfo_rootx(), w.winfo_rooty()
        return x, y, x + w.winfo_width(), y + w.winfo_height()
