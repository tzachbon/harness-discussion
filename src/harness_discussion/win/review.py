"""Review (panel B): the original on the left, the rewrite on the right, word changes marked. Never takes focus.

Clicking a marked change on either side puts the original words back on the right. Both panes are read-only.
"""
import re
import tkinter as tk
from tkinter import ttk

from .. import writer
from .popup import ACCENT, BG, CLOSE, DIVIDER, FG, SUBTLE, _FlatButton, fonts, work_area
from .toast import floating

# Windows 11 light status colors
ADDED_FG, ADDED_BG, REMOVED_FG, REMOVED_BG, PANE, DISABLED = "#0f7b0f", "#dff6dd", "#c42b1c", "#fde7e9", "#ffffff", "#a0a0a0"
_UNSET = object()

def hunks(old, new):
    """writer.diff grouped into equal text (str) and changes ([old, new, reverted])."""
    out = []
    for op, text in writer.diff(old, new):
        if op == "=":
            out.append(text)
            continue
        if not out or isinstance(out[-1], str):
            out.append(["", "", False])
        out[-1][0 if op == "-" else 1] += text
    return out

class Review:
    def __init__(self, root, on_replace=None, on_copy=None, on_rewrite=None):
        """Each callback gets the current result, with any reverted changes put back."""
        self.on_replace, self.on_copy, self.on_rewrite = on_replace, on_copy, on_rewrite
        self._shown_on_replace, self._shown_on_copy = on_replace, on_copy
        self.parts, self.can_replace = [], True
        icons, text = fonts(root)
        w = self.win = floating(root)
        w.columnconfigure((0, 1), weight=1, uniform="side")
        self.close_btn = _FlatButton(w, self.hide, (CLOSE, (icons, 8), SUBTLE))
        self.close_btn.grid(row=0, column=1, sticky="e", padx="4p", pady=("4p", 0))
        self.headings = [tk.Label(w, text="Original", bg=BG, fg=SUBTLE, font=(text, 9)),
                         tk.Label(w, bg=BG, fg=SUBTLE, font=(text, 9))]
        self.left, self.right = (self._pane(w, column, text) for column in (0, 1))
        for column, heading in enumerate(self.headings):
            heading.grid(row=1, column=column, sticky="w", padx="8p")
        footer = tk.Frame(w, bg=BG)
        footer.grid(row=3, column=0, columnspan=2, sticky="e", padx="6p", pady="6p")
        hint = tk.Label(footer, text="Click a change to undo it", bg=BG, fg=SUBTLE, font=(text, 9))
        hint.pack(side="left", padx=(0, "8p"))
        self.rewrite_btn = _FlatButton(footer, self._rewrite, ("Rewrite again", (text, 10), FG))
        self.copy_btn = _FlatButton(footer, self._copy, ("Copy", (text, 10), FG))
        self.replace_btn = _FlatButton(footer, self._replace, ("Replace", (text, 10, "bold"), ACCENT))
        for button in (self.rewrite_btn, self.copy_btn, self.replace_btn):
            button.pack(side="left", padx=(0, "2p"))

    def _pane(self, w, column, font):
        """A read-only, word-wrapped text box sized in characters and lines, so it follows Windows scaling."""
        frame = tk.Frame(w, bg=DIVIDER, padx=1, pady=1)  # the 1 px border
        frame.grid(row=2, column=column, sticky="nsew", padx=("8p" if column == 0 else "4p", "4p" if column == 0 else "8p"))
        box = tk.Text(frame, width=40, height=10, wrap="word", font=(font, 10), bg=PANE, fg=FG, relief="flat",
                      borderwidth=0, padx=6, pady=4, cursor="arrow", highlightthickness=0)
        bar = ttk.Scrollbar(frame, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        box.pack(side="left", fill="both", expand=True)
        box.tag_configure("removed", foreground=REMOVED_FG, background=REMOVED_BG, overstrike=True)
        box.tag_configure("added", foreground=ADDED_FG, background=ADDED_BG)
        return box

    def show(self, original, rewritten, on_replace=_UNSET, on_copy=_UNSET, can_replace=True):
        # Keep the former positional can_replace argument working as well as its keyword form.
        if isinstance(on_replace, bool) and on_copy is _UNSET:
            can_replace, on_replace = on_replace, _UNSET
        self._shown_on_replace = self.on_replace if on_replace is _UNSET else on_replace
        self._shown_on_copy = self.on_copy if on_copy is _UNSET else on_copy
        if on_replace is None:
            can_replace = False
        self.parts = hunks(original, rewritten)
        self._render()
        self.set_can_replace(can_replace)
        w = self.win
        w.update_idletasks()
        x, y = w.winfo_pointerxy()
        area = work_area(x, y)
        width, height = w.winfo_reqwidth(), w.winfo_reqheight()
        w.geometry(f"+{max(area.left, (area.left + area.right - width) // 2)}"
                   f"+{max(area.top, (area.top + area.bottom - height) // 2)}")
        w.deiconify()

    def set_can_replace(self, can_replace):
        """False when the original selection is gone, so only Copy is left."""
        self.can_replace = bool(can_replace)
        for button, enabled, color in (
                (self.replace_btn, self.can_replace and callable(self._shown_on_replace), ACCENT),
                (self.rewrite_btn, self.can_replace and callable(self.on_rewrite), FG)):
            button.parts[1]["fg"] = color if enabled else DISABLED
            for part in button.parts:
                part["cursor"] = "hand2" if enabled else "arrow"

    def result(self):
        return "".join(p if isinstance(p, str) else p[0] if p[2] else p[1] for p in self.parts)

    def hide(self):
        self.win.withdraw()

    def destroy(self):
        self.win.destroy()

    @property
    def visible(self):
        return bool(self.win.winfo_exists() and self.win.winfo_viewable())

    def contains(self, x, y):
        w = self.win
        return (self.visible and w.winfo_rootx() <= x < w.winfo_rootx() + w.winfo_width()
                and w.winfo_rooty() <= y < w.winfo_rooty() + w.winfo_height())

    def _replace(self):
        if self.can_replace and callable(self._shown_on_replace):
            self._shown_on_replace(self.result())

    def _rewrite(self):
        if self.can_replace and callable(self.on_rewrite):
            self.on_rewrite(self.result())

    def _copy(self):
        if callable(self._shown_on_copy):
            self._shown_on_copy(self.result())

    def _render(self):
        changes = sum(1 for p in self.parts if not isinstance(p, str) and not p[2])
        self.headings[1]["text"] = f"Rewrite · {changes} change{'' if changes == 1 else 's'}"
        for box, side in ((self.left, 0), (self.right, 1)):
            box.configure(state="normal")
            box.delete("1.0", "end")
            for tag in box.tag_names():
                if tag.startswith("h"):
                    box.tag_delete(tag)
            for i, part in enumerate(self.parts):
                if isinstance(part, str):
                    box.insert("end", part)
                elif part[2]:
                    box.insert("end", part[0])  # reverted: the original words, unmarked
                elif part[side]:
                    for run in re.split(r"(\s+)", part[side]):  # a marked newline would fill the whole line
                        box.insert("end", run, (f"h{i}",) + (("removed" if side == 0 else "added",) * bool(run.strip())))
                    box.tag_bind(f"h{i}", "<Button-1>", lambda _e, i=i: self._revert(i))
                    box.tag_bind(f"h{i}", "<Enter>", lambda _e, box=box: box.configure(cursor="hand2"))
                    box.tag_bind(f"h{i}", "<Leave>", lambda _e, box=box: box.configure(cursor="arrow"))
            box.configure(state="disabled")

    def _revert(self, i):
        self.parts[i][2] = True
        self._render()
