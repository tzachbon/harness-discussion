"""Turns global mouse input into "text may have just been selected here" events.

Coordinates are top-left based on the main screen, in points, the way the core and popup use them.
"""
import time
import threading

import AppKit
import CoreFoundation as CF
import objc
import Quartz

from .. import hotkeys
from ..clicks import Clicks
from ..held_shortcut import HeldShortcut

MASK = (AppKit.NSEventMaskLeftMouseDown | AppKit.NSEventMaskLeftMouseUp |
        AppKit.NSEventMaskRightMouseDown | AppKit.NSEventMaskOtherMouseDown)


def top_left(point):
    height = AppKit.NSScreen.screens()[0].frame().size.height
    return point.x, height - point.y


class SelectionTrigger:
    """Handlers run on the main thread, so they must only hand work off, never block."""

    def __init__(self, on_selection, on_press):
        self.on_selection, self.on_press = on_selection, on_press
        self._clicks = Clicks(AppKit.NSEvent.doubleClickInterval())
        self._monitors = []

    def start(self):
        if self._monitors:
            return
        self._monitors.append(AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(MASK, self._event))

        def local(event):  # clicks on our own windows, which the global monitor never sees
            self._event(event)
            return event
        self._monitors.append(AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(MASK, local))

    def stop(self):
        for monitor in self._monitors:
            AppKit.NSEvent.removeMonitor_(monitor)
        self._monitors = []

    def _event(self, event):
        x, y = top_left(AppKit.NSEvent.mouseLocation())
        kind = event.type()
        if kind in (AppKit.NSEventTypeLeftMouseDown, AppKit.NSEventTypeRightMouseDown, AppKit.NSEventTypeOtherMouseDown):
            if kind == AppKit.NSEventTypeLeftMouseDown:
                self._clicks.down(x, y)
            self.on_press(x, y)
        elif kind == AppKit.NSEventTypeLeftMouseUp and self._clicks.up(x, y, time.monotonic()) and event.window() is None:
            self.on_selection(x, y)  # a drag or multi-click in another app


FLAGS = {"ctrl": AppKit.NSEventModifierFlagControl, "alt": AppKit.NSEventModifierFlagOption,
         "shift": AppKit.NSEventModifierFlagShift, "cmd": AppKit.NSEventModifierFlagCommand}
# ANSI key codes, so the shortcut follows the physical key under any keyboard layout (Hebrew, Russian...)
KEY_CODES = {0: "a", 1: "s", 2: "d", 3: "f", 4: "h", 5: "g", 6: "z", 7: "x", 8: "c", 9: "v", 11: "b", 12: "q",
             13: "w", 14: "e", 15: "r", 16: "y", 17: "t", 18: "1", 19: "2", 20: "3", 21: "4", 22: "6", 23: "5",
             25: "9", 26: "7", 28: "8", 29: "0", 31: "o", 32: "u", 34: "i", 35: "p", 37: "l", 38: "j", 40: "k",
             45: "n", 46: "m", 122: "f1", 120: "f2", 99: "f3", 118: "f4", 96: "f5", 97: "f6", 98: "f7",
             100: "f8", 101: "f9", 109: "f10", 103: "f11", 111: "f12"}
# keys that type no character, so the layout can't name them
SPECIAL = {36: "↩", 48: "⇥", 49: "Space", 51: "⌫", 53: "⎋", 71: "⌧", 76: "⌤", 114: "Help", 115: "↖",
           116: "⇞", 117: "⌦", 119: "↘", 121: "⇟", 123: "←", 124: "→", 125: "↓", 126: "↑",
           105: "F13", 107: "F14", 113: "F15", 106: "F16", 64: "F17", 79: "F18", 80: "F19", 90: "F20"}
KEYPAD = {65, 67, 69, 75, 78, 81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 92}  # type the same as the main keys
# Physical modifier keycodes and NX_DEVICE* masks from Apple's IOLLEvent.h.
MOD_CODES = {59: ("ctrl", 0x1), 62: ("ctrl", 0x2000), 56: ("shift", 0x2), 60: ("shift", 0x4),
             58: ("alt", 0x20), 61: ("alt", 0x40), 55: ("cmd", 0x8), 54: ("cmd", 0x10)}


def event_mods(event):
    flags = event.modifierFlags()
    return {name for name, flag in FLAGS.items() if flags & flag}


def event_spec(event):
    """The hotkeys string for a key-down event: letters, digits and F1-F12 by name, any other key by its code."""
    code = event.keyCode()
    return hotkeys.spec(event_mods(event), KEY_CODES.get(code) or f"key_{code}")


def _typed(code):
    """What the current keyboard layout types for a key, unmodified; "" when macOS can't say."""
    event = AppKit.NSEvent.eventWithCGEvent_(Quartz.CGEventCreateKeyboardEvent(None, code, True))
    return event.charactersIgnoringModifiers() if event else ""


