"""Turns global mouse input into "text may have just been selected here" events.

Coordinates are top-left based on the main screen, in points, the way the core and popup use them.
"""
import threading
import time

import AppKit
import CoreFoundation as CF
import objc
import Quartz

from .. import hotkeys
from ..clicks import Clicks
from ..held_shortcut import HeldShortcut
from .keycodes import FLAGS, KEY_CODES, KEYPAD, MOD_CODES, SPECIAL

MASK = (AppKit.NSEventMaskLeftMouseDown | AppKit.NSEventMaskLeftMouseUp |
        AppKit.NSEventMaskRightMouseDown | AppKit.NSEventMaskOtherMouseDown)
KEY_MASK = AppKit.NSEventMaskKeyDown | AppKit.NSEventMaskFlagsChanged
SELECT_KEYS = {115, 116, 119, 121, 123, 124, 125, 126}  # Home, Page Up, End, Page Down, the arrows


def top_left(point):
    height = AppKit.NSScreen.screens()[0].frame().size.height
    return point.x, height - point.y


class SelectionTrigger:
    """Handlers run on the main thread, so they must only hand work off, never block."""

    def __init__(self, on_selection, on_press):
        self.on_selection, self.on_press = on_selection, on_press
        self._clicks = Clicks(AppKit.NSEvent.doubleClickInterval())
        self._monitors, self._keyed = [], False

    def start(self):
        if self._monitors:
            return
        self._monitors.append(AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(MASK, self._event))
        self._monitors.append(AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(KEY_MASK, self._key))

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

    def _key(self, event):
        """⇧ with an arrow, Home, End or a page key, or ⌘A, in another app. Fires once every modifier is up,
        so holding ⇧ to grow the selection reads it once."""
        mods = event_mods(event)
        if event.type() == AppKit.NSEventTypeKeyDown:
            code = event.keyCode()
            self._keyed = ("shift" in mods and code in SELECT_KEYS) or (mods == {"cmd"} and code == 0)
        elif self._keyed and not mods:
            self._keyed = False
            threading.Thread(target=self._at_selection, daemon=True).start()  # Accessibility can block

    def _at_selection(self):
        """Fires on the main thread at the selected text, its field or its window. The pointer is the last resort,
        as a pointer resting on the bar would make the core take the selection for a click on the bar."""
        from PyObjCTools.AppHelper import callAfter  # here, so tests can load this module with stub frameworks
        from . import selection
        time.sleep(0.08)  # let the app finish updating its selection
        point = selection.bounds()
        callAfter(lambda: self.on_selection(*(point or top_left(AppKit.NSEvent.mouseLocation()))))


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


MODIFIER_TAPS = set()  # modifier-only HotKeys, which never see a key the event tap swallows


class HotKey:
    """Watches key-downs for one shortcut. In another app it can't swallow the key, so that app sees it too.
    With `on_release`, it also reports when the key or one of its modifiers comes up, for hold shortcuts.
    `press` watches a keyed shortcut on the event tap instead, which sees keys macOS reserves, such as ⌘`, and
    swallows the shortcut's key so the frontmost app doesn't act on it too."""

    def __init__(self, spec, on_hotkey, on_release=None, *, on_activity=None, on_cancel=None, press=False):
        self.keys, self.on_hotkey = hotkeys.parse(spec), on_hotkey  # parsed, so modifier order doesn't matter
        self.on_release, self._tap, self._spoiled = on_release, False, False
        self._swallowed_at = self._since = float("-inf")  # NSEvent timestamps
        self._hold, self._closed = None, False
        if on_release is not None or press:
            options = {"on_activity": on_activity} if on_activity is not None else {}
            if on_cancel is not None:
                options["on_cancel"] = on_cancel
            self._hold = NativeHold(spec, on_hotkey, on_release or (lambda: None), press=press, **options)
            return
        mask = AppKit.NSEventMaskKeyDown
        if self.keys[1] is None:
            mask |= AppKit.NSEventMaskKeyUp | AppKit.NSEventMaskFlagsChanged
            MODIFIER_TAPS.add(self)
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
            now = event.timestamp()
            if self._tap and not self._since < self._swallowed_at < now:
                self.on_hotkey()
            self._tap = self._spoiled = False
            self._since = now
        elif mods == chord and not self._spoiled:
            self._tap = True
        else:  # an extra modifier joined, so this hold is no longer a tap of the chord
            self._tap, self._spoiled = False, True

    def swallowed(self, mods, at):
        """The event tap swallowed a key at `at`, on its own thread. Comparing event times, not arrival order,
        spoils the hold that spans `at` even while this monitor still has older modifier events queued."""
        if mods & self.keys[0]:
            self._swallowed_at = at

    def stop(self):
        if self._closed:
            return
        self._closed = True
        MODIFIER_TAPS.discard(self)
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

    def __init__(self, spec, on_down, on_up, *, on_activity=None, on_cancel=None, press=False):
        mods, key = hotkeys.parse(spec)
        primary = (next(code for code, name in KEY_CODES.items() if name == key)
                   if key in KEY_CODES.values() else int(key[4:]) if key else None)
        keymap = {code: name for code, (name, _) in MOD_CODES.items()}
        if primary is not None:
            keymap[primary] = primary
        self.tracker = HeldShortcut(mods, primary, keymap, on_down, on_up, press=press)
        self.on_activity, self.on_cancel = on_activity, on_cancel
        self._closed, self._swallowed = False, set()
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
            # Default preserves the existing Accessibility grant. Only the shortcut's own key is ever suppressed.
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
                    down = kind == Quartz.kCGEventKeyDown
                    self.tracker.event(code, code, down, repeat=bool(repeat) or mods != self.tracker.mods)
                    if self._swallow(event, code, down, bool(repeat), mods):
                        return None
            except Exception:
                self._loss()
            return event

    def _swallow(self, event, code, down, repeat, mods):
        """The shortcut's key, from a fresh press with exactly its modifiers through that key's own key-up."""
        if code != self.tracker.primary:
            return False
        if code in self._swallowed and (repeat or not down):
            if not down:
                self._swallowed.discard(code)
            return True
        if not down or repeat or mods != self.tracker.mods:
            self._swallowed.discard(code)  # a fresh press after a missed key-up
            return False
        self._swallowed.add(code)
        taps = list(MODIFIER_TAPS)  # so releasing ⌃⌥D doesn't also fire a ⌃⌥ shortcut
        if taps:
            at = AppKit.NSEvent.eventWithCGEvent_(event).timestamp()  # the clock the monitors' events use
            for tap in taps:
                tap.swallowed(mods, at)
        return True

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
