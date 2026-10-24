"""Explicit clipboard writes and conditional restoration after temporary pastes."""
import ctypes
import threading
import time
from ctypes import c_size_t, c_uint, c_void_p
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32.GetClipboardData.restype = k32.GlobalLock.restype = k32.GlobalAlloc.restype = c_void_p
u32.GetClipboardData.argtypes, u32.SetClipboardData.argtypes = [c_uint], [c_uint, c_void_p]
k32.GlobalLock.argtypes = k32.GlobalUnlock.argtypes = k32.GlobalSize.argtypes = [c_void_p]
k32.GlobalSize.restype, k32.GlobalAlloc.argtypes = c_size_t, [c_uint, c_size_t]
u32.SetClipboardData.restype = c_void_p
k32.GlobalFree.argtypes, k32.GlobalFree.restype = [c_void_p], c_void_p
u32.OpenClipboard.argtypes = [wintypes.HWND]
u32.GetClipboardOwner.restype = wintypes.HWND
u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, c_void_p]
u32.CreateWindowExW.restype = wintypes.HWND
u32.DestroyWindow.argtypes = [wintypes.HWND]
u32.IsWindow.argtypes = [wintypes.HWND]
BORROW_LOCK = threading.RLock()
_owner = threading.local()

BITMAP, DIB, DIBV5 = 2, 8, 17
METAFILES = {3, 14}  # CF_METAFILEPICT, CF_ENHMETAFILE: GDI handles, not byte-copyable
SKIP = {BITMAP, 9, 0x80, 0x82, 0x83, 0x8E}  # synthesized or GDI-handle formats we don't copy


def safe_to_borrow(formats: set[int]) -> bool:
    """True when snapshot() can capture everything the clipboard holds."""
    if formats & METAFILES:
        return False
    if formats & (SKIP - {BITMAP}):
        return False
    return BITMAP not in formats or bool(formats & {DIB, DIBV5})


class _Open:
    def __enter__(self):
        BORROW_LOCK.acquire()
        try:
            native, hwnd = getattr(_owner, "window", (None, None))
            if native is not u32 or not hwnd or not u32.IsWindow(hwnd):
                hwnd = u32.CreateWindowExW(0, "STATIC", "Harness Discussion clipboard", 0,
                                          0, 0, 0, 0, None, None, None, None)
                _owner.window = u32, hwnd
            self.hwnd = hwnd
            if not self.hwnd:
                raise OSError("clipboard owner unavailable")
            for _ in range(20):
                if u32.OpenClipboard(self.hwnd):
                    return self
                time.sleep(0.01)
            raise OSError("clipboard busy")
        except BaseException:
            BORROW_LOCK.release()
            raise

    def __exit__(self, *exc):
        try:
            u32.CloseClipboard()
        finally:
            BORROW_LOCK.release()


def formats() -> set[int]:
    with _Open():
        found, fmt = set(), 0
        while fmt := u32.EnumClipboardFormats(fmt):
            found.add(fmt)
        return found


def sequence() -> int:
    return u32.GetClipboardSequenceNumber()


def _owned_sequence(hwnd):
    current = sequence()
    return current if u32.GetClipboardOwner() == hwnd else None


def snapshot() -> list[tuple[int, bytes]]:
    with _Open():
        return _snapshot_locked()


def snapshot_with_sequence():
    with _Open():
        return _snapshot_locked(), sequence()


def _snapshot_locked():
    found, fmt = [], 0
    while True:
        ctypes.set_last_error(0)
        fmt = u32.EnumClipboardFormats(fmt)
        if not fmt:
            if ctypes.get_last_error():
                raise OSError("clipboard enumeration failed")
            break
        found.append(fmt)
    if not safe_to_borrow(set(found)):
        raise OSError("clipboard formats cannot be preserved")
    saved = []
    for fmt in found:
        if fmt in SKIP:
            continue  # a bitmap backed by a DIB is synthesized again on restore
        h = u32.GetClipboardData(fmt)
        size = k32.GlobalSize(h) if h else 0
        p = k32.GlobalLock(h) if size else None
        if not p:
            raise OSError("clipboard format cannot be preserved")
        try:
            saved.append((fmt, ctypes.string_at(p, size)))
        finally:
            k32.GlobalUnlock(h)
    return saved


