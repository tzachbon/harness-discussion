"""The macOS window-switching shortcut that can still beat ours: "Move focus to next window" with Stage Manager on."""
import AppKit
import CoreFoundation as CF

from .trigger import FLAGS, KEY_CODES


def window_switch_keys():
    """Shortcuts macOS cycles windows with before the event tap sees them: "Move focus to next window" and its ⇧
    reverse, while Stage Manager is on. With it off, the tap gets the key first and swallows it."""
    try:
        for domain in ("com.apple.WindowManager", "com.apple.symbolichotkeys"):
            CF.CFPreferencesAppSynchronize(domain)
        if not CF.CFPreferencesCopyAppValue("GloballyEnabled", "com.apple.WindowManager"):
            return set()
        system = CF.CFPreferencesCopyAppValue("AppleSymbolicHotKeys", "com.apple.symbolichotkeys") or {}
        entry = system.get("27")  # Move focus to next window. Absent until changed, so the default ⌘`
        if entry is None:
            code, flags = 50, AppKit.NSEventModifierFlagCommand
        elif not entry.get("enabled"):
            return set()
        else:
            _, code, flags = entry["value"]["parameters"]  # character, key code, modifier flags
        mods = frozenset(name for name, flag in FLAGS.items() if flags & flag)
        key = KEY_CODES.get(code) or f"key_{code}"
        return {(mods, key), (mods | {"shift"}, key)}
    except Exception:
        return set()
