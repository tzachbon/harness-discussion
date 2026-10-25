"""The Windows Get Started tour window. It draws tour_view's dict, owns the practice box, and records shortcuts
with the Settings recorder."""
import ctypes
import os
import tkinter as tk
from ctypes import wintypes

from .. import hotkeys
from . import edit, tour_steps
from .bar_parts import fonts, work_area
from .captions import _bind, _get_parent
from .shortcut_row import ShortcutRow

BG = tour_steps.BG
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_window_rect = _bind(ctypes.windll.user32, "GetWindowRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))


class TourWindow(ShortcutRow):
    """Main thread only, except `alive`, `box` and frontmost()."""

    def __init__(self, root, app, on_record):
        self.app, self.on_record = app, on_record
        self._recording, self.shortcuts, self._shown = None, {}, {}  # _shown: the spec each row last showed
        self.box = self.keep_switch = None
        self.alive, self._placed, self._done = True, False, False
        self.fonts = fonts(root)
        w = self.win = tk.Toplevel(root, bg=BG)
        w.withdraw()
        w.title("Harness Discussion · Get started")
        w.resizable(False, False)
        self._min = round(w.winfo_fpixels("480p")), round(w.winfo_fpixels("420p"))  # 640 x 560 px at 100%
        w.minsize(*self._min)
        w.update_idletasks()
        self._hwnd = _get_parent(w.winfo_id())
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(self._hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                                                       ctypes.byref(ctypes.c_int(1)), 4)
        except OSError:
            pass  # older builds keep a light title bar
        w.protocol("WM_DELETE_WINDOW", self._user_close)
        w.bind("<Return>", self._return)
        self.foot = tk.Frame(w, bg=BG)
        self.foot.pack(side="bottom", fill="x", padx="16p", pady=("10p", "14p"))
        self.head, self.top, self.sc, self.cards, self.box_area, self.notes = (tk.Frame(w, bg=BG) for _ in range(6))
        for frame in (self.head, self.top, self.sc, self.cards, self.box_area, self.notes):
            frame.pack(fill="x", padx="16p")
        self.head.pack_configure(pady=("10p", 0))

    @property
    def cfg(self):
        return self.app.cfg

    def _set(self, key, spec):
        self.app.tour_action("shortcut", (key, spec))

    def show(self, view):
        self._end_recording()
        self.shortcuts, self._shown, self.box = {}, {}, None
        tour_steps.clear(self.sc)
        tour_steps.clear(self.box_area)
        if view["done"] is None:
            self.sc.pack_configure(side="top", fill="x", anchor="center", padx="16p")
        else:  # the shortcut sheet is the right column, the step rows the left
            self.sc.pack_configure(side="right", fill="none", anchor="n", padx=("8p", "16p"))
        tour_steps.shortcuts(self, self.sc, view)
        tour_steps.box(self, self.box_area, view)
        self._draw(view)
        if not self._placed:
            self._place()
        self.focus()
        if view["step"] == "dictate" and self.box:
            self.box.widget.focus_force()  # the dictation types at the box's caret

    def refresh(self, view):
        """Redraw in place: the box and the shortcut rows stay, so typing, a recording and an error survive."""
        self._draw(view)
        if self._recording:
            return
        for key, widgets in self.shortcuts.items():
            if self.cfg[key] != self._shown.get(key):
                widgets["label"]["text"] = hotkeys.label(self.cfg[key])
                self._shown[key] = self.cfg[key]

    def _draw(self, v):
        self.keep_switch, self._done = None, v["done"] is not None
        for frame in (self.head, self.top, self.cards, self.notes, self.foot):
            tour_steps.clear(frame)
        tour_steps.header(self, self.head, v)
        tour_steps.heading(self, self.top, v)
        tour_steps.rows(self, self.cards, v)
        tour_steps.quote(self, self.cards, v)
        for part in (tour_steps.status, tour_steps.dest, tour_steps.keep):
            part(self, self.notes, v)
        if self._done:
            tour_steps.done(self, self.cards, self.notes, v)
        tour_steps.footer(self, self.foot, v)

    def _place(self):
        w = self.win
        w.update_idletasks()
        area = work_area(*w.winfo_pointerxy())
        width, height = max(w.winfo_reqwidth(), self._min[0]), max(w.winfo_reqheight(), self._min[1])
        w.geometry(f"+{area.left + max(0, (area.right - area.left - width) // 2)}"
                   f"+{area.top + max(0, (area.bottom - area.top - height) // 2)}")
        self._placed = True

    def focus(self):
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def close(self):
        """Close without calling back."""
        self._end_recording()
        self.shortcuts, self.box, self.alive = {}, None, False
        self.win.destroy()

    def _user_close(self):
        self._end_recording()  # tour_closed() never calls close(), so × during "Press keys…" ends it here
        self.app.tour_save_about()  # before the box goes
        self.box, self.alive = None, False
        self.win.destroy()
        self.app.tour_closed()

    def _end_recording(self):
        if self._recording:
            self._stop_recording()
            self.on_record(False)

    def _next(self):
        if self._done:
            self.app.tour_finish()
        else:
            self.app.tour_next()

    def _return(self, event):
        if self._recording:  # Tk runs only this binding, the more specific one, so it records Return
            return self._hotkey_key(event)
        if self.box is None or event.widget is not self.box.widget:  # Enter in the box types a line break
            self._next()
        return None

    def frontmost(self):
        """Any thread: our process owns the foreground window."""
        identity = edit._foreground()
        return bool(identity) and identity[0] == os.getpid()

    def hit(self, x, y):
        """A screen point on the box, elsewhere on the window (title bar included), or None."""
        if not self.alive:
            return None
        if self.box is not None:
            left, top, right, bottom = self.box.screen_rect()
            if left <= x < right and top <= y < bottom:
                return "box"
        r = wintypes.RECT()
        if _window_rect(self._hwnd, ctypes.byref(r)) and r.left <= x < r.right and r.top <= y < r.bottom:
            return "window"
        return None
