"""Identify microphone users via macOS 14 CoreAudio process objects."""
import ctypes as c
import os

_ca = c.CDLL('/System/Library/Frameworks/CoreAudio.framework/CoreAudio')
_cf = c.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
_cf.CFStringGetCString.argtypes = [c.c_void_p, c.c_char_p, c.c_long, c.c_uint32]
_cf.CFRelease.argtypes = [c.c_void_p]


class Address(c.Structure):
    _fields_ = [('selector', c.c_uint32), ('scope', c.c_uint32), ('element', c.c_uint32)]


def _address(key):
    return Address(int.from_bytes(key.encode(), 'big'), int.from_bytes(b'glob', 'big'), 0)


def _read(obj, key, typ):
    address = _address(key)
    value = typ()
    size = c.c_uint32(c.sizeof(value))
    if _ca.AudioObjectGetPropertyData(obj, c.byref(address), 0, None, c.byref(size), c.byref(value)):
        raise OSError(key)
    return value.value


def _processes():
    address = _address('prs#')
    size = c.c_uint32()
    if _ca.AudioObjectGetPropertyDataSize(1, c.byref(address), 0, None, c.byref(size)):
        raise OSError('prs#')
    objects = (c.c_uint32 * (size.value // 4))()
    if _ca.AudioObjectGetPropertyData(1, c.byref(address), 0, None, c.byref(size), objects):
        raise OSError('prs#')
    rows = []
    for obj in objects[:size.value // 4]:
        try:
            pid = _read(obj, 'ppid', c.c_int32)
            ref = _read(obj, 'pbid', c.c_void_p)
            buf = c.create_string_buffer(1024)
            try:
                bundle = buf.value.decode() if ref and _cf.CFStringGetCString(ref, buf, len(buf), 0x08000100) else ''
            finally:
                if ref:
                    _cf.CFRelease(ref)
        except OSError:
            continue
        active = _read(obj, 'piri', c.c_uint32)
        rows.append((pid, bundle, active))
    return rows


def users(own_pids=None):
    """Return active bundle IDs, or None when the process API is unavailable."""
    own = set(own_pids or ()) | {os.getpid()}
    try:
        rows = _processes()
    except OSError:
        return None
    return {bundle.lower() for pid, bundle, active in rows if active and bundle and pid not in own}
