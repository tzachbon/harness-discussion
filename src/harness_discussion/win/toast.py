"""The small pill shown after an in-place rewrite. Never takes focus."""
import tkinter as tk

from .captions import _get_parent, _get_style, _set_style
from .popup import (ACCENT, BG, CHEVRON, DIVIDER, FG, GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,
                    WS_EX_TOPMOST, _FlatButton, fonts, round_corners, work_area)

HIDE_MS = 6000
ACTIVITY_MS = 120
ACTIONS = ("undo", "review", "mode", "copy")
LEGACY_ACTIONS = ACTIONS[:3]
ACTIVITY = ("◐", "◓", "◑", "◒")
WAVE_BARS = 9


def floating(root, **options):
    """A borderless, topmost, rounded window that never activates, so the target app keeps focus and its caret."""
    w = tk.Toplevel(root, bg=BG, **options)
    w.overrideredirect(True)
    w.attributes("-topmost", True)
    w.withdraw()
    w.update_idletasks()
    hwnd = _get_parent(w.winfo_id())
    _set_style(hwnd, GWL_EXSTYLE, _get_style(hwnd, GWL_EXSTYLE) | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
    round_corners(hwnd)
    return w


def place(w, x, y, above=None):
    """Below the point, kept inside the work area of its monitor. With `above`, the top of the bar the point is
    under: at the point itself, or above the bar when there is no room under it."""
    w.update_idletasks()
    area = work_area(x, y if above is None else above)  # the bar's monitor, not the one past its edge
    width, height = w.winfo_reqwidth(), w.winfo_reqheight()
    if above is None:
        top = y + 16 if y + 16 + height <= area.bottom else y - height - 14
    else:
        top = y if y + height <= area.bottom else above - height - 6
    w.geometry(f"+{max(area.left, min(x - width // 2, area.right - width))}+{max(area.top, top)}")


class Toast:
    def __init__(self, root, on_action=None):
        """Legacy action IDs call on_action(name); shared actions carry their own callbacks."""
        self._root, self.on_action, self.on_hide = root, on_action, None
        self._hide_job = self._activity_job = self._menu_close_job = None
        self._hovered = self._busy = self._menu_open = False
        self._activity_frame = 0
        self._menu_win = None
        self._action_buttons = []
        icons, text = self.fonts = fonts(root)
        w = self.win = floating(root)
        self.spinner = tk.Label(w, bg=BG, fg=ACCENT, font=(icons, 10), padx=0, pady=0)
        self.label = tk.Label(w, bg=BG, fg=FG, font=(text, 10), padx=0, pady=0)
        # Rounded bars of the last WAVE_BARS measured levels, newest on the right. Redrawn only by set_input_level.
        bar, gap, self._wave_height = (w.winfo_fpixels(v) for v in ("2p", "2p", "14p"))
        self.meter = tk.Canvas(w, bg=BG, highlightthickness=0, bd=0, height=round(self._wave_height),
                               width=round(WAVE_BARS * (bar + gap) - gap))
        self._wave_bar = bar
        self._wave = [self.meter.create_line(i * (bar + gap) + bar / 2, 0, i * (bar + gap) + bar / 2, 0,
                                             width=bar, capstyle="round", fill=ACCENT) for i in range(WAVE_BARS)]
        self._levels, self._envelope = [0.0] * WAVE_BARS, 0.0
        self.spinner.pack(side="left", padx=("8p", "4p"), pady="4p")
        self.label.pack(side="left", padx=("8p", "4p"), pady="4p")
        self.divider = tk.Frame(w, bg=DIVIDER, width=1)
        self.buttons = {}
        for name in ACTIONS:
            parts = [(name.title(), (text, 10), FG)] + ([(CHEVRON, (icons, 7), FG)] if name == "mode" else [])
            self.buttons[name] = _FlatButton(w, lambda name=name: self._legacy_action(name), *parts)
        w.bind("<Enter>", self._enter)
        w.bind("<Leave>", self._leave)

    def show(self, message, x=None, y=None, actions=None, busy=False, above=None):
        """Show at (x, y), or the pointer. Shared actions are (title, fn) or (title, [(title, fn, checked)])."""
        self._close_menu()
        self._clear_shared_actions()
        self.meter.pack_forget()
        self._levels, self._envelope = [0.0] * WAVE_BARS, 0.0
        self.label["text"] = message
        if actions is None:
            actions = LEGACY_ACTIONS if self.on_action is not None else ()
        for button in self.buttons.values():
            button.pack_forget()
        shown = []
        for action in actions:
            if isinstance(action, str):
                button = self.buttons[action]
            else:
                title, callback = action
                if isinstance(callback, list):
                    holder = {}
                    button = _FlatButton(self.win,
                                         lambda entries=callback, h=holder: self._open_menu(h["button"], entries),
                                         (f"{title} ▾", (self.fonts[1], 10), FG))
                    holder["button"] = button
                else:
                    button = _FlatButton(self.win, lambda fn=callback: self._shared_action(fn),
                                         (title, (self.fonts[1], 10), FG))
                self._action_buttons.append(button)
            button.pack(side="left", padx=(0, "2p"), pady="2p")
            shown.append(button)
        if shown:
            self.divider.pack(side="left", fill="y", pady="5p", padx="2p", before=shown[0])
        else:
            self.divider.pack_forget()

        if x is None or y is None:
            x, y = self.win.winfo_pointerxy()
        place(self.win, x, y, above)
        self.win.deiconify()
        self._busy = bool(busy)
        if self._busy:
            self.spinner["text"] = ACTIVITY[self._activity_frame]
            self.spinner.pack(side="left", before=self.label, padx=("8p", "4p"), pady="4p")
            self._schedule_activity()
            self._cancel_hide()
        else:
            self.spinner.pack_forget()
            self._cancel_activity()
            self._schedule_hide()

    def set_input_level(self, level):
        self._cancel_activity()
        self.spinner.pack_forget()
        level = max(0.0, min(1.0, level))
        # Fast attack, slower release, so syllables register at once and pauses settle instead of flicker.
        self._envelope += (level - self._envelope) * (0.7 if level > self._envelope else 0.35)
        self._levels = self._levels[1:] + [self._envelope]
        middle, reach = self._wave_height / 2, (self._wave_height - self._wave_bar) / 2
        for item, value in zip(self._wave, self._levels):
            x = self.meter.coords(item)[0]
            half = max(0.5, value * reach)  # silence is a row of calm dots
            self.meter.coords(item, x, middle - half, x, middle + half)
        self.meter.pack(side="left", before=self.label, padx=("8p", "4p"), pady="4p")

    def hide(self):
        was_visible = self.visible
        self._cancel_hide()
        self._cancel_activity()
        self._close_menu()
        self._busy = self._hovered = False
        try:
            self.win.withdraw()
        except tk.TclError:
            pass
        if was_visible and self.on_hide:
            self.on_hide()

    def destroy(self):
        self.hide()
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    @property
    def visible(self):
        try:
            return bool(self.win.winfo_exists() and self.win.winfo_viewable())
        except tk.TclError:
            return False

    def contains(self, x, y):
        """On the toast or its open menu."""
        try:
            return self.visible and any(
                w and w.winfo_exists() and w.winfo_rootx() <= x < w.winfo_rootx() + w.winfo_width()
                and w.winfo_rooty() <= y < w.winfo_rooty() + w.winfo_height() for w in (self.win, self._menu_win))
        except tk.TclError:
            return False

    def _legacy_action(self, name):
        if self.on_action:
            self.on_action(name)

    def _shared_action(self, callback):
        self.hide()
        if callback:
            callback()

    def _open_menu(self, anchor, entries):
        self._close_menu()
        self._cancel_hide()
        self._menu_open = True
        menu = self._menu_win = floating(self._root)
        for title, callback, checked in entries:
            mark = "✓ " if checked else "   "
            button = _FlatButton(menu, lambda fn=callback: self._shared_action(fn),
                                 (mark + title, (self.fonts[1], 10), FG))
            button.pack(fill="x", padx="2p", pady="1p")
        menu.bind("<Enter>", lambda _event: self._cancel_menu_close())
        menu.bind("<Leave>", lambda _event: self._schedule_menu_close())
        menu.update_idletasks()
        x, y = anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height()
        area, width, height = work_area(x, y), menu.winfo_reqwidth(), menu.winfo_reqheight()
        top = y if y + height <= area.bottom else max(area.top, y - height - anchor.winfo_height())
        menu.geometry(f"+{max(area.left, min(x, area.right - width))}+{top}")
        menu.deiconify()

    def _schedule_menu_close(self):
        self._cancel_menu_close()
        if self._menu_win and self._menu_win.winfo_exists():
            self._menu_close_job = self.win.after(250, self._menu_left)

    def _cancel_menu_close(self):
        self._cancel_after("_menu_close_job")

    def _menu_left(self):
        self._menu_close_job = None
        self._close_menu()
        self._schedule_hide()

    def _close_menu(self):
        self._cancel_menu_close()
        menu, self._menu_win = self._menu_win, None
        self._menu_open = False
        if menu:
            try:
                menu.destroy()
            except tk.TclError:
                pass

    def _clear_shared_actions(self):
        for button in self._action_buttons:
            try:
                button.destroy()
            except tk.TclError:
                pass
        self._action_buttons.clear()

    def _schedule_hide(self):
        self._cancel_hide()
        if not self._hovered and not self._busy and not self._menu_open and self.visible:
            self._hide_job = self.win.after(HIDE_MS, self._hide_timeout)

    def _hide_timeout(self):
        self._hide_job = None
        self.hide()

    def _schedule_activity(self):
        self._cancel_activity()
        if self._busy and self.visible:
            self._activity_job = self.win.after(ACTIVITY_MS, self._animate_activity)

    def _animate_activity(self):
        self._activity_job = None
        if self._busy and self.visible:
            self._activity_frame = (self._activity_frame + 1) % len(ACTIVITY)
            self.spinner["text"] = ACTIVITY[self._activity_frame]
            self._activity_job = self.win.after(ACTIVITY_MS, self._animate_activity)

    def _enter(self, _event):
        self._hovered = True
        self._cancel_hide()
        self._cancel_menu_close()

    def _leave(self, _event):
        self._hovered = False
        if self._menu_open:
            self._schedule_menu_close()
        else:
            self._schedule_hide()

    def _cancel_hide(self):
        self._cancel_after("_hide_job")

    def _cancel_activity(self):
        self._cancel_after("_activity_job")

    def _cancel_after(self, attr):
        job = getattr(self, attr)
        setattr(self, attr, None)
        if job:
            try:
                self.win.after_cancel(job)
            except tk.TclError:
                pass
