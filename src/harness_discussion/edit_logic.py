"""The platform-free part of in-place editing: the context, guards, UTF-16 lengths, and the undo sequence.

A platform module (`mac/edit.py`, `win/edit.py`) provides `capture(clipboard_fallback)`, `replace(text, ctx)`,
`type_at_caret(text, ctx)`, and `select_back(n, ctx)`, each refusing when the target is no longer `ctx`'s, and
optionally `collapse()`.
"""
from dataclasses import dataclass, field as _field


@dataclass(frozen=True)
class Context:
    text: str
    app: int  # process id
    window: int  # window number (Mac) or hwnd (Windows)
    editable: bool
    secure: bool
    field: object = _field(default=None, compare=False)  # the platform's focused-element handle, or None
    caret: object = _field(default=None, compare=False)  # (start, length) of the selection, or None


@dataclass(frozen=True)
class Field:
    """The focused text field as Check as you type sees it, read without the clipboard or keys. `text` has "\\n" line
    breaks on Windows and is the raw AXValue on macOS, `caret` is the selection's (start, end) in UTF-16 units of
    `text`, and `digest` is the SHA-256 of `text` in UTF-16-LE. `app_name` is what the per-app off list stores; `label`
    is shown to the user."""
    app: int
    window: int
    app_name: str
    label: str
    text: str
    caret: tuple
    digest: bytes
    rect: tuple = None  # (left, top, right, bottom) of the field on screen, where the badge goes; None when unknown
    handle: object = _field(default=None, compare=False)  # the platform's focused-element identity


def utf16_len(text: str, newline: str = "\n") -> int:
    """Length as the target stores it: UTF-16 code units, with each newline written as `newline`."""
    text = text.replace("\r\n", "\n").replace("\n", newline)
    return len(text.encode("utf-16-le")) // 2


def same_context(ctx: Context, now) -> bool:
    """Same app and window, and the selection still reads as the captured text. An empty read counts as changed."""
    return (now is not None and now.app == ctx.app and now.window == ctx.window
            and bool(now.text) and now.text == ctx.text)


def restore_clipboard(count_now: int, count_after_set: int) -> bool:
    """Put the user's clipboard back only if nothing else wrote to it after we did."""
    return count_now == count_after_set


def undo(platform, ctx: Context, original: str, inserted: str, newline: str = "\n") -> bool:
    """Select the inserted text, check it is still what we pasted in the same window, then put the original back.
    When `ctx.caret` is known, the selection must also be exactly where the paste landed.
    False when any check fails: the caller tells the user to use the app's own undo."""
    if hasattr(platform, "undo"):
        return platform.undo(ctx, original, inserted)
    n = utf16_len(inserted, newline)
    if not platform.select_back(n, ctx):
        return False
    now = platform.capture(True)
    caret = getattr(ctx, "caret", None)
    if (not same_context(Context(inserted, ctx.app, ctx.window, ctx.editable, ctx.secure), now)
            or (caret is not None and getattr(now, "caret", None) != (caret[0], n))):
        if hasattr(platform, "collapse"):
            platform.collapse()
        return False
    return platform.replace(original, now)
