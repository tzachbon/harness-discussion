"""Serialized hold lifecycle shared by the native keyboard listeners."""
import threading
import time


class HeldShortcut:
    INTERVAL = .05

    def __init__(self, mods, primary, keymap, on_down, on_up, settle=0, press=False):
        """press: a press shortcut rearms once its key comes up, so tapping it twice with the modifiers held fires
        twice. A hold rearms only after every key of the chord is up."""
        self.mods, self.primary, self.keymap = frozenset(mods), primary, keymap
        self.on_down, self.on_up, self.settle, self.press = on_down, on_up, settle, press
        self.lock = threading.RLock()
        self.held, self._changed = {}, {}
        self.active, self._armed, self._blocked, self._closed = False, True, False, False
        self._stopped = threading.Event()

    def _required(self, name):
        return name in self.mods or (self.primary is not None and name == self.primary)

    def seed(self, held):
        """Binding during a chord must wait for a full reset, including key-only autorepeat."""
        with self.lock:
            if not self._closed:
                self.held = dict(held)
                self._armed = not any(self._required(name) for name in self.held.values())

    def _released(self):
        names = set(self.held.values())
        if self.active and (not names >= self.mods or
                            (self.primary is not None and self.primary not in names)):
            self._end()
        if not self._blocked and (not any(self._required(name) for name in names)
                                  or self.press and self.primary is not None and self.primary not in names):
            self._armed = True

    def _end(self):
        if self.active:
            self.active = False
            self.on_up()

    def event(self, physical, name, down, repeat=False):
        with self.lock:
            if self._closed:
                return
            was_down = physical in self.held
            self._changed[physical] = time.monotonic()
            if not down:
                self.held.pop(physical, None)
                self._released()
                return
            self.held[physical] = name
            if was_down or repeat or not self._armed or self._blocked or not self._required(name):
                return
            names = set(self.held.values())
            modifiers = names & {"ctrl", "alt", "shift", "cmd"}
            matches = (modifiers >= self.mods if self.primary is None else
                       modifiers == self.mods and self.primary in names)
            if matches:
                self.active, self._armed = True, False
                self.on_down()

    def reconcile(self, is_down):
        """Only remove observed keys. Physical polling can never activate a chord."""
        with self.lock:
            if self._closed:
                return
            now = time.monotonic()

            def down(code):
                # A Windows low-level hook runs before GetAsyncKeyState reflects its event.
                if now - self._changed.get(code, float("-inf")) < self.settle:
                    return code in self.held
                return is_down(code)

            for code in list(self.held):
                if not down(code):
                    self.held.pop(code, None)
            if self._blocked and not any(down(code) for code, name in self.keymap.items()
                                         if self._required(name)):
                self._blocked = False
            self._released()

    def interrupt(self):
        with self.lock:
            if not self._closed:
                self._armed, self._blocked = False, True
                self._end()

    def start_polling(self, is_down):
        def poll():
            while not self._stopped.wait(self.INTERVAL):
                with self.lock:
                    if not self._closed and (self.held or self._blocked):
                        try:
                            self.reconcile(is_down)
                        except Exception:
                            self.interrupt()

        threading.Thread(target=poll, daemon=True, name="held-shortcut").start()

    def stop(self):
        with self.lock:
            if not self._closed:
                self._closed = True
                self._stopped.set()
                self._end()
