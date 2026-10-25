"""The read-selection shortcut, stored as a pynput hotkey string such as "<ctrl>+<alt>+r"."""
import re
import sys

MODS = ("ctrl", "alt", "shift", "cmd")  # display order
KEYS = {*"abcdefghijklmnopqrstuvwxyz0123456789", *(f"f{n}" for n in range(1, 13))}
CODE = re.compile(r"key_[0-9]{1,3}")  # any other key by its code: macOS keyCode or Windows virtual key, both < 256
DEFAULT = "<cmd>+<alt>+r" if sys.platform == "darwin" else "<ctrl>+<alt>+r"
# Windows avoids Ctrl+Alt: AltGr sends it, so AltGr+E (€ on many layouts) would also start a rewrite
REWRITE = "<cmd>+<alt>+e" if sys.platform == "darwin" else "<ctrl>+<shift>+e"
DICTATE = "<cmd>+<alt>+d" if sys.platform == "darwin" else "<ctrl>+<shift>+d"  # held
SHORTCUTS = ("hotkey", "rewrite_hotkey", "dictate_hotkey")


def conflict(cfg, key, spec):
    """The other shortcut setting that already uses `spec`, or None."""
    parsed = parse(spec)
    return next((k for k in SHORTCUTS if k != key and parsed and parse(cfg.get(k)) == parsed), None)


def parse(spec):
    """(modifiers, key) for a valid shortcut, with or without modifiers, else None. key is None for modifiers alone."""
    if not isinstance(spec, str) or not spec:
        return None
    mods, keys = set(), []
    for part in spec.lower().split("+"):
        name = part[1:-1] if part.startswith("<") and part.endswith(">") else part
        if part == f"<{name}>" and name in MODS:
            mods.add(name)
        elif (name in KEYS or CODE.fullmatch(name)) and (part == name) == (len(name) == 1):  # a letter bare, others in <>
            keys.append(name)
        else:
            return None
    if not keys and mods:
        return frozenset(mods), None
    return (frozenset(mods), keys[0]) if len(keys) == 1 else None


def spec(mods, key=None):
    """The stored string for modifiers and a key, or None when they don't make a valid shortcut."""
    key = key.lower() if key else None
    text = "+".join([f"<{m}>" for m in MODS if m in mods] + ([key if len(key) == 1 else f"<{key}>"] if key else []))
    return text if parse(text) else None



def key_label(key):
    """How a key reads in Settings; a key stored by code is named by the current keyboard layout."""
    if not key.startswith("key_"):
        return key.upper()
    if sys.platform == "darwin":
        from .mac.trigger import key_name
    else:
        from .win.trigger import key_name
    return key_name(int(key[4:]))


def keys(text):
    """Each key's name in display order, one per keycap; [] when the shortcut is off or invalid."""
    parsed = parse(text)
    if not parsed:
        return []
    mods, key = parsed
    names = ({"ctrl": "⌃", "alt": "⌥", "shift": "⇧", "cmd": "⌘"} if sys.platform == "darwin"
             else {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win"})
    return [names[m] for m in MODS if m in mods] + ([key_label(key)] if key else [])

def label(text):
    names = keys(text)
    return ("" if sys.platform == "darwin" else "+").join(names) if names else "Off"
