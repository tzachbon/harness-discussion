"""Guarded Windows edit targets. COM stays on one MTA worker, Context carries only primitive snapshots."""
import ctypes
import hashlib
import os
import time
from dataclasses import dataclass, field
from ctypes import wintypes

import uiautomation as uia

from .. import edit_logic, live
from . import clipboard, selection, uia_thread
from . import input as keys

u32 = ctypes.windll.user32
u32.GetForegroundWindow.restype = wintypes.HWND
u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                           ctypes.POINTER(wintypes.DWORD)]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
MAX_DOCUMENT = 1_000_000
NEWLINE = "\r\n"
START, END = uia.TextPatternRangeEndpoint.Start, uia.TextPatternRangeEndpoint.End


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
    _insertion_attempted: bool = field(default=False, repr=False, compare=False)


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


def _inspect(limit=MAX_DOCUMENT):
    """(Context, document, selection range, document range), or None. The ranges are None when the focus has no
    usable text pattern."""
    identity = _foreground()
    if not identity:
        return None
    app, window = identity
    focus = uia.GetFocusedControl()
    ancestors = selection._safe_controls(focus, window)
    if ancestors is selection.PASSWORD:
        return Context("", app, window, False, True), "", None, None
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
        document = whole.GetText(limit + 1)
        if len(document) > limit:
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
        return context, document, span, whole
    return Context("", app, window, False, False, element), "", None, None


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


def _replace(text, context, cancelled):
    with clipboard.BORROW_LOCK:
        if cancelled():
            return False
        found = _inspect()
        if not found:
            return False
        current, document, *_ = found
        context = context if context is not None else current
        if not _matches(context, current):
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
                if (check and _matches(context, check[0]) and clipboard.sequence() == ours and not cancelled()
                        and keys.chord(ord("V"),
                                       on_attempt=lambda: setattr(context, "_insertion_attempted", True))):
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


def replace(text, ctx=None, *, cancelled=lambda: False):
    """True only after exact document/caret verification. False after input must never be retried automatically."""
    if ctx is not None:
        ctx._insertion_attempted = False
    if not isinstance(text, str) or "\0" in text or (ctx is not None and ctx.secure):
        return False
    return bool(_run(_replace, text, ctx, cancelled))


def type_at_caret(text, ctx=None, *, cancelled=lambda: False):
    """Insert at the unchanged caret, or replace its unchanged selection, like a normal paste."""
    return replace(text, ctx, cancelled=cancelled)


def undo(ctx, original, inserted):
    """Keep the replacement receipt on the context retained by Writer for later Mode/Review/Undo actions."""
    def restore():
        if not select_back(utf16_length(inserted), ctx):
            return False
        post = ctx._receipt[0]
        selected = capture(False)
        if (selected is None or selected.secure or not selected.editable
                or (selected.app, selected.window, selected._element) != (ctx.app, ctx.window, ctx._element)
                or selected._digest != post._digest or selected._end != post._end
                or selected._start != post._end - len(_encoded(inserted)) // 2
                or _canonical(selected.text) != _canonical(inserted)):
            return False
        if not replace(original, selected):
            return False
        ctx._receipt = selected._receipt
        return True
    return bool(_run(restore))


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

# Check as you type: read the focused field and place text ranges without the clipboard or keys.

def _exe(pid):
    """The process's executable name, lowercased ("slack.exe"), or ""."""
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
        if not k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value).lower()
    finally:
        k32.CloseHandle(handle)

def observe():
    """The focused editable, non-secure field as an edit_logic.Field, or None. Never another app's terminal, never
    this app's own windows, never a document longer than live.LIVE_MAX_DOC."""
    def look():
        identity = _foreground()
        if not identity or identity[0] == os.getpid():
            return None  # checked before any UIA call reaches our own Tk windows
        found = _inspect(live.LIVE_MAX_DOC)
        if not found or found[2] is None:
            return None
        context, document = found[0], found[1]
        if context.secure or not context.editable or context._start is None:
            return None
        name = _exe(context.app)
        if not name:
            return None
        try:
            r = uia.GetFocusedControl().BoundingRectangle
            rect = (r.left, r.top, r.right, r.bottom)
        except Exception:
            rect = None
        return edit_logic.Field(context.app, context.window, name, name.rsplit(".", 1)[0].capitalize(),
                                _canonical(document), (context._start, context._end), context._digest, rect,
                                context._element)
    return _run(look)

