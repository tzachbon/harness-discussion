"""The small floating Play/Stop button with a language menu. Never takes focus from the source app.

One cohesive widget, so it stays in one file even though it passes 80 lines.
"""
import ctypes
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

from PIL import ImageTk

from .. import icons, lang, settings
from .captions import CaptionSurface, _get_parent, _get_style, _set_style
from .trigger import Escape

GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST = -20, 0x08000000, 0x80, 0x8
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2
IDLE_HIDE_MS = 4000
# Windows 11 light flyout colors
BG, HOVER, PRESSED, FG, ACCENT, DIVIDER = "#f9f9f9", "#ededed", "#e4e4e4", "#1b1b1b", "#005fb8", "#e0e0e0"
SUBTLE = "#5f5f5f"
# Selection-bar HUD colors are separate from the light review and toast surfaces.
HUD_BG, HUD_HOVER, HUD_PRESSED = "#262629", "#414145", "#535359"
HUD_FG, HUD_MUTED, HUD_CHIP, HUD_ACCENT = "#ffffff", "#b8b8bf", "#3b3b40", "#64b5ff"
PLAY, PAUSE, STOP, CHEVRON, CLOSE, SETTINGS = "\ue768", "\ue769", "\ue71a", "\ue70d", "\ue8bb", "\ue713"
SPARKLE, SPARKLE_FONT = "✦", ("Segoe UI Symbol", 11)  # neither icon font has a sparkle on Windows 10
PREVIOUS, NEXT = "\ue892", "\ue893"


def fonts(root):
    """Windows 11 fonts, with the Windows 10 equivalents as fallback."""
    have = set(tkfont.families(root))
    icons = "Segoe Fluent Icons" if "Segoe Fluent Icons" in have else "Segoe MDL2 Assets"
    text = "Segoe UI Variable Text" if "Segoe UI Variable Text" in have else "Segoe UI"
    return icons, text


def round_corners(hwnd):
    corner = ctypes.c_int(DWMWCP_ROUND)
    attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
    attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    attribute.restype = wintypes.LONG
    attribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner), ctypes.sizeof(corner))


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("monitor", wintypes.RECT), ("work", wintypes.RECT),
                ("flags", wintypes.DWORD)]


def work_area(x, y) -> wintypes.RECT:
    """Usable area (minus taskbar) of the monitor under the point."""
    u32 = ctypes.windll.user32
    u32.MonitorFromPoint.restype = wintypes.HMONITOR
    u32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    u32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo)]
    u32.GetMonitorInfoW.restype = wintypes.BOOL
    info = _MonitorInfo(cb=ctypes.sizeof(_MonitorInfo))
    u32.GetMonitorInfoW(u32.MonitorFromPoint(wintypes.POINT(x, y), 2), ctypes.byref(info))
    return info.work


class _FlatButton(tk.Frame):
    """A borderless button made of labels, with Windows 11 hover and pressed fills."""

    def __init__(self, master, command, *labels):
        super().__init__(master, bg=BG, cursor="hand2")
        self.command, self.parts = command, [self]
        for text, font, fg in labels:
            lbl = tk.Label(self, text=text, font=font, fg=fg, bg=BG, padx=0, pady=0)
            lbl.pack(side="left", padx=("3p" if len(self.parts) == 1 else 0, "3p"), pady="3p")
            self.parts.append(lbl)
        for w in self.parts:
            w.bind("<Enter>", lambda _e: self._fill(HOVER))
            w.bind("<Leave>", lambda _e: self._fill(BG))
            w.bind("<ButtonPress-1>", lambda _e: self._fill(PRESSED))
            w.bind("<ButtonRelease-1>", self._release)

    def _fill(self, color):
        for w in self.parts:
            w["bg"] = color

    def _release(self, e):
        self._fill(HOVER)
        if self.winfo_containing(e.x_root, e.y_root) in self.parts:
            self.command()


