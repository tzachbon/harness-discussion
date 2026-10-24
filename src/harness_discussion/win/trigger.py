"""Turns global mouse input into "text may have just been selected here" events."""
import ctypes
import threading
import time

from pynput import keyboard, mouse

from .. import hotkeys
from ..clicks import DRAG_PX, Clicks  # noqa: F401  DRAG_PX is part of this module's surface
from ..held_shortcut import HeldShortcut

MULTI_CLICK_S = ctypes.windll.user32.GetDoubleClickTime() / 1000


class SelectionTrigger:
    """Callbacks run on the hook thread, so they must only enqueue work, never block."""

    def __init__(self, on_selection, on_press):
        self.on_selection, self.on_press = on_selection, on_press
        self._clicks = Clicks(MULTI_CLICK_S)
        self._listener = mouse.Listener(on_click=self._click)

    def start(self):
        self._listener.start()

    def stop(self):
        self._listener.stop()

    def _click(self, x, y, button, pressed):
        if pressed:
            if button == mouse.Button.left:
                self._clicks.down(x, y)
            self.on_press(x, y)
            return
        if button != mouse.Button.left:
            return
        if self._clicks.up(x, y, time.monotonic()):
            self.on_selection(x, y)


# Virtual-key codes, so the shortcut follows the physical key under any keyboard layout (Hebrew, Russian...)
VK = {**{k: ord(k.upper()) for k in "abcdefghijklmnopqrstuvwxyz0123456789"}, **{f"f{n}": 0x6F + n for n in range(1, 13)}}
MOD_VKS = {0x10, 0x11, 0x12, 0x14, 0x5B, 0x5C, 0x90, *range(0xA0, 0xA6)}  # modifiers, Caps Lock and Num Lock
# extended keys GetKeyNameText misnames without the extended bit: Left would read as Num 4
EXTENDED_VKS = {*range(0x21, 0x29), 0x2C, 0x2D, 0x2E, 0x5D, 0x6F}
MOD_KEYS = {"ctrl": (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r),
            "alt": (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr),
            "shift": (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r),
            "cmd": (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r)}


def vk(name):
    """The virtual key for a stored key name: letters, digits and F1-F12 by name, any other key as key_N."""
    return VK.get(name) or int(name[4:])


def key_name(code):
    """What the current keyboard layout calls a virtual key, such as "Space" or "`"."""
    if code == 0x13:  # Pause's E1 scan prefix reads as Right Ctrl to GetKeyNameText
        return "Pause"
    user32 = ctypes.windll.user32
    scan = user32.MapVirtualKeyW(code, 4)  # MAPVK_VK_TO_VSC_EX: an E0 prefix marks an extended key, but not always
    buf = ctypes.create_unicode_buffer(64)
    if user32.GetKeyNameTextW((scan & 0xFF) << 16 | (1 << 24 if scan >> 8 or code in EXTENDED_VKS else 0), buf, 64):
        return buf.value
    return f"Key {code}"


def _name(key):
    """A modifier's name, or the key's virtual-key code (function keys are Key members holding a KeyCode)."""
    for name, keys in MOD_KEYS.items():
        if key in keys:
            return name
    return getattr(key, "vk", None) or getattr(getattr(key, "value", None), "vk", None)


def _physical(key):
    return getattr(key, "vk", None) or getattr(getattr(key, "value", None), "vk", None) or key


class HotKey:
    """Fires once every key of the shortcut is up, so a borrowed Ctrl+C isn't sent as Ctrl+Alt+C.
    With on_release, fires at key-down instead and ends on primary up or required modifier loss,
    for hold-to-act shortcuts like dictation."""

    def __init__(self, spec, on_hotkey, on_release=None, *, on_activity=None):
        mods, key = hotkeys.parse(spec)
        self.on_hotkey, self.on_release, self._keys, self._held = on_hotkey, on_release, {*mods, *([vk(key)] if key else [])}, {}
        self._pending = False
        self._lock, self._closed, self._hold = threading.RLock(), False, None
        self.on_activity = on_activity
        if on_release is not None:
            keymap = {0xA0: "shift", 0xA1: "shift", 0xA2: "ctrl", 0xA3: "ctrl",
                      0xA4: "alt", 0xA5: "alt", 0x5B: "cmd", 0x5C: "cmd"}
            primary = vk(key) if key else None
            if primary is not None:
                keymap[primary] = primary
            self._hold = HeldShortcut(mods, primary, keymap, on_hotkey, on_release, settle=.05)
            self._lock = self._hold.lock
            self._hold.seed({code: name for code, name in keymap.items() if self._key_down(code)})
        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
        try:
            self._listener.start()
            if self._hold:
                self._hold.start_polling(self._key_down)
        except BaseException:
            self.stop()
            raise

    @staticmethod
    def _key_down(code):
        return bool(ctypes.windll.user32.GetAsyncKeyState(code) & 0x8000)

    def _press(self, key):
        with self._lock:
            if self._closed:
                return
            if self._hold:
                name = _name(key)
                if self.on_activity is not None and not self._hold._required(name):
                    self.on_activity()
                self._hold.event(_physical(key), name, True)
            else:
                self._held[_physical(key)] = _name(key)
                if set(self._held.values()) == self._keys:
                    self._pending = True

    def _release(self, key):
        with self._lock:
            if self._closed:
                return
            if self._hold:
                name = _name(key)
                if self.on_activity is not None and name in MOD_KEYS and not self._hold._required(name):
                    self.on_activity()
                self._hold.event(_physical(key), name, False)
            else:
                self._held.pop(_physical(key), None)
                if not self._held and self._pending:
                    self._pending = False
                    self.on_hotkey()

    def stop(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                if self._hold:
                    self._hold.stop()
            finally:
                self._listener.stop()


class Escape:
    """Temporary watcher. Callback only enqueues, suppress() is true for bar/rewrite activity."""
    def __init__(self, on_escape, suppress=lambda: True, autostart=True):
        self.on_escape, self.suppress = on_escape, suppress
        self._lock = threading.RLock()
        self._held, self._listener, self._generation = False, None, 0
        if autostart:
            self.start()

    @property
    def active(self):
        with self._lock:
            return self._listener is not None

    def start(self):
        with self._lock:
            if self._listener is not None:
                return
            self._generation += 1
            generation = self._generation
            self._held = False

            listener = None

            def event_filter(message, data):
                return self._filter(listener, generation, message, data)

            listener = keyboard.Listener(win32_event_filter=event_filter)
            self._listener = listener
            try:
                listener.start()
            except BaseException:
                self._listener = None
                self._generation += 1
                self._held = False
                try:
                    listener.stop()
                except BaseException:
                    pass
                raise

    def _filter(self, listener, generation, message, data):
        with self._lock:
            if self._listener is not listener or self._generation != generation:
                return False
            if data.vkCode != 27:
                return True
            if message in (0x100, 0x104):  # WM_KEYDOWN / WM_SYSKEYDOWN
                if not self._held:
                    self._held = True
                    self.on_escape()
            elif message in (0x101, 0x105):
                self._held = False
            if self._listener is not listener or self._generation != generation:
                return False
            suppress = self.suppress()
            if self._listener is not listener or self._generation != generation:
                return False
            if suppress:
                listener.suppress_event()
            return False

    def stop(self):
        with self._lock:
            listener = self._listener
            if listener is None:
                return
            self._listener = None
            self._generation += 1
            self._held = False
            listener.stop()
