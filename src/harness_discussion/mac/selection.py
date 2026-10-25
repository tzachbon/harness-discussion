"""Reads the current selection in the frontmost app through the Accessibility API."""
import ctypes
import os

import AppKit
from ApplicationServices import (AXUIElementCopyAttributeValue, AXUIElementCopyParameterizedAttributeValue,
                                 AXUIElementCreateApplication, AXUIElementCreateSystemWide,
                                 AXUIElementSetAttributeValue, AXUIElementSetMessagingTimeout, AXValueGetValue,
                                 kAXErrorSuccess, kAXValueCGPointType, kAXValueCGRectType)

from .. import richtext
from . import pasteboard, permission

MAX_CHARS = 4000
SECURE = "AXSecureTextField"
_system = AXUIElementCreateSystemWide()
AXUIElementSetMessagingTimeout(_system, 0.5)  # the default for every element: a hung app can't block reads
_manual = set()  # pids already asked to expose their accessibility tree
_carbon = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Carbon.framework/Carbon")
_carbon.IsSecureEventInputEnabled.restype = ctypes.c_bool


def secure_input() -> bool:
    """True while a password field anywhere has keyboard focus."""
    return _carbon.IsSecureEventInputEnabled()


def _attr(element, name):
    err, value = AXUIElementCopyAttributeValue(element, name, None)
    return value if err == kAXErrorSuccess else None


def _expose(pid):
    """Chromium and Electron apps build their accessibility tree only when asked."""
    if pid not in _manual:
        _manual.add(pid)
        AXUIElementSetAttributeValue(AXUIElementCreateApplication(pid), "AXManualAccessibility", True)


def _via_ax(pid, need_web=False):
    """(selected text, whether it is in a web page), checking the page only for multi-line text when asked."""
    # The system-wide element often fails with kAXErrorCannotComplete. Asking the app works.
    element = _attr(AXUIElementCreateApplication(pid), "AXFocusedUIElement")
    for _ in range(6):
        if element is None:
            return "", False
        if SECURE in (_attr(element, "AXRole"), _attr(element, "AXSubrole")):
            return None, False
        text = _attr(element, "AXSelectedText")
        if text:
            text = str(text)
            return text, need_web and "\n" in text and _in_web(element)
        element = _attr(element, "AXParent")
    return "", False


def _in_web(element):
    """Browser and Electron pages nest deeper than the text search walks."""
    for _ in range(25):
        role = _attr(element, "AXRole") if element is not None else None
        if role == "AXWebArea":
            return True
        if role in (None, "AXWindow", "AXApplication"):
            return False
        element = _attr(element, "AXParent")
    return False


def bounds():
    """Top-left of the selected text in the frontmost app, else of its focused field, else of its focused window.
    Top-left based like the pointer. None when the app reports none of them."""
    pid = _frontmost_pid()
    app = AXUIElementCreateApplication(pid) if pid else None
    focused = _attr(app, "AXFocusedUIElement") if app else None
    element = focused
    for _ in range(6):
        if element is None:
            break
        span = _attr(element, "AXSelectedTextRange")
        if span is not None:
            err, value = AXUIElementCopyParameterizedAttributeValue(element, "AXBoundsForRange", span, None)
            ok, rect = AXValueGetValue(value, kAXValueCGRectType, None) if err == kAXErrorSuccess else (False, None)
            if ok and rect.size.width and rect.size.height:  # some apps answer an empty rect
                return rect.origin.x, rect.origin.y
            break
        element = _attr(element, "AXParent")
    for element in (focused, _attr(app, "AXFocusedWindow") if app else None):
        position = _attr(element, "AXPosition") if element is not None else None
        ok, point = AXValueGetValue(position, kAXValueCGPointType, None) if position is not None else (False, None)
        if ok:
            return point.x, point.y
    return None


def _frontmost_pid():
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    return app.processIdentifier() if app is not None else None


def read(clipboard_fallback: bool = True) -> str:
    if not permission.trusted():
        return ""
    pid = _frontmost_pid()
    if pid is None or pid == os.getpid():
        return ""
    _expose(pid)
    text, in_web = _via_ax(pid, clipboard_fallback)
    if text is None or _frontmost_pid() != pid:
        return ""  # a password field, or the user switched apps while we read
    if not text.strip() and clipboard_fallback and not secure_input():
        text = pasteboard.copy_selection()
        if _frontmost_pid() != pid:
            return ""  # the switch came during the copy, so Cmd+C reached the other app
    elif in_web and clipboard_fallback and not richtext.has_numbers(text) and not secure_input():
        # web pages draw list numbers with CSS: the selected text drops them, the copied HTML keeps them
        _, html = pasteboard.copy_selection(html=True)
        if _frontmost_pid() != pid:
            return ""
        if html and "<ol" in html:
            text = richtext.html_to_text(html)
    return text.strip()[:MAX_CHARS]
