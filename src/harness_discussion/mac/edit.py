"""In-place editing on macOS: read the selection with its app and window, and replace it by pasting.

There is no AX setter that keeps the app's own undo, so text goes in through the pasteboard and Cmd+V,
and the user's pasteboard is put back afterwards.
"""
import hashlib
import os
import time

import AppKit
import Quartz
import ApplicationServices as AS  # Check as you type reads its names when used
from ApplicationServices import (AXUIElementCopyAttributeValue, AXUIElementCreateApplication,
                                 AXUIElementIsAttributeSettable, AXUIElementSetAttributeValue, AXValueCreate,
                                 AXValueGetValue, kAXErrorSuccess, kAXValueCFRangeType)
from CoreFoundation import CFEqual, CFRangeMake

from .. import edit_logic, live
from ..edit_logic import Context
from . import pasteboard, permission, selection

KEY_V, KEY_LEFT, KEY_RIGHT = 9, 123, 124
EDITABLE_ROLES = {"AXTextArea", "AXTextField", "AXComboBox", "AXSearchField"}
PASTE_ACK_S = 1.0
NEWLINE = "\n"


def _attr(element, name):
    err, value = AXUIElementCopyAttributeValue(element, name, None)
    return value if err == kAXErrorSuccess else None


def _focused(pid):
    return _attr(AXUIElementCreateApplication(pid), "AXFocusedUIElement")


def _window_number(pid):
    """The frontmost on-screen window of `pid`, or 0."""
    infos = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID)
    for info in infos or []:
        if info.get("kCGWindowOwnerPID") == pid and info.get("kCGWindowLayer") == 0:
            return int(info.get("kCGWindowNumber", 0))
    return 0


def _editable(element):
    if element is None:
        return False
    if _attr(element, "AXRole") in EDITABLE_ROLES:
        return True
    err, settable = AXUIElementIsAttributeSettable(element, "AXValue", None)
    return err == kAXErrorSuccess and bool(settable)


def _frontmost_app():
    return AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()

def _frontmost_pid():
    app = _frontmost_app()
    return app.processIdentifier() if app is not None else None


def capture(clipboard_fallback=False):
    """The selection with its app and window, or None when nothing is readable (or it is our own window)."""
    if not permission.trusted():
        return None
    pid = _frontmost_pid()
    if pid is None or pid == os.getpid():
        return None
    selection._expose(pid)
    element = _focused(pid)
    secure = selection.secure_input() or (element is not None and selection.SECURE in (
        _attr(element, "AXRole"), _attr(element, "AXSubrole")))
    text = ""
    if not secure:
        found, _ = selection._via_ax(pid)
        text = found or ""
        if not text and clipboard_fallback:
            text = pasteboard.copy_selection()
    if _frontmost_pid() != pid:
        return None
    rng = _range(element) if element is not None else None
    return Context(text, pid, _window_number(pid), _editable(element), secure, element,
                   (rng[0], rng[1]) if rng is not None else None)


def same_context(ctx):
    return edit_logic.same_context(ctx, capture(clipboard_fallback=True))


def _key(code, flags=0):
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, code, down)
        Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def _field_state(element):
    """(selection range, character count) of `element`, or None when it exposes neither."""
    if element is None:
        return None
    rng = _range(element)
    state = ((rng[0], rng[1]) if rng is not None else None, _attr(element, "AXNumberOfCharacters"))
    return state if state != (None, None) else None


def _paste(text):
    """Pastes `text`. Puts the pasteboard back only once the field shows the paste landed and nothing else wrote to
    the pasteboard meanwhile. A paste that never shows up leaves `text` on the pasteboard, so a late paste is right."""
    if not pasteboard.safe_to_borrow(pasteboard.types()):
        return False
    board = AppKit.NSPasteboard.generalPasteboard()
    pid = _frontmost_pid()
    with pasteboard._borrow:
        saved = pasteboard.snapshot()
        board.clearContents()
        board.setString_forType_(text, AppKit.NSPasteboardTypeString)
        count = board.changeCount()
        element = _focused(pid) if pid else None
        before = _field_state(element)
        _key(KEY_V, Quartz.kCGEventFlagMaskCommand)
        deadline, landed = time.monotonic() + PASTE_ACK_S, False
        while before is not None and not landed and time.monotonic() < deadline:
            time.sleep(0.025)
            now = _focused(pid)
            if now is None or not CFEqual(now, element):
                break  # focus moved: whether the paste landed is unknown
            state = _field_state(element)
            landed = state is not None and state != before
        if landed and edit_logic.restore_clipboard(board.changeCount(), count):
            pasteboard.restore(saved)
    return True


