"""Windows floating-window styles and toolbar controls."""
import ctypes
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST = -20, 0x08000000, 0x80, 0x8
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2
DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE = 34, 0xFFFFFFFE
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


def clip_pill(hwnd, width, height):
    gdi, user = ctypes.windll.gdi32, ctypes.windll.user32
    gdi.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
    gdi.CreateRoundRectRgn.restype = wintypes.HRGN
    user.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
    region = gdi.CreateRoundRectRgn(0, 0, width + 1, height + 1, height, height)
    if region and not user.SetWindowRgn(hwnd, region, True):
        gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi.DeleteObject(region)  # Windows owns the region after a successful SetWindowRgn.


def suppress_border(hwnd):
    border = wintypes.DWORD(DWMWA_COLOR_NONE)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_BORDER_COLOR,
                                              ctypes.byref(border), ctypes.sizeof(border))