class _BarButton(tk.Canvas):
    """A rounded HUD control with the same click behavior as the light flyout buttons."""

    def __init__(self, master, command, *labels, primary=False, chip=False, minimum=0):
        super().__init__(master, bg=HUD_BG, bd=0, highlightthickness=0, cursor="hand2")
        self.command, self.parts, self._windows = command, [self], []
        self._height = round(self.winfo_fpixels("30p" if primary else "26p"))
        self._padding = round(self.winfo_fpixels("7p" if chip or primary else "4p"))
        self._minimum = max(minimum, self._height)
        self._normal = "#0a84ff" if primary else HUD_CHIP if chip else HUD_BG
        self._hover = "#3498ff" if primary else HUD_HOVER
        self._pressed = "#006bd6" if primary else HUD_PRESSED
        self._surface = [self.create_rectangle(0, 0, 0, 0, fill=self._normal, outline=""),
                         self.create_oval(0, 0, 0, 0, fill=self._normal, outline=""),
                         self.create_oval(0, 0, 0, 0, fill=self._normal, outline="")]
        for text, font, fg in labels:
            label = tk.Label(self, text=text, font=font, fg=fg, bg=self._normal, padx=0, pady=0)
            self.parts.append(label)
            self._windows.append(self.create_window(0, 0, window=label))
            label.bind("<Configure>", self._measure)
        for widget in self.parts:
            widget.bind("<Enter>", lambda _e: self._fill(self._hover))
            widget.bind("<Leave>", lambda _e: self._fill(self._normal))
            widget.bind("<ButtonPress-1>", lambda _e: self._fill(self._pressed))
            widget.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", self._draw)
        self._measure()

    def _measure(self, _event=None):
        widths = [label.winfo_reqwidth() for label in self.parts[1:]]
        width = max(self._minimum, sum(widths) + 2 * self._padding)
        self.configure(width=width, height=self._height)
        x = (width - sum(widths)) / 2
        for item, size in zip(self._windows, widths):
            self.coords(item, x + size / 2, self._height / 2)
            x += size

    def _draw(self, event):
        w, h, r = event.width, event.height, event.height / 2
        self.coords(self._surface[0], r, 0, w-r, h)
        self.coords(self._surface[1], 0, 0, h, h)
        self.coords(self._surface[2], w-h, 0, w, h)

    def _fill(self, color):
        for item in self._surface:
            self.itemconfigure(item, fill=color)
        for label in self.parts[1:]:
            label.configure(bg=color)

    def _release(self, event):
        self._fill(self._hover)
        if self.winfo_containing(event.x_root, event.y_root) in self.parts:
            self.command()