def _same_target(ctx, same_caret=False):
    """With a captured Context, the same app, window, and field is still focused, editable, and not secure.
    With `same_caret`, the selection or caret is also where it was at capture."""
    if ctx is None:
        return True
    now = capture(False)
    return (now is not None and (now.app, now.window) == (ctx.app, ctx.window) and now.editable and not now.secure
            and ctx.field is not None and now.field is not None and bool(CFEqual(now.field, ctx.field))
            and (not same_caret or (ctx.caret is not None and now.caret == ctx.caret)))


def replace(text, ctx=None):
    """Replaces the selection. False if the target or selection moved, or the app ignored the paste."""
    if not _same_target(ctx, same_caret=True):
        return False
    pid = _frontmost_pid()
    before = _attr(_focused(pid), "AXSelectedText") if pid else None
    if not _paste(text):
        return False
    after = _attr(_focused(pid), "AXSelectedText") if pid else None
    return not (before and after and str(after) == str(before) and str(before) != text)


def type_at_caret(text, ctx=None):
    return _same_target(ctx, same_caret=True) and _paste(text)


def _range(element):
    value = _attr(element, "AXSelectedTextRange")
    if value is None:
        return None
    ok, rng = AXValueGetValue(value, kAXValueCFRangeType, None)
    return rng if ok else None


def select_back(n, ctx=None):
    """Selects the `n` UTF-16 units before the caret. AX range first, Shift+Left × n as the fallback."""
    if not _same_target(ctx):
        return False
    pid = _frontmost_pid()
    element = _focused(pid) if pid else None
    rng = _range(element) if element is not None else None
    if rng is not None and rng[0] >= n:
        value = AXValueCreate(kAXValueCFRangeType, CFRangeMake(rng[0] - n, n))
        if AXUIElementSetAttributeValue(element, "AXSelectedTextRange", value) == kAXErrorSuccess:
            return True
    for _ in range(n):
        _key(KEY_LEFT, Quartz.kCGEventFlagMaskShift)
    return True


def collapse():
    """Moves the caret to the end of the selection."""
    pid = _frontmost_pid()
    element = _focused(pid) if pid else None
    rng = _range(element) if element is not None else None
    if rng is not None:
        value = AXValueCreate(kAXValueCFRangeType, CFRangeMake(rng[0] + rng[1], 0))
        if AXUIElementSetAttributeValue(element, "AXSelectedTextRange", value) == kAXErrorSuccess:
            return
    _key(KEY_RIGHT)


# Check as you type: read the focused field and place text ranges without the pasteboard or keys.

# Terminals expose scrollback as an editable text area, which must never be sent anywhere.
TERMINALS = {"com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp-Stable", "net.kovidgoyal.kitty",
             "io.alacritty", "org.alacritty", "com.github.wez.wezterm", "com.mitchellh.ghostty"}

def _digest(text):
    return hashlib.sha256(text.encode("utf-16-le", "surrogatepass")).digest()

def _param(element, name, value):
    err, result = AS.AXUIElementCopyParameterizedAttributeValue(element, name, value, None)
    return result if err == kAXErrorSuccess else None

def _cf_range(start, length):
    return AXValueCreate(kAXValueCFRangeType, CFRangeMake(start, length))