def _allocate(saved):
    handles = []
    try:
        for fmt, data in saved:
            h = k32.GlobalAlloc(0x0042, len(data))  # movable, zero-initialized; owned by us until Set
            if not h:
                raise OSError("clipboard allocation failed")
            handles.append([fmt, h])
            p = k32.GlobalLock(h)
            if not p:
                raise OSError("clipboard allocation failed")
            try:
                ctypes.memmove(p, data, len(data))
            finally:
                k32.GlobalUnlock(h)
        return handles
    except BaseException:
        _free(handles)
        raise


def _free(handles):
    for _, handle in handles:
        if handle:
            k32.GlobalFree(handle)


def _install_locked(handles):
    if not u32.EmptyClipboard():
        raise OSError("clipboard write failed")
    for item in handles:
        if not u32.SetClipboardData(*item):
            raise OSError("clipboard write failed")
        item[1] = None  # ownership transferred to Windows


def _materialize_text_locked():
    # Synthesized text formats can change the sequence on first retrieval.
    for fmt in (1, 7, 13, 16):
        if u32.IsClipboardFormatAvailable(fmt) and not u32.GetClipboardData(fmt):
            raise OSError("clipboard text rendering failed")


class PreparedRestore:
    """Allocate the original before borrowing; cleanup never snapshots temporary target data."""
    def __init__(self, saved):
        self.handles = _allocate(saved)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        _free(self.handles)
        self.handles = []

    def apply(self, expected_sequence):
        with _Open() as opened:
            if sequence() != expected_sequence:
                return None
            _install_locked(self.handles)
            _materialize_text_locked()
        return _owned_sequence(opened.hwnd)


def restore(saved: list[tuple[int, bytes]], expected_sequence=None):
    """Replace only if still ours. Sequence is checked while locked, before clearing anything."""
    with _Open() as opened:
        if expected_sequence is not None and sequence() != expected_sequence:
            return None
        current = _snapshot_locked()
        incoming, backup = _allocate(saved), []
        try:
            backup = _allocate(current)
            try:
                _install_locked(incoming)
                _materialize_text_locked()
            except OSError:
                _install_locked(backup)
                raise
        finally:
            _free(incoming)
            _free(backup)
    # CloseClipboard synthesizes text formats and advances the sequence. Do not adopt another owner's copy.
    return _owned_sequence(opened.hwnd)


def write_text(text: str, *, cancelled=lambda: False) -> bool:
    """Explicit Copy replaces old contents, including formats we cannot snapshot."""
    if not isinstance(text, str) or "\0" in text:
        raise ValueError("Invalid clipboard text")
    if cancelled():
        return False
    incoming = _allocate([(13, (text + "\0").encode("utf-16-le"))])
    try:
        with _Open():
            if cancelled():
                return False
            _install_locked(incoming)
    finally:
        _free(incoming)
    return True


def html() -> str:
    """The "HTML Format" flavor (CF_HTML, UTF-8), which keeps ordered-list numbers."""
    with _Open():
        return _html_locked()


def _html_locked():
    h = u32.GetClipboardData(u32.RegisterClipboardFormatW("HTML Format"))
    if not h or not (p := k32.GlobalLock(h)):
        return ""
    try:
        return ctypes.string_at(p, k32.GlobalSize(h)).split(b"\0", 1)[0].decode("utf-8", "replace")
    finally:
        k32.GlobalUnlock(h)


def text() -> str:
    with _Open():
        return _text_locked()


def _text_locked():
    h = u32.GetClipboardData(13)  # CF_UNICODETEXT
    if not h or not (p := k32.GlobalLock(h)):
        return ""
    try:
        data = ctypes.string_at(p, k32.GlobalSize(h))
        return data.decode("utf-16-le", "replace").split("\0", 1)[0]
    finally:
        k32.GlobalUnlock(h)
