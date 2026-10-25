"""Reads the user's current text selection in whatever app has focus."""
import ctypes
import os

import uiautomation as uia
import uiautomation.uiautomation as automation

from .. import settings
from . import uia_thread

MAX_CHARS = 4000
TERMINALS = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow",
             "mintty", "PuTTY", "VirtualConsoleClass"}  # Ctrl+C means "interrupt" there
PASSWORD = object()
u32 = ctypes.windll.user32
u32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
os.makedirs(settings.TEMP_DIR, exist_ok=True)
uia.Logger.SetLogFile(os.path.join(settings.TEMP_DIR, "uia.log"))  # default: cwd


def _via_uia():
    """Selected text, or "" when UIA can't tell (no text pattern, or apps like VS Code that hide it)."""
    def inspect():
        try:
            controls = _safe_controls()
            if controls is None or controls is PASSWORD:
                return PASSWORD
            for ctl in controls:
                pattern = ctl.GetPattern(uia.PatternId.TextPattern)
                if pattern:
                    text = "".join(r.GetText(-1) for r in pattern.GetSelection())
                    if text.strip():
                        return text
        except Exception:
            return PASSWORD
        return ""
    result = uia_thread.run(inspect)
    return PASSWORD if result is None else result


def _safe_controls(focus=None, foreground=None):
    controls, node = [], focus if focus is not None else uia.GetFocusedControl()
    foreground = uia.GetForegroundWindow() if foreground is None else foreground
    skipped_renderer = False
    for _ in range(32):
        if node is None:
            return None if skipped_renderer else controls
        password = password_state(node)
        if password is None:
            if not controls or not _native_host(node, foreground):
                return None
            value = node.Element.GetCurrentPropertyValueEx(uia.PropertyId.IsPasswordProperty, True)
            if not automation._AutomationClient.instance().IUIAutomation.CheckNotSupported(value):
                return None
            if node.NativeWindowHandle == foreground:
                return controls  # Native host envelopes are never text sources.
            skipped_renderer = True
        elif password:
            return PASSWORD
        elif skipped_renderer and node.NativeWindowHandle == foreground:
            return controls if _native_host(node, foreground) else None
        else:
            controls.append(node)
        node = node.GetParentControl()
    return None


def _native_host(control, foreground):
    hwnd = control.NativeWindowHandle
    if (not hwnd or not foreground
            or control.ControlType not in (uia.ControlType.WindowControl, uia.ControlType.PaneControl)
            or uia.GetAncestor(hwnd, uia.GAFlag.Root) != foreground
            or not uia.ControlsAreSame(control, uia.ControlFromHandle(hwnd))):
        return False
    if hwnd == foreground:
        return True
    name = ctypes.create_unicode_buffer(64)
    return (control.ClassName == "Chrome_RenderWidgetHostHWND"
            and bool(u32.GetClassNameW(hwnd, name, len(name)))
            and name.value == "Chrome_RenderWidgetHostHWND")


def password_state(control):
    # The wrapper's IsPassword substitutes False for unsupported metadata.
    value = control.Element.GetCurrentPropertyValueEx(uia.PropertyId.IsPasswordProperty, True)
    return value if type(value) is bool else None


def read(clipboard_fallback: bool = True) -> str:
    """Read accessible selection text. The shared clipboard flag is retained but cannot enable unsafe copying."""
    text = _via_uia()
    return "" if text is PASSWORD else text.strip()[:MAX_CHARS]