class Popup:
    def __init__(self, root, speed, *, on_play, on_stop, on_pause, on_resume, on_lang, on_speed, on_settings,
                 on_source, sources, on_skip, on_summarize=lambda text, tag: None, on_rewrite=None,
                 modes=lambda: [], on_escape=None):
        """Sources lists menu choices; on_rewrite receives (text, mode name); on_skip takes -1 or 1."""
        self.on_play, self.on_stop, self.on_pause, self.on_resume = on_play, on_stop, on_pause, on_resume
        self.on_summarize = on_summarize
        self.on_lang, self.on_speed, self.on_source, self.sources = on_lang, on_speed, on_source, sources
        self.on_settings = on_settings
        self.editable, self.rewriting = False, False
        self.on_rewrite, self.modes, self.default_mode = on_rewrite, modes, lambda: "Fix errors"
        self.writer_enabled = on_rewrite is not None
        self._legacy_writer = False
        self._last_mode = None
        self._visible = self.escape_wired = self._is_destroyed = False
        self._esc = (Escape(on_escape, suppress=lambda: self._visible, autostart=False)
                     if on_escape is not None else None)
        self.text, self.playing, self.paused, self.menu_open, self._hide_job = "", False, False, False, None
        self.lang = tk.StringVar(master=root, value="")  # "" means Auto
        self.speed = tk.DoubleVar(master=root, value=speed)
        self.source = tk.StringVar(master=root, value="")
        icons, text = fonts(root)
        w = self.win = tk.Toplevel(root, bg=HUD_BG)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        self.btn = _BarButton(w, self._toggle, (PLAY, (icons, 11), HUD_FG), primary=True)
        self.btn.pack(side="left", padx=("2p", 0), pady="2p")
        self._text_font = text
        self.ai_btn = _BarButton(w, self._summarize, (SPARKLE, SPARKLE_FONT, HUD_FG))
        self.ai_btn.pack(side="left", padx=0, pady="2p")
        self.stop_btn = _BarButton(w, self._stop, (STOP, (icons, 11), HUD_FG))  # packed while reading
        self.back_btn = _BarButton(w, lambda: on_skip(-1), (PREVIOUS, (icons, 11), HUD_MUTED))
        self.next_btn = _BarButton(w, lambda: on_skip(1), (NEXT, (icons, 11), HUD_MUTED))
        self.spinner = tk.Canvas(w, bg=HUD_BG, bd=0, highlightthickness=0)  # shown until speech starts
        self._spin_angle, self._spin_job, self.loading = 0, None, False
        self.divider = tk.Frame(w, bg="#55555b", width=1, height="18p")
        self.divider.pack(side="left", fill="y", pady="5p", padx="2p")
        self.lang_btn = _FlatButton(w, lambda: self._post_menu(self.lang_btn, self.menu),
                                    ("Auto", (text, 10), FG),
                                    (CHEVRON, (icons, 7), FG))
        self.lang_btn.pack(side="left", padx=0, pady="2p")
        self.speed_btn = _BarButton(w, lambda: self._post_menu(self.speed_btn, self.speed_menu),
                                     (settings.speed_label(speed), (text, 10), HUD_FG),
                                     (CHEVRON, (icons, 7), HUD_MUTED), chip=True,
                                     minimum=round(w.winfo_fpixels("52p")))
        self.speed_btn.pack(side="left", padx=0, pady="2p")
        self.source_btn = _BarButton(w, self._source_menu, ("Voice", (text, 10), HUD_FG),
                                     (CHEVRON, (icons, 7), HUD_MUTED), chip=True,
                                     minimum=round(w.winfo_fpixels("88p")))
        self.source_btn.pack(side="left", padx=0, pady="2p")
        self.settings_btn = _FlatButton(w, self._open_settings, (SETTINGS, (icons, 11), FG))
        self.settings_btn.pack(side="left", padx=0, pady="2p")
        self.close_btn = _BarButton(w, self.close, (CLOSE, (icons, 8), HUD_MUTED))
        self.close_btn.pack(side="left", padx=(0, "2p"), pady="2p")
        self.menu = tk.Menu(w, tearoff=0, postcommand=self._menu_posted)
        for tag, label in [("", "Auto")] + [(t, v[0]) for t, v in lang.LANGS.items()]:
            self.menu.add_radiobutton(label=label, value=tag, variable=self.lang,
                                      command=self._lang_changed)
        self.speed_menu = tk.Menu(w, tearoff=0, postcommand=self._menu_posted)
        for preset in settings.SPEED_PRESETS:
            self.speed_menu.add_radiobutton(label=settings.speed_label(preset), value=preset,
                                            variable=self.speed, command=self._speed_changed)
        self.sources_menu = tk.Menu(w, tearoff=0, postcommand=self._menu_posted)
        self.more_btn = _BarButton(w, self._overflow_menu, ("\ue712", (icons, 11), HUD_MUTED))
        self.overflow = tk.Menu(w, tearoff=0, postcommand=self._menu_posted)
        self.rewrite_btn = _BarButton(w, lambda: self._rewrite(),
                                      ("✎ Rewrite", (text, 10, "bold"), HUD_FG), primary=True)
        self.mode_btn = _BarButton(w, self.show_modes, (CHEVRON, (icons, 8), HUD_MUTED))
        self.mode_menu = tk.Menu(w, tearoff=0, postcommand=self._menu_posted)
        self._icons = {}
        w.bind("<Enter>", lambda _e: self._cancel_hide())
        w.bind("<Leave>", lambda _e: self.schedule_hide())
        w.bind("<Destroy>", self._destroyed, add="+")
        self._bar_size = None
        w.bind("<Configure>", self._round_bar, add="+")
        self._layout()
        w.withdraw()
        w.update_idletasks()
        hwnd = _get_parent(w.winfo_id())
        ex = _get_style(hwnd, GWL_EXSTYLE)
        _set_style(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
        round_corners(hwnd)
        self.caption = CaptionSurface(w, text, self._caption_placement)

    def _round_bar(self, event):
        if event.widget is not self.win or (event.width, event.height) == self._bar_size:
            return
        self._bar_size = event.width, event.height
        gdi, user = ctypes.windll.gdi32, ctypes.windll.user32
        gdi.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
        gdi.CreateRoundRectRgn.restype = wintypes.HRGN
        user.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        region = gdi.CreateRoundRectRgn(0, 0, event.width + 1, event.height + 1, event.height, event.height)
        if region and not user.SetWindowRgn(_get_parent(self.win.winfo_id()), region, True):
            gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
            gdi.DeleteObject(region)  # Windows owns the region after a successful SetWindowRgn.
        if self._visible:
            x, y = self.win.winfo_x(), self.win.winfo_y()
            area = work_area(x, y)
            left = max(area.left, min(x, area.right - event.width))
            if left != x:
                self.win.geometry(f"+{left}+{y}")

    def show(self, text, x, y, editable=False):
        self.text = text
        if not self._legacy_writer or editable:
            self.set_editable(editable)
        self.set_playing(False)
        self.lang.set("")
        self.lang_btn.parts[1]["text"] = "Auto"
        w = self.win
        if self._esc:
            self._esc.start()
            self.escape_wired = True
        self._layout()
        w.update_idletasks()
        area, h = work_area(x, y), w.winfo_reqheight()
        top = y - h - 14 if y - h - 14 >= area.top else y + 16  # above: Windows' own text bar sits below
        w.geometry(f"+{max(area.left, min(x + 12, area.right - w.winfo_reqwidth()))}"
                   f"+{max(area.top, min(top, area.bottom - h))}")
        self._visible = True
        w.deiconify()
        self.schedule_hide()

    def close(self):
        """The ✕ button: stop any read and hide."""
        self.set_playing(False)
        self.hide()

    def hide(self):
        if self._is_destroyed:
            return
        self._cancel_hide()
        self._visible = False
        if self._esc and self.escape_wired:
            self._esc.stop()
        self.escape_wired = False
        self.caption.hide()
        self.win.withdraw()
        self.on_stop()  # drops the Codex session warmed up for this popup

    def destroy(self):
        if self._is_destroyed:
            return
        self.set_loading(False)
        self.hide()
        self.win.destroy()

    def _destroyed(self, event):
        if event.widget is self.win:
            was_wired = self.escape_wired
            self._is_destroyed = True
            self._visible = self.escape_wired = False
            if self._esc and was_wired:
                self._esc.stop()

    @property
    def visible(self):
        return self._visible and not self._is_destroyed and self.win.winfo_viewable()

    def anchor(self):
        return self.win.winfo_rootx(), self.win.winfo_rooty(), self.win.winfo_height()

    def contains(self, x, y):
        w = self.win
        return (self.visible and w.winfo_rootx() <= x < w.winfo_rootx() + w.winfo_width()
                and w.winfo_rooty() <= y < w.winfo_rooty() + w.winfo_height())

    def set_loading(self, loading):
        if loading == self.loading:
            return
        self.loading = loading
        if loading:
            b = self.btn
            self.spinner.configure(width=b.winfo_reqwidth(), height=b.winfo_reqheight())
            self._layout()
            self._spin()
        else:
            if self._spin_job:
                self.win.after_cancel(self._spin_job)
                self._spin_job = None
            self._layout()

    def _spin(self):
        if not self.loading:
            return
        c, size = self.spinner, min(self.btn.winfo_width(), self.btn.winfo_height())
        x, y, r = int(c["width"]) / 2, int(c["height"]) / 2, size * 0.28
        c.delete("all")
        c.create_arc(x - r, y - r, x + r, y + r, start=self._spin_angle, extent=270, style="arc",
                     outline=HUD_ACCENT, width=max(2, round(size / 14)))
        self._spin_angle = (self._spin_angle - 24) % 360
        self._spin_job = self.win.after(40, self._spin)

    def set_playing(self, playing, paused=False):
        self.playing, self.paused = playing, playing and paused
        if not playing:
            self.caption.hide()
            self.set_loading(False)
            self._highlight(False)
        self.caption.set_paused(self.paused)
        self.btn.parts[1]["text"] = PAUSE if playing and not paused else PLAY
        self._layout()
        if not playing:
            self.schedule_hide()

    def set_summary(self):
        """A summary is on its way: the spinner until its first audio, and ✦ in the accent color until it ends."""
        self._cancel_hide()
        self.set_playing(True)
        self.set_loading(True)
        self._highlight(True)

    def _highlight(self, on):
        self.ai_btn.parts[1]["fg"] = HUD_ACCENT if on else HUD_FG

    def set_summarize_progress(self, fraction):
        """0..1 while the model downloads, shown as a percentage on ✦; None after."""
        label = self.ai_btn.parts[1]
        if fraction is None:
            label.configure(text=SPARKLE, font=SPARKLE_FONT)
        else:
            label.configure(text=f"{int(fraction * 100)}%", font=(self._text_font, 9))

    def _summarize(self):
        self._cancel_hide()
        self.on_summarize(self.text, self.lang.get() or None)
        if not self.playing:  # nothing started, such as a cancelled download
            self.schedule_hide()

    def set_caption(self, text):
        if self.playing:
            self.caption.show_text(text)

    def set_caption_paused(self, paused):
        self.caption.set_paused(paused)

    def configure_captions(self, font_size, background, background_opacity):
        self.caption.configure(font_size, background, background_opacity)

    def _caption_placement(self):
        w = self.win
        w.update_idletasks()
        x, y = w.winfo_rootx(), w.winfo_rooty()
        area = work_area(x, y)
        return ((x, y, w.winfo_width(), w.winfo_height()),
                (area.left, area.top, area.right, area.bottom))

    def play(self):
        """Starts reading the shown text, as ▶ does."""
        if not self.playing:
            self._toggle()

    def _toggle(self):
        if not self.playing:
            self._cancel_hide()
            self.set_playing(True)
            self.set_loading(True)
            self.on_play(self.text, self.lang.get() or None)
        elif self.paused:
            self.on_resume()
            self.set_playing(True)
        else:
            self.on_pause()
            self.set_playing(True, paused=True)

    def _open_settings(self):
        if self.playing and not self.paused:
            self._toggle()
        self.on_settings()

    def _layout(self):
        for widget in (self.btn, self.spinner, self.ai_btn, self.back_btn, self.next_btn,
                       self.stop_btn, self.divider, self.lang_btn, self.speed_btn,
                       self.source_btn, self.settings_btn, self.close_btn, self.more_btn,
                       self.rewrite_btn, self.mode_btn):
            widget.pack_forget()
        primary = self.spinner if self.loading else self.btn
        controls = ([primary, self.back_btn, self.next_btn, self.stop_btn]
                    if self.playing else [primary, self.ai_btn])
        if not self.playing and self.writer_enabled and self.editable:
            self.rewrite_btn.parts[1]["text"] = "✎ " + ("Rewriting…" if self.rewriting else self._current_mode())
            controls = [self.rewrite_btn, self.mode_btn, self.divider, primary, self.ai_btn]
        controls += [self.source_btn, self.speed_btn, self.more_btn, self.close_btn]
        for index, widget in enumerate(controls):
            widget.pack(side="left", padx=("6p" if index == 0 else "2p",
                                           "6p" if index == len(controls) - 1 else "2p"), pady="6p")

    def _overflow_menu(self):
        self._source_menu(post=False)
        menu = self.overflow
        menu.delete(0, "end")
        menu.add_cascade(label="Voice", menu=self.sources_menu)
        menu.add_cascade(label="Language", menu=self.menu)
        menu.add_cascade(label="Speed", menu=self.speed_menu)
        menu.add_separator()
        menu.add_command(label="Summarize", command=self._summarize)
        self._mode_choices()
        menu.add_cascade(label="Rewrite", menu=self.mode_menu,
                         state="normal" if self.writer_enabled and not self.rewriting else "disabled")
        menu.add_separator()
        menu.add_command(label="Settings…", command=self._open_settings)
        self._post_menu(self.more_btn, menu)

    def configure_writer(self, on_rewrite, modes, default_mode, enabled=True):
        """Mode-name callbacks and live suppliers from the shared Writer coordinator."""
        self.on_rewrite, self.modes, self.default_mode = on_rewrite, modes, default_mode
        self._legacy_writer = True
        self.set_writer(enabled)

    def set_writer(self, enabled):
        self.writer_enabled = bool(enabled and self.on_rewrite is not None)
        self.editable = self.editable and self.writer_enabled
        self._layout()

    def set_mode(self, editable):
        self.set_editable(editable)

    def set_editable(self, editable):
        self.editable = bool(editable and self.writer_enabled)
        self._layout()

    def set_last_mode(self, name):
        self._last_mode = name
        self._layout()

    def set_rewriting(self, on):
        self.rewriting = bool(on)
        self._layout()
        if self.rewriting:
            self._cancel_hide()
        else:
            self.schedule_hide()

    def _mode_names(self):
        names = []
        for mode in self.modes():
            names.append(mode if isinstance(mode, str) else mode.name)
        return names

    def _current_mode(self):
        if self._last_mode:
            return self._last_mode
        if self._legacy_writer:
            return self.default_mode() or "Fix errors"
        names = self._mode_names()
        return names[0] if names else "Fix errors"

    def _rewrite(self, name=None):
        if not self.writer_enabled or self.rewriting or self.on_rewrite is None:
            return
        name = name or self._current_mode()
        self.set_last_mode(name)
        if self._legacy_writer:
            self.on_rewrite(name)
        else:
            self.on_rewrite(self.text, name)

    def _mode_choices(self):
        self.mode_menu.delete(0, "end")
        for name in self._mode_names():
            self.mode_menu.add_command(label=name, command=lambda name=name: self._rewrite(name))

    def show_modes(self, x=None, y=None):
        if not self.writer_enabled or not self.editable:
            return
        self._mode_choices()
        if x is None or y is None:
            self._post_menu(self.mode_btn if self.editable else self.more_btn, self.mode_menu)
        else:
            self.mode_menu.post(x, y)

    def _stop(self):
        self.on_stop()
        self.set_playing(False)

    def _post_menu(self, button, menu):
        menu.update_idletasks()
        menu.post(button.winfo_rootx(), button.winfo_rooty() - menu.winfo_reqheight())  # upward

    def lang_tag(self):
        return self.lang.get() or None

    def set_speed(self, speed):
        self.speed.set(speed)
        self.speed_btn.parts[1]["text"] = settings.speed_label(speed)

    def set_source(self, name):
        self.source.set(name)
        self.source_btn.parts[1].configure(text=name, image=self._icon(name), compound="left")

    def _source_menu(self, post=True):
        """Built on each click: the ready sources change as statuses refresh and Settings reorders."""
        menu = self.sources_menu
        menu.delete(0, "end")
        self.source.set(self.source_btn.parts[1]["text"])  # a pick mid-read must not move the check off the label
        choices = self.sources()
        for name, fix in choices:
            if not fix:
                menu.add_radiobutton(label=name, value=name, variable=self.source, image=self._icon(name),
                                     compound="left", command=lambda name=name: self.on_source(name))
        fixes = [(name, fix) for name, fix in choices if fix]
        if fixes and len(fixes) < len(choices):
            menu.add_separator()
        for name, fix in fixes:  # a source that needs an install or sign-in offers it instead
            menu.add_command(label=f"{name}: {fix}", image=self._icon(name), compound="left",
                             command=lambda name=name: self.on_source(name))
        if post:
            self._post_menu(self.source_btn, menu)

    def _icon(self, name):
        if name not in self._icons:  # Tk drops an image nothing in Python refers to
            self._icons[name] = ImageTk.PhotoImage(icons.source_image(name, 16), master=self.sources_menu)
        return self._icons[name]

    def _speed_changed(self):
        self.set_speed(self.speed.get())
        self.on_speed(self.speed.get())

    def _lang_changed(self):
        tag = self.lang.get()
        self.lang_btn.parts[1]["text"] = lang.name(tag) or "Auto"
        self.on_lang(tag or None)

    def _menu_posted(self):
        self.menu_open = True  # the next click belongs to the menu, not "outside the popup"
        self._cancel_hide()

    def schedule_hide(self):
        self._cancel_hide()
        if not (self.playing or self.menu_open or self.rewriting) and self.visible:  # a hidden popup must not stop reads
            self._hide_job = self.win.after(IDLE_HIDE_MS, self.hide)

    def _cancel_hide(self):
        if self._hide_job:
            self.win.after_cancel(self._hide_job)
            self._hide_job = None