def _offset(whole, document, candidate, endpoint):
    """Where `candidate`'s endpoint is, in the canonical units _inspect uses. Between "\\r" and "\\n" counts as before
    the line break, so the next move steps past it."""
    prefix = whole.Clone()
    if not prefix.MoveEndpointByRange(END, candidate, endpoint, waitTime=0):
        return None
    before = prefix.GetText(MAX_DOCUMENT + 1)
    split = before.endswith("\r") and document[len(before):len(before) + 1] == "\n"
    return len(_encoded(before)) // 2 - split

def _range_at(whole, document, start, end, expected):
    """A range over canonical [start, end) whose text is `expected`, or None. Providers count characters their own
    way (CRLF as two, an emoji as one, a family emoji as one), so each endpoint moves by the measured difference in
    code points, and by one character once a move overshoots."""
    canonical = _encoded(document)
    points = lambda u: len(canonical[:max(u, 0) * 2].decode("utf-16-le", "surrogatepass"))
    candidate, last = whole.Clone(), {}
    if not candidate.MoveEndpointByRange(END, candidate, START, waitTime=0):
        return None
    for _ in range(12):
        at = [_offset(whole, document, candidate, e) for e in (START, END)]
        if None in at:
            return None
        if at == [start, end]:
            return candidate if _encoded(candidate.GetText(MAX_DOCUMENT + 1)) == _encoded(expected) else None
        steps = []
        for endpoint, now, goal in ((START, at[0], start), (END, at[1], end)):
            step = points(goal) - points(now)
            if step and last.get(endpoint, step) * step < 0:  # overshot: walk one character at a time
                step = 1 if step > 0 else -1
            last[endpoint] = step or last.get(endpoint, 0)
            steps.append((endpoint, step))
        for endpoint, step in steps[::-1] if steps[1][1] > 0 else steps:  # never move one endpoint past the other
            if step:
                candidate.MoveEndpointByUnit(endpoint, uia.TextUnit.Character, step, waitTime=0)
    return None

def _same_field(field, found):
    return bool(found and found[3] is not None and not found[0].secure and found[0].editable
                and (found[0].app, found[0].window, found[0]._element) == (field.app, field.window, field.handle)
                and found[0]._digest == field.digest)

def bounds(field, spans):
    """Screen rectangles per (start, end, expected) span of `field`: a list (empty when scrolled out of view), or
    None when the span cannot be placed exactly. None for the whole call when the field changed."""
    def place():
        found = _inspect(live.LIVE_MAX_DOC)
        if not _same_field(field, found):
            return None
        out = []
        for start, end, expected in spans:
            candidate = _range_at(found[3], found[1], start, end, expected)
            out.append(None if candidate is None else
                       [(r.left, r.top, r.right, r.bottom) for r in candidate.GetBoundingRectangles()])
        return out
    return _run(place)

def _collapse(span):
    caret = span.Clone()
    if caret.MoveEndpointByRange(END, caret, START, waitTime=0):
        caret.Select(waitTime=0)

def collapse():
    """Collapses the focused field's selection to its start, so a refused paste leaves nothing selected."""
    def run():
        found = _inspect(live.LIVE_MAX_DOC)
        if found and found[2] is not None and found[0]._start != found[0]._end:
            _collapse(found[2])
    _run(run)

def select_range(field, start, end, expected):
    """Selects [start, end) of the unchanged `field` and returns the Context for replace(), or None. A selection
    that does not read back exactly is collapsed again, so the next key press cannot overwrite it."""
    def select():
        found = _inspect(live.LIVE_MAX_DOC)
        if not _same_field(field, found):
            return None
        candidate = _range_at(found[3], found[1], start, end, expected)
        if candidate is None or not candidate.Select(waitTime=0):
            return None
        result = _inspect(live.LIVE_MAX_DOC)
        if (_same_field(field, result) and result[0]._start == start and result[0]._end == end
                and _canonical(result[0].text) == expected):
            return result[0]
        if result and result[2] is not None:
            _collapse(result[2])
        return None
    return _run(select)