def key_name(code):
    """A fixed name for keys that type no character, else what the current layout types."""
    if code in SPECIAL:
        return SPECIAL[code]
    text = _typed(code)
    if not text or not text.isprintable():
        return f"Key {code}"
    return ("Keypad " if code in KEYPAD else "") + text.upper()


class HotKey:
    """Watches key-downs for one shortcut. In another app it can't swallow the key, so that app sees it too.
    With `on_release`, it also reports when the key or one of its modifiers comes up, for hold shortcuts."""

    def __init__(self, spec, on_hotkey, on_release=None, *, on_activity=None, on_cancel=None):
        self.keys, self.on_hotkey = hotkeys.parse(spec), on_hotkey  # parsed, so modifier order doesn't matter
        self.on_release, self._tap, self._spoiled = on_release, False, False
        self._hold, self._closed = None, False
        if on_release is not None:
            options = {"on_activity": on_activity} if on_activity is not None else {}
            if on_cancel is not None:
                options["on_cancel"] = on_cancel
            self._hold = NativeHold(spec, on_hotkey, on_release, **options)
            return
        mask = AppKit.NSEventMaskKeyDown
        if self.keys[1] is None:
            mask |= AppKit.NSEventMaskKeyUp | AppKit.NSEventMaskFlagsChanged
        self._monitors = [AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, self._event)]

        def local(event):  # keys while one of our windows is active, which the global monitor never sees
            self._event(event)
            return event
        self._monitors.append(AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, local))

    def _event(self, event):
        if getattr(self, "_closed", False):
            return
        kind = event.type()
        if self.keys[1] is None:
            return self._modifiers_only(event, kind)
        if kind == AppKit.NSEventTypeKeyDown:
            if not event.isARepeat() and hotkeys.parse(event_spec(event)) == self.keys:
                self.on_hotkey()

    def _modifiers_only(self, event, kind):
        """A tap fires once the chord is let go with no other key pressed, so ⌃⌥R doesn't also fire ⌃⌥."""
        if kind == AppKit.NSEventTypeKeyDown:
            if event_mods(event) & self.keys[0]:  # typing before the chord starts doesn't cancel it
                self._tap, self._spoiled = False, True
            return
        if kind != AppKit.NSEventTypeFlagsChanged:
            return
        mods, chord = frozenset(event_mods(event)), self.keys[0]
        if not mods >= chord:  # the chord ended
            if self._tap:
                self.on_hotkey()
            self._tap = self._spoiled = False
        elif mods == chord and not self._spoiled:
            self._tap = True
        else:  # an extra modifier joined, so this hold is no longer a tap of the chord
            self._tap, self._spoiled = False, True

    def stop(self):
        if self._closed:
            return
        self._closed = True
        if self._hold:
            self._hold.stop()
            return
        for monitor in self._monitors:
            AppKit.NSEvent.removeMonitor_(monitor)
        self._monitors = []

    @property
    def active(self):
        return self._hold.active if self._hold else bool(self._monitors) and not self._closed


