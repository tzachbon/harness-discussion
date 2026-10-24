"""Windows host: Tk on the main thread, pystray in the tray, UI Automation for the selection."""
import ctypes
import os
import threading
from ctypes import wintypes
import tkinter as tk
from tkinter import messagebox

import pystray

from ..codex_rt import CodexEngine
from ..cursor_tts import CursorEngine
from ..engines import EdgeEngine
from ..icons import icon_image
from ..kokoro_tts import KokoroEngine
from . import clipboard, edit, microphone, selection, uia_thread
from .popup import Popup
from .marks import Marks
from .review import Review
from .settings_ui import SettingsWindow
from .toast import Toast
from .trigger import Escape, HotKey, SelectionTrigger
from .voice import SapiEngine


class TkHost:
    edit = edit

    def __init__(self):
        self.app = None  # set by core.App
        self.root = tk.Tk()
        self.root.withdraw()
        self.trigger = self.hotkey = None
        self.shortcuts = {}
        self._writer_esc, self._cancel_writer = [], None
        self._mode_menu = self._mode_choice = None
        self.icon = pystray.Icon("harness-discussion", icon_image(), "Harness Discussion", self._menu())

    def _menu(self):
        put = lambda *event: lambda: self.app.events.put(event)
        return pystray.Menu(
            pystray.MenuItem("Settings", put("settings"), default=True),
            pystray.MenuItem(lambda _: f"Last voice source: {self.app.chain.last or '-'}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", put("quit")))

    def engines(self):
        return [CursorEngine(), CodexEngine(), KokoroEngine(), EdgeEngine(), SapiEngine()]

    def make_popup(self, speed, **callbacks):
        if on_escape := callbacks.get("on_escape"):
            callbacks["on_escape"] = lambda: self.app.events.put(("call", on_escape))
        return Popup(self.root, speed, **callbacks)

    def esc_watcher(self, on_escape):
        watcher = Escape(lambda: self.app.events.put(("call", on_escape)),
                         suppress=lambda: bool(self.app.rewriting), autostart=False)
        self._writer_esc.append(watcher)
        self._cancel_writer = on_escape
        return watcher

    def make_toast(self):
        return Toast(self.root)

    def make_review(self):
        return Review(self.root)

    def make_marks(self):
        return Marks(self.root)

    def pick_mode(self, names, current, pick):
        if self._mode_menu:
            self._mode_menu.destroy()
        menu = self._mode_menu = tk.Menu(self.root, tearoff=0)
        self._mode_choice = tk.StringVar(master=self.root, value=current)
        for name in names:
            menu.add_radiobutton(label=name, value=name, variable=self._mode_choice,
                                 command=lambda name=name: pick(name))
        menu.post(*self.pointer())

    def copy_text(self, text):
        try:
            if not isinstance(text, str) or "\0" in text:
                raise ValueError("Invalid clipboard text")
            clipboard.write_text(text)
            return True
        except (OSError, ValueError):
            self.notify("Could not copy the result.")
            return False

    def open_settings(self, welcome=False):
        a = self.app
        return SettingsWindow(self.root, dict(a.cfg), icon_image(256), a.change, a.test_voice,
                              lambda: a.stt_error, a.update_state, a.check_update, engines=a.chain.engines,
                              statuses=lambda: a.statuses,
                              on_play=lambda name: a.play(f"This is {name}.", None, only=name), welcome=welcome,
                              on_fix=a.fix_source, summarizer=a,
                              writer_catalogs=lambda: a.writer_models, writer_statuses=lambda: a.writer_statuses,
                              on_writer_fix=a.fix_writer,
                              stt_statuses=lambda: a.stt_statuses,
                              dictate_status=self.mic_status, on_mic_settings=self.open_mic_settings,
                              on_record=self._record_shortcuts)

    def mic_status(self):
        return microphone.status()

    def mic_permission(self, then):
        # Desktop apps use Windows' shared privacy switch, without an app permission prompt.
        then(self.mic_status() == "Available")

    def open_mic_settings(self):
        try:
            os.startfile("ms-settings:privacy-microphone")
        except OSError:
            self.notify("Could not open Windows microphone settings.")

    def _record_shortcuts(self, recording):
        if recording and self._cancel_writer:
            self._cancel_writer()
        for key in ("hotkey", "rewrite_hotkey", "dictate_hotkey"):
            self.app.apply(key, "" if recording else self.app.cfg[key])

    def read_selection(self, clipboard_fallback):
        return selection.read(clipboard_fallback)

    def start_trigger(self, on_selection, on_press):
        self.trigger = SelectionTrigger(on_selection, on_press)
        self.trigger.start()

    def stop_trigger(self):
        if self.trigger:
            self.trigger.stop()

    def set_hotkey(self, spec, on_hotkey):
        """Replaces the read shortcut; "" turns it off."""
        if self.hotkey:
            self.hotkey.stop()
        self.hotkey = HotKey(spec, on_hotkey) if spec else None

    def set_shortcut(self, name, spec, on_hotkey, on_release=None):
        old = self.shortcuts.pop(name, None)
        if old:
            old.stop()
        if spec:
            self.shortcuts[name] = HotKey(spec, on_hotkey, on_release)

    def pointer(self):
        point = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    def after(self, ms, fn):
        self.root.after(ms, fn)

    def notify(self, message):
        self.icon.notify(message, "Harness Discussion")

    def confirm(self, title, message):
        return messagebox.askokcancel(title, message, icon="question", parent=self.root)

    def run(self, pump):
        def tick():
            pump()
            if not self.app._closing:
                self.root.after(30, tick)
        threading.Thread(target=self.icon.run, daemon=True).start()
        self.root.after(30, tick)
        self.root.mainloop()

    def quit(self):
        self.stop_trigger()
        self.set_hotkey("", None)
        for name in tuple(self.shortcuts):
            self.set_shortcut(name, "", None)
        for watcher in self._writer_esc:
            watcher.stop()
        self._writer_esc.clear()
        self.icon.stop()
        self.root.destroy()
        uia_thread.close()