def _rect_of(element):
    position, size = _attr(element, "AXPosition"), _attr(element, "AXSize")
    ok_p, p = AXValueGetValue(position, AS.kAXValueCGPointType, None) if position is not None else (False, None)
    ok_s, s = AXValueGetValue(size, AS.kAXValueCGSizeType, None) if size is not None else (False, None)
    if not (ok_p and ok_s):
        return None
    return int(p.x), int(p.y), int(p.x + s.width), int(p.y + s.height)

def observe():
    """The focused editable, non-secure field as an edit_logic.Field, or None. The text is the raw AXValue, since AX
    ranges count its UTF-16 units as they are."""
    if not permission.trusted():
        return None
    app = _frontmost_app()
    if app is None or app.processIdentifier() == os.getpid() or app.bundleIdentifier() in TERMINALS:
        return None
    pid, bundle = app.processIdentifier(), app.bundleIdentifier() or ""
    selection._expose(pid)
    element = _focused(pid)
    if element is None or not _editable(element) or selection.secure_input() or selection.SECURE in (
            _attr(element, "AXRole"), _attr(element, "AXSubrole")):
        return None
    count = _attr(element, "AXNumberOfCharacters")
    if count is not None and int(count) > live.LIVE_MAX_DOC:
        return None
    value, rng = _attr(element, "AXValue"), _range(element)
    if value is None or rng is None:
        return None
    text = str(value)
    if live.units(text) > live.LIVE_MAX_DOC or _frontmost_pid() != pid:
        return None
    return edit_logic.Field(pid, _window_number(pid), bundle, str(app.localizedName() or bundle), text,
                            (rng[0], rng[0] + rng[1]), _digest(text), _rect_of(element), element)

def _same_field(field):
    """The field's element, still focused, with the same text; or None."""
    if _frontmost_pid() != field.app:
        return None
    element = _focused(field.app)
    if element is None or not CFEqual(element, field.handle):
        return None
    value = _attr(element, "AXValue")
    return element if value is not None and _digest(str(value)) == field.digest else None

def _place(element, text, start, end, expected):
    """Rectangles for [start, end), one per line, or None when AX cannot prove the range reads `expected`."""
    if live.text_at(text, start, end) != expected:
        return None
    rects, at = [], start
    while at < end:
        line = _param(element, "AXLineForIndex", at)
        line_range = _param(element, "AXRangeForLine", line) if line is not None else None
        ok, rng = AXValueGetValue(line_range, kAXValueCFRangeType, None) if line_range is not None else (False, None)
        if not ok or rng[0] + rng[1] <= at:
            return None
        stop = min(end, rng[0] + rng[1])
        piece = _cf_range(at, stop - at)
        if str(_param(element, "AXStringForRange", piece) or "") != live.text_at(text, at, stop):
            return None
        bounds = _param(element, "AXBoundsForRange", piece)
        ok, r = AXValueGetValue(bounds, AS.kAXValueCGRectType, None) if bounds is not None else (False, None)
        if not ok:
            return None
        if r.size.width > 0 and r.size.height > 0:
            rects.append((int(r.origin.x), int(r.origin.y), int(r.origin.x + r.size.width),
                          int(r.origin.y + r.size.height)))
        at = stop
    return rects

def bounds(field, spans):
    """Screen rectangles (top-left origin) per (start, end, expected) span: a list, empty when not visible, or None
    when the span cannot be placed exactly. None for the whole call when the field changed."""
    element = _same_field(field)
    if element is None:
        return None
    return [_place(element, field.text, start, end, expected) for start, end, expected in spans]

def select_range(field, start, end, expected):
    """Selects [start, end) of the unchanged `field` and returns the Context for replace(), or None. A selection
    that does not read back exactly is collapsed again."""
    element = _same_field(field)
    if element is None or live.text_at(field.text, start, end) != expected:
        return None
    if AXUIElementSetAttributeValue(element, "AXSelectedTextRange", _cf_range(start, end - start)) != kAXErrorSuccess:
        return None
    now = capture(False)
    if (now is not None and (now.app, now.window) == (field.app, field.window) and now.editable and not now.secure
            and now.field is not None and CFEqual(now.field, field.handle) and now.caret == (start, end - start)
            and now.text == expected):
        return now
    collapse()
    return None
