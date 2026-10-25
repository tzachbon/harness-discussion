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
RENDERER, BROWSER, WEBVIEW_HOST = "Chrome_RenderWidgetHostHWND", "Chrome_WidgetWin_1", "Chrome_WidgetWin_0"
WINUI_BRIDGE = "Microsoft.UI.Content.DesktopChildSiteBridge"  # WinUI 3 apps (WhatsApp) report focus here
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


def focused():
    """The focused control. A WinUI bridge pane stands for the descendant that has the keyboard focus."""
    focus = uia.GetFocusedControl()
    if getattr(focus, "ClassName", None) != WINUI_BRIDGE:
        return focus
    client = automation._AutomationClient.instance().IUIAutomation
    inner = focus.Element.FindFirst(4, client.CreatePropertyCondition(  # TreeScope_Descendants
        uia.PropertyId.HasKeyboardFocusProperty, True))
    return uia.Control.CreateControlFromElement(inner) if inner else focus

def _safe_controls(focus=None, foreground=None):
    controls, node = [], focus if focus is not None else focused()
    foreground = uia.GetForegroundWindow() if foreground is None else foreground
    skipped_renderer, hosted = False, None  # hosted: a WebView2 browser's own top-level window, until vouched for
    for _ in range(32):
        if node is None:
            return None if skipped_renderer else controls
        password = password_state(node)
        if password is None:
            if not controls:
                return None
            hwnd = node.NativeWindowHandle
            top = uia.GetAncestor(hwnd, uia.GAFlag.Root) if hwnd else None
            if hosted is None and top and top != foreground and _class(top) == BROWSER:
                hosted = top
            if hosted is not None and top == foreground:  # its WebView2 host, inside the foreground window
                if hwnd == foreground or not _native_host(node, foreground, names=(WEBVIEW_HOST,)):
                    return None
                hosted = None
            elif not _native_host(node, foreground, hosted, (RENDERER, BROWSER) if hosted else (RENDERER,)):
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
            return controls if hosted is None and _native_host(node, foreground) else None
        else:
            controls.append(node)
        node = node.GetParentControl()
    return None


def _class(hwnd):
    name = ctypes.create_unicode_buffer(64)
    return name.value if u32.GetClassNameW(hwnd, name, len(name)) else ""

def _native_host(control, foreground, root=None, names=(RENDERER,)):
    """A native window of `root` (the foreground window by default) that is that window itself or a verified
    Chromium window named in `names`."""
    hwnd = control.NativeWindowHandle
    if (not hwnd or not foreground
            or control.ControlType not in (uia.ControlType.WindowControl, uia.ControlType.PaneControl)
            or uia.GetAncestor(hwnd, uia.GAFlag.Root) != (root or foreground)
            or not uia.ControlsAreSame(control, uia.ControlFromHandle(hwnd))):
        return False
    if hwnd == foreground:
        return True
    return control.ClassName in names and _class(hwnd) == control.ClassName


def password_state(control):
    # The wrapper's IsPassword substitutes False for unsupported metadata.
    value = control.Element.GetCurrentPropertyValueEx(uia.PropertyId.IsPasswordProperty, True)
    return value if type(value) is bool else None


def read(clipboard_fallback: bool = True) -> str:
    """Read accessible selection text. The shared clipboard flag is retained but cannot enable unsafe copying."""
    text = _via_uia()
    return "" if text is PASSWORD else text.strip()[:MAX_CHARS]
