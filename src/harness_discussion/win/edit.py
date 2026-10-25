"""Guarded Windows edit targets. COM stays on one MTA worker, Context carries only primitive snapshots."""
import ctypes
import hashlib
import time
from dataclasses import dataclass, field
from ctypes import wintypes

import uiautomation as uia

from . import clipboard, selection, uia_thread
from . import input as keys

u32 = ctypes.windll.user32
u32.GetForegroundWindow.restype = wintypes.HWND
u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
MAX_DOCUMENT = 1_000_000


@dataclass
class Context:
    text: str
    app: int
    window: int
    editable: bool
    secure: bool
    _element: tuple = ()
    _start: int | None = None
    _end: int | None = None
    _digest: bytes = b""
    _units: int = 0
    _receipt: tuple | None = field(default=None, repr=False, compare=False)


def _canonical(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _encoded(text):
    return _canonical(text).encode("utf-16-le", "surrogatepass")


def utf16_length(text):
    """Windows plain-text units, including CRLF; these are not keyboard navigation steps."""
    return len(_canonical(text).replace("\n", "\r\n").encode("utf-16-le", "surrogatepass")) // 2


def _run(fn, *args):
    return uia_thread.run(fn, *args)


def _foreground():
    hwnd = u32.GetForegroundWindow()
    pid, name = wintypes.DWORD(), ctypes.create_unicode_buffer(256)
    if not hwnd or not u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
        return None
    if not u32.GetClassNameW(hwnd, name, len(name)) or name.value in selection.TERMINALS:
        return None
    return pid.value, hwnd


def _inspect():
    identity = _foreground()
    if not identity:
        return None
    app, window = identity
    focus = uia.GetFocusedControl()
    ancestors = selection._safe_controls(focus, window)
    if ancestors is selection.PASSWORD:
        return Context("", app, window, False, True), "", None
    if not ancestors:
        return None
    element = tuple(focus.GetRuntimeId())
    if not element:
        return None
    for node in ancestors:
        pattern = node.GetPattern(uia.PatternId.TextPattern)
        if not pattern:
            continue
        ranges = pattern.GetSelection()
        if len(ranges) != 1:
            return None
        span = ranges[0]
        value = node.GetPattern(uia.PatternId.ValuePattern)
        writable = (not value.IsReadOnly if value else
                    span.GetAttributeValue(uia.TextAttributeId.IsReadOnlyAttribute) is False)
        whole = pattern.DocumentRange
        if whole.GetChildren():
            return None  # plain text cannot prove positions across embedded objects
        document = whole.GetText(MAX_DOCUMENT + 1)
        if len(document) > MAX_DOCUMENT:
            return None
        text = span.GetText(MAX_DOCUMENT + 1)
        # uiautomation 2.0.29 CompareEndpoints forgets to unwrap its second argument.
        collapsed = span.textRange.CompareEndpoints(uia.TextPatternRangeEndpoint.Start, span.textRange,
                                                   uia.TextPatternRangeEndpoint.End) == 0
        if collapsed != (text == ""):
            return None
        prefix = whole.Clone()
        if not prefix.MoveEndpointByRange(uia.TextPatternRangeEndpoint.End, span,
                                          uia.TextPatternRangeEndpoint.Start, waitTime=0):
            return None
        before = prefix.GetText(MAX_DOCUMENT + 1)
        start, raw = len(_encoded(before)) // 2, _encoded(document)
        end = start + len(_encoded(text)) // 2
        if raw[start * 2:end * 2] != _encoded(text) or _foreground() != identity:
            return None
        context = Context(text, app, window, writable, False, element, start, end,
                          hashlib.sha256(raw).digest(), len(raw) // 2)
        return context, document, span
    return Context("", app, window, False, False, element), "", None


def capture(clipboard_fallback=True):
    """Preserve whitespace. Unknown or secure targets never become automatic edit targets."""
    def take():
        found = _inspect()
        if not found:
            return None
        return found[0]
    return _run(take)


def _matches(old, new):
    return bool(old and new and not old.secure and not new.secure and old.editable and new.editable
                and old._start is not None and old == new)


def same_context(context):
    def check():
        found = _inspect()
        return bool(found and _matches(context, found[0]))
    return bool(_run(check))


def _replace(text, context, caret_only):
    with clipboard.BORROW_LOCK:
        found = _inspect()
        if not found:
            return False
        current, document, _ = found
        context = context if context is not None else current
        if not _matches(context, current) or (caret_only and current._start != current._end):
            return False
        raw, addition = _encoded(document), _encoded(text)
        expected = raw[:current._start * 2] + addition + raw[current._end * 2:]
        digest, caret = hashlib.sha256(expected).digest(), current._start + len(addition) // 2
        saved, before = clipboard.snapshot_with_sequence()
        with clipboard.PreparedRestore(saved) as cleanup:
            data = (_canonical(text).replace("\n", "\r\n") + "\0").encode("utf-16-le", "surrogatepass")
            ours = clipboard.restore([(13, data)], expected_sequence=before)
            if ours is None:
                return False
            verified, post = False, None
            try:
                check = _inspect()
                if (check and _matches(context, check[0]) and clipboard.sequence() == ours
                        and keys.chord(ord("V"))):
                    deadline = time.monotonic() + 1.0
                    while time.monotonic() < deadline:
                        result = _inspect()
                        if not result:
                            break
                        post = result[0]
                        if ((post.app, post.window, post._element) != (current.app, current.window, current._element)
                                or post.secure or not post.editable):
                            break
                        if (post._digest == digest and post._units == len(expected) // 2
                                and post._start == post._end == caret):
                            verified = True
                            break
                        time.sleep(0.02)
            finally:
                try:
                    cleanup.apply(expected_sequence=ours)
                except OSError:
                    verified = False
            if verified:
                context._receipt = post, text
            return verified


def replace(text, ctx=None):
    """True only after exact document/caret verification. False after input must never be retried automatically."""
    if not isinstance(text, str) or "\0" in text or (ctx is not None and ctx.secure):
        return False
    return bool(_run(_replace, text, ctx, False))


def type_at_caret(text, ctx=None):
    if not isinstance(text, str) or "\0" in text or (ctx is not None and ctx.secure):
        return False
    return bool(_run(_replace, text, ctx, True))


def select_back(n, ctx=None):
    """Select only a verified insertion receipt, measuring provider text rather than guessing arrow counts."""
    if not isinstance(n, int) or isinstance(n, bool) or n < 0 or not ctx or ctx.secure or not ctx._receipt:
        return False
    post, inserted = ctx._receipt
    if utf16_length(inserted) != n:
        return False
    def select():
        found = _inspect()
        if not found or not _matches(post, found[0]) or post._start != post._end:
            return False
        candidate = found[2].Clone()
        for _ in range(n + 1):
            text = candidate.GetText(MAX_DOCUMENT + 1)
            units = utf16_length(text)
            if units >= n:
                break
            if not candidate.MoveEndpointByUnit(uia.TextPatternRangeEndpoint.Start, uia.TextUnit.Character,
                                                -1, waitTime=0):
                return False
        if units != n or _canonical(text) != _canonical(inserted):
            return False
        check = _inspect()
        if not check or not _matches(post, check[0]) or not candidate.Select(waitTime=0):
            return False
        result = _inspect()
        return bool(result and (result[0].app, result[0].window, result[0]._element)
                    == (post.app, post.window, post._element)
                    and not result[0].secure and result[0].editable and result[0]._digest == post._digest
                    and result[0]._start == post._start - len(_encoded(inserted)) // 2
                    and result[0]._end == post._end and _canonical(result[0].text) == _canonical(inserted))
    return bool(_run(select))
