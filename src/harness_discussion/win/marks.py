"""Check as you type marks: 2 px underline strips that clicks pass through, and a badge with the suggestion count.

Clicks on an underline reach the app under it, so its caret moves as usual; the global mouse hook reports the
click, and `hit` finds which suggestion's text it landed on.
"""
import ctypes
import tkinter as tk
from ctypes import wintypes as wt

from .captions import _bind, _get_parent, _get_style, _set_style
from .popup import ACCENT, fonts
from .review import REMOVED_FG
from .toast import floating

# no activate, tool window, topmost, layered, transparent to the mouse: the strip never takes focus or a click
CLICK_THROUGH = 0x08000000 | 0x80 | 0x8 | 0x80000 | 0x20
_alpha = _bind(ctypes.windll.user32, "SetLayeredWindowAttributes", wt.BOOL, wt.HWND, wt.DWORD, wt.BYTE, wt.DWORD)
STRIP = 2  # px tall at any scale: the process is per-monitor DPI aware, so this is physical pixels

class Marks:
    def __init__(self, root):
        self._root, self._strips, self._words = root, [], {}
        self._badge_at = None
        _, text = fonts(root)
        self.badge = floating(root)
        self.count = tk.Label(self.badge, bg=ACCENT, fg="#ffffff", font=(text, 9, "bold"), padx=6, pady=1,
                              cursor="hand2")
        self.count.pack()

    def _strip(self, i):
        while len(self._strips) <= i:
            w = tk.Toplevel(self._root, bg=REMOVED_FG)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.withdraw()
            w.update_idletasks()
            hwnd = _get_parent(w.winfo_id())
            _set_style(hwnd, -20, _get_style(hwnd, -20) | CLICK_THROUGH)
            _alpha(hwnd, 0, 255, 2)  # LWA_ALPHA: a layered window paints only once it has attributes
            self._strips.append(w)
        return self._strips[i]

    def show(self, strips, badge):
        """strips: [(key, (left, top, right, bottom))] of each underlined line of text. badge: (x, y, count) or None,
        with x the right edge it hangs from."""
        self._words = {}
        for i, (key, (left, top, right, bottom)) in enumerate(strips):
            self._words.setdefault(key, []).append((left, top, right, bottom))
            w = self._strip(i)
            w.geometry(f"{max(1, right - left)}x{STRIP}+{left}+{bottom - 1}")
            w.deiconify()
        for w in self._strips[len(strips):]:
            w.withdraw()
        if badge:
            x, y, count = badge
            self.count.configure(text=str(count))
            self.badge.update_idletasks()
            width, height = self.badge.winfo_reqwidth(), self.badge.winfo_reqheight()
            self._badge_at = (x - width - 4, y + 4, x - 4, y + 4 + height)
            self.badge.geometry(f"+{self._badge_at[0]}+{self._badge_at[1]}")
            self.badge.deiconify()
        else:
            self._badge_at = None
            self.badge.withdraw()

    def hide(self):
        self.show([], None)

    def hit(self, x, y):
        """"badge", the key of the suggestion whose text (x, y) is on, or None."""
        b = self._badge_at
        if b and b[0] <= x < b[2] and b[1] <= y < b[3]:
            return "badge"
        for key, rects in self._words.items():
            if any(left <= x < right and top <= y < bottom for left, top, right, bottom in rects):
                return key
        return None

    def destroy(self):
        for w in (*self._strips, self.badge):
            w.destroy()
        self._strips = []
