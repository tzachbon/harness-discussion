"""In-place editing on macOS: read the selection with its app and window, and replace it by pasting.

There is no AX setter that keeps the app's own undo, so text goes in through the pasteboard and Cmd+V,
and the user's pasteboard is put back afterwards.
"""
import os
import hashlib
import time

import AppKit
import Quartz
from ApplicationServices import (AXUIElementCopyAttributeValue, AXUIElementCreateApplication,
                                 AXUIElementIsAttributeSettable, AXUIElementSetAttributeValue, AXValueCreate,
                                 AXValueGetValue, kAXErrorSuccess, kAXValueCFRangeType)
from CoreFoundation import CFEqual, CFRangeMake

from .. import edit_logic
from ..edit_logic import Context
from . import pasteboard, permission, selection

KEY_V, KEY_LEFT, KEY_RIGHT = 9, 123, 124
EDITABLE_ROLES = {"AXTextArea", "AXTextField", "AXComboBox", "AXSearchField"}
PASTE_ACK_S = 1.0
MAX_DOCUMENT = 1_000_000
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


def _frontmost_pid():
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    return app.processIdentifier() if app is not None else None


def _document(element, rng):
    """Selection and fingerprint from one anchored plain-text value, or no proof."""
    value = _attr(element, "AXValue")
    if not isinstance(value, str) or len(value) > MAX_DOCUMENT or rng is None:
        return None, None
    start, length = rng
    if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (start, length)):
        return None, None
    raw = value.encode("utf-16-le", "surrogatepass")
    if start + length > len(raw) // 2:
        return None, None
    selected = raw[start * 2:(start + length) * 2].decode("utf-16-le", "surrogatepass")
    return selected, hashlib.sha256(raw).digest()


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
    text, digest = "", None
    rng = _range(element) if element is not None else None
    if not secure:
        text, digest = _document(element, rng) if element is not None else (None, None)
        if text is None:
            found, _ = selection._via_ax(pid)
            text = found or ""
        if not text and clipboard_fallback:
            text = pasteboard.copy_selection()
            digest = None  # Clipboard or ancestor text is not proof of the anchored element.
    window, editable = _window_number(pid), _editable(element)
    secure = secure or selection.secure_input() or (element is not None and selection.SECURE in (
        _attr(element, "AXRole"), _attr(element, "AXSubrole")))
    if secure:
        text, digest = "", None
    current = _focused(pid)
    if _frontmost_pid() != pid or element is None or current is None or not CFEqual(current, element):
        return None
    return Context(text, pid, window, editable, secure, element,
                   (rng[0], rng[1]) if rng is not None else None, digest)


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


def _paste(text, ctx=None, *, cancelled=lambda: False):
    """Pastes `text`. Puts the pasteboard back only once the field shows the paste landed and nothing else wrote to
    the pasteboard meanwhile. A paste that never shows up leaves `text` on the pasteboard, so a late paste is right."""
    if cancelled() or not pasteboard.safe_to_borrow(pasteboard.types()):
        return False
    board = AppKit.NSPasteboard.generalPasteboard()
    pid = _frontmost_pid()
    with pasteboard._borrow:
        saved = pasteboard.snapshot()
        board.clearContents()
        board.setString_forType_(text, AppKit.NSPasteboardTypeString)
        count = board.changeCount()
        attempted = False
        try:
            element = _focused(pid) if pid else None
            before = _field_state(element)
            if (ctx is not None and (pid != ctx.app or element is None or not CFEqual(element, ctx.field)
                                     or before is None or before[0] != ctx.caret
                                     or not _same_target(ctx, same_caret=True, dictation=True))
                    or board.changeCount() != count or cancelled()):
                return False
            attempted = True  # A partial native post may still paste later.
            _key(KEY_V, Quartz.kCGEventFlagMaskCommand)
        finally:
            if not attempted and edit_logic.restore_clipboard(board.changeCount(), count):
                pasteboard.restore(saved)
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


def _same_target(ctx, same_caret=False, *, dictation=False):
    """With a captured Context, the same app, window, and field is still focused, editable, and not secure.
    With `same_caret`, the selection or caret is also where it was at capture."""
    if ctx is None:
        return not dictation
    now = capture(False)
    return (now is not None and (now.app, now.window) == (ctx.app, ctx.window) and now.editable and not now.secure
            and ctx.field is not None and now.field is not None and bool(CFEqual(now.field, ctx.field))
            and (not same_caret or (ctx.caret is not None and now.caret == ctx.caret))
            and (not dictation or (ctx.editable and not ctx.secure and ctx._digest is not None
                                   and now._digest == ctx._digest and now.text == ctx.text)))


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


def type_at_caret(text, ctx=None, *, cancelled=lambda: False):
    return (not cancelled() and _same_target(ctx, same_caret=True, dictation=True)
            and _paste(text, ctx, cancelled=cancelled))


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