class NativeHold:
    """Observe input and binding health independently of the AppKit main loop."""

    def __init__(self, spec, on_down, on_up, *, on_activity=None, on_cancel=None):
        mods, key = hotkeys.parse(spec)
        primary = (next(code for code, name in KEY_CODES.items() if name == key)
                   if key in KEY_CODES.values() else int(key[4:]) if key else None)
        keymap = {code: name for code, (name, _) in MOD_CODES.items()}
        if primary is not None:
            keymap[primary] = primary
        self.tracker = HeldShortcut(mods, primary, keymap, on_down, on_up)
        self.on_activity, self.on_cancel = on_activity, on_cancel
        self._closed = False
        self._tap = self._source = self._loop = None
        self._callback = self._event  # retain the bridged callback until owner cleanup
        from .permission import trusted
        self._trusted = trusted
        mask = sum(1 << kind for kind in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp,
                   Quartz.kCGEventFlagsChanged, Quartz.kCGEventLeftMouseDown,
                   Quartz.kCGEventRightMouseDown, Quartz.kCGEventOtherMouseDown))
        try:
            if not self._trusted():  # the existing Accessibility watcher rebinds after a grant
                self._loss()
                self.stop()
                return
            self.tracker.seed({code: name for code, name in keymap.items() if self._key_down(code)})
            # Default preserves the existing Accessibility grant. Never suppress or alter events.
            self._tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                               Quartz.kCGEventTapOptionDefault, mask, self._callback, None)
            if self._tap is None:
                raise RuntimeError("event tap unavailable")
            self._source = CF.CFMachPortCreateRunLoopSource(None, self._tap, 0)
            if self._source is None:
                raise RuntimeError("event tap source unavailable")
            if not self.active:
                raise RuntimeError("event tap inactive")
            self._thread = threading.Thread(target=self._run, daemon=True, name="mac-native-hold")
            self._thread.start()
        except Exception:
            self._loss()
            self.stop()

    @property
    def active(self):
        with self.tracker.lock:
            try:
                return (not self._closed and self._tap is not None and bool(self._trusted()) and
                        bool(CF.CFMachPortIsValid(self._tap)) and bool(Quartz.CGEventTapIsEnabled(self._tap)))
            except Exception:
                return False

    @staticmethod
    def _key_down(code):
        return bool(Quartz.CGEventSourceKeyState(Quartz.kCGEventSourceStateCombinedSessionState, code))

    def _loss(self):
        with self.tracker.lock:
            if self._closed:
                return
            try:
                if self.on_activity is not None:
                    self.on_activity()
            except Exception:
                pass  # callback failures must never suppress the original native event
            finally:
                try:
                    self.tracker.interrupt()
                except Exception:
                    pass

    def _health(self):
        with self.tracker.lock:
            if self._closed:
                return
            if not CF.CFMachPortIsValid(self._tap):
                raise RuntimeError("event tap invalidated")
            if not self._trusted():
                self._loss()
                return
            if not Quartz.CGEventTapIsEnabled(self._tap):
                self._loss()
                if self._trusted() and CF.CFMachPortIsValid(self._tap):
                    Quartz.CGEventTapEnable(self._tap, True)
                return
            try:
                # Poll throughout transcription/final validation, including after full release.
                physical = {code: self._key_down(code) for code in self.tracker.keymap.keys() | self.tracker.held.keys()}
                self.tracker.reconcile(physical.__getitem__)
            except Exception:
                self._loss()

    def _run(self):
        added = False
        try:
            with objc.autorelease_pool():
                with self.tracker.lock:
                    if self._closed:
                        return
                    self._loop = CF.CFRunLoopGetCurrent()
                    CF.CFRunLoopAddSource(self._loop, self._source, CF.kCFRunLoopDefaultMode)
                    added = True
                while True:
                    with self.tracker.lock:
                        if self._closed:
                            break
                    result = CF.CFRunLoopRunInMode(CF.kCFRunLoopDefaultMode, .05, False)
                    with self.tracker.lock:
                        if self._closed:
                            break
                        if result not in (CF.kCFRunLoopRunTimedOut, CF.kCFRunLoopRunHandledSource):
                            raise RuntimeError("event tap owner loop exited")
                        self._health()
        except Exception:
            try:
                self._loss()
            finally:
                self.stop()
        finally:
            with self.tracker.lock:
                try:
                    if added:
                        CF.CFRunLoopRemoveSource(self._loop, self._source, CF.kCFRunLoopDefaultMode)
                finally:
                    self._loop = self._source = self._tap = self._callback = None

    def _event(self, proxy, kind, event, context):
        with self.tracker.lock:
            if self._closed:
                return event
            try:
                if kind in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                    self._loss()
                    self._health()
                    return event
                if not self.active:
                    self._loss()
                    return event
                if kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventRightMouseDown, Quartz.kCGEventOtherMouseDown):
                    if self.on_activity is not None:
                        self.on_activity()
                    return event
                if kind not in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp, Quartz.kCGEventFlagsChanged):
                    return event
                code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                if kind == Quartz.kCGEventKeyDown and code == 53 and self.on_cancel is not None:
                    self.on_cancel()
                flags = Quartz.CGEventGetFlags(event)
                name = MOD_CODES[code][0] if kind == Quartz.kCGEventFlagsChanged and code in MOD_CODES else code
                if (kind in (Quartz.kCGEventKeyDown, Quartz.kCGEventFlagsChanged) and
                        self.on_activity is not None and not self.tracker._required(name)):
                    self.on_activity()
                mods = {name for name, flag in FLAGS.items() if flags & flag}
                # Aggregate flags also heal a missed modifier up on the next keyboard event.
                for physical, name in list(self.tracker.held.items()):
                    if name in FLAGS and name not in mods:
                        self.tracker.event(physical, name, False)
                if kind == Quartz.kCGEventFlagsChanged:
                    if code in MOD_CODES:
                        name, side = MOD_CODES[code]
                        sides = sum(mask for mod, mask in MOD_CODES.values() if mod == name)
                        # Some keyboards omit side flags. flagsChanged still identifies the changed physical key.
                        down = bool(flags & side) if flags & sides else name in mods and code not in self.tracker.held
                        self.tracker.event(code, name, down)
                else:
                    repeat = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat)
                    self.tracker.event(code, code, kind == Quartz.kCGEventKeyDown,
                                       repeat=bool(repeat) or mods != self.tracker.mods)
            except Exception:
                self._loss()
            return event

    def stop(self):
        with self.tracker.lock:
            if self._closed:
                return
            self._closed = True
            try:
                self.tracker.stop()
            finally:
                if self._tap is not None:
                    for cleanup in (lambda: Quartz.CGEventTapEnable(self._tap, False),
                                    lambda: CF.CFMachPortInvalidate(self._tap)):
                        try:
                            cleanup()
                        except Exception:
                            pass
                if self._loop is not None:
                    for cleanup in (CF.CFRunLoopStop, CF.CFRunLoopWakeUp):
                        try:
                            cleanup(self._loop)
                        except Exception:
                            pass
