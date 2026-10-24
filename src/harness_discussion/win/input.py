"""Balanced copy/paste input. Never releases a modifier that was already physically held."""
import ctypes
from ctypes import wintypes

u32 = ctypes.windll.user32
UP = 2
MODIFIERS = (0x10, 0x11, 0x12, 0x5B, 0x5C)


class _Keyboard(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class _Mouse(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("data", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class _Payload(ctypes.Union):
    _fields_ = [("ki", _Keyboard), ("mouse", _Mouse)]


class _Input(ctypes.Structure):
    _anonymous_ = ("payload",)
    _fields_ = [("type", wintypes.DWORD), ("payload", _Payload)]


u32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
u32.SendInput.restype = wintypes.UINT


def chord(key, on_attempt=None):
    """One Ctrl+key batch. Notify just before input, after held-key refusal. Never retry the action."""
    if any(u32.GetAsyncKeyState(vk) & 0x8000 for vk in (*MODIFIERS, key)):
        return False
    events = [(0x11, 0), (key, 0), (key, UP), (0x11, UP)]
    batch = (_Input * len(events))(*(_Input(type=1, ki=_Keyboard(wVk=vk, dwFlags=flag))
                                    for vk, flag in events))
    held = {0x11, key}
    if on_attempt is not None:
        on_attempt()
    try:
        count = u32.SendInput(len(batch), batch, ctypes.sizeof(_Input))
        if count == len(batch):
            return True
        held = set()
        for vk, flags in events[:count]:
            held.discard(vk) if flags & UP else held.add(vk)
    except OSError:
        pass  # delivery is unknown, so clean up only keys known to be up before this operation
    if held:
        cleanup = (_Input * len(held))(*(_Input(type=1, ki=_Keyboard(wVk=vk, dwFlags=UP))
                                        for vk in sorted(held, reverse=True)))
        try:
            u32.SendInput(len(cleanup), cleanup, ctypes.sizeof(_Input))
        except OSError:
            pass
    return False
