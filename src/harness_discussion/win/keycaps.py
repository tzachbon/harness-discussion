"""A shortcut drawn as keycaps, one rounded chip per key, like an HTML <kbd> group."""
import tkinter as tk
import tkinter.font as tkfont

from .. import hotkeys

class Keys(tk.Frame):
    """show(spec) draws the shortcut's keys; say(text) shows plain text instead, such as "Press keys…".
    `text` is what it reads as, the shortcut's label for keys."""

    def __init__(self, parent, family, fg, fill, cap_fg, bold=False):
        super().__init__(parent, bg=parent["bg"])
        self.font = (family, 10, "bold" if bold else "normal")
        self.cap_font = tkfont.Font(self, (family, 9))
        self.fg, self.fill, self.cap_fg, self.text = fg, fill, cap_fg, ""

    def show(self, spec, after=""):
        names = hotkeys.keys(spec)
        if not names:
            return self.say("Off")
        self._clear(hotkeys.label(spec) + after)
        for name in names:
            self._cap(name).pack(side="left", padx=(0, "3p"))
        if after:
            tk.Label(self, text=after, font=self.font, fg=self.fg, bg=self["bg"], padx=0, pady=0).pack(side="left")

    def say(self, text):
        self._clear(text)
        tk.Label(self, text=text, font=self.font, fg=self.fg, bg=self["bg"], padx=0, pady=0).pack(side="left")

    def _clear(self, text):
        for child in self.winfo_children():
            child.destroy()
        self.text = text

    def _cap(self, name):
        pad, r = self.winfo_fpixels("4p"), self.winfo_fpixels("3p")
        h = round(self.cap_font.metrics("linespace") + self.winfo_fpixels("3p"))
        w = round(max(h, self.cap_font.measure(name) + 2 * pad))
        cap = tk.Canvas(self, width=w, height=h, bg=self["bg"], bd=0, highlightthickness=0)
        x2, y2 = w - 1, h - 1  # doubled points keep the edges straight; smoothing rounds only the corners
        cap.create_polygon(r, 0, r, 0, x2 - r, 0, x2 - r, 0, x2, 0, x2, r, x2, r, x2, y2 - r, x2, y2 - r, x2, y2,
                           x2 - r, y2, x2 - r, y2, r, y2, r, y2, 0, y2, 0, y2 - r, 0, y2 - r, 0, r, 0, r, 0, 0,
                           smooth=True, fill=self.fill, outline="")
        cap.create_text(w / 2, h / 2, text=name, font=self.cap_font, fill=self.cap_fg)
        return cap
