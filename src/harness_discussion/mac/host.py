"""macOS host: AppKit on the main thread, a menu bar item, Accessibility for the selection."""
import os

import AppKit
import objc
from Foundation import NSObject

from .. import icons, settings, updates
from ..codex_rt import CodexEngine
from ..cursor_tts import CursorEngine
from ..kokoro_tts import KokoroEngine
from . import edit, selection, ui
from .esc import EscWatcher
from .popup import Popup, build_menu
from .review import Review
from .settings_ui import SettingsWindow
from .toast import Toast
from .trigger import HotKey, SelectionTrigger, top_left
from .voice import MacVoiceEngine


class _Delegate(NSObject):
    def initWithHost_(self, host):
        self = objc.super(_Delegate, self).init()
        if self is not None:
            self.host = host
        return self

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
        self.host.app.events.put(("settings",))  # opening the app again shows Settings
        return False

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return False

    def menuWillOpen_(self, menu):
        self.host.refresh_menu()


class MacHost:
    edit = edit  # in-place editing, which turns the Writer on

    def __init__(self):
        self.app = None  # set by core.App
        self.watcher = None  # set by mac.main
        self.trigger, self.hotkey, self._missing, self._error = None, None, False, None
        self.shortcuts = {}  # name -> HotKey for the writer's shortcuts
        self.ns = AppKit.NSApplication.sharedApplication()
        self.ns.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self.delegate = _Delegate.alloc().initWithHost_(self)
        self.ns.setDelegate_(self.delegate)
        self._mark = ui.pil_image(icons.menu_bar_image(36), 18)
        self.item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSVariableStatusItemLength)
        self.menu = AppKit.NSMenu.alloc().init()
        self.menu.setAutoenablesItems_(False)
        self.menu.setDelegate_(self.delegate)
        put = lambda *event: lambda _s: self.app.events.put(event)
        self.status_item = self._add("", None)
        self.pause_item = self._add("Pause", lambda _s: self.app.popup.playing and self.app.popup._toggle())
        self.stop_item = self._add("Stop", lambda _s: self.app.popup.playing and self.app.popup._stop())
        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self.speed_menu = self._submenu("Speed")
        self.source_menu = self._submenu("Voice Source")
        # one target per submenu: rebuilt items carry their value, so opening the menu adds no targets
        self._pick_speed = ui.target(lambda s: self.app.change("speed", float(s.representedObject())))
        self._pick_source = ui.target(lambda s: self.app._popup_source(str(s.representedObject())))
        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
        # off until turned on again or the app restarts, so a forgotten toggle can't keep it off
        self.listen_item = self._add("Show Popup on Selection", lambda _s: self.stop_trigger() if self.trigger
                                     else self.start_trigger(*self._trigger_args))
        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self._add("Settings…", put("settings"), ",")
        self.permission_item = self._add("Permission needed…", lambda _s: self.watcher and self.watcher.show_guide())
        self.error_item = self._add("", None)
        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self._add(f"Harness Discussion {updates.current_version()}", None)
        self._add("Quit Harness Discussion", put("quit"), "q")
        self.item.setMenu_(self.menu)
        self.refresh_menu()

    def _add(self, title, fn, key="", menu=None):
        item = (menu or self.menu).addItemWithTitle_action_keyEquivalent_(title, "run:" if fn else None, key)
        if fn:
            item.setTarget_(ui.target(fn))
        item.setEnabled_(fn is not None)
        return item

    def _submenu(self, title):
        menu = AppKit.NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        self.menu.setSubmenu_forItem_(menu, self.menu.addItemWithTitle_action_keyEquivalent_(title, None, ""))
        return menu

    def _fill(self, menu, choices, current, target):
        """choices: [(value, title)], None for a separator. Rebuilt on each open, as sign-ins change."""
        menu.removeAllItems()
        for choice in choices:
            if choice is None:
                menu.addItem_(AppKit.NSMenuItem.separatorItem())
                continue
            value, title = choice
            item = menu.addItemWithTitle_action_keyEquivalent_(title, "run:", "")
            item.setTarget_(target)
            item.setRepresentedObject_(value)
            item.setState_(1 if value == current else 0)

    def refresh_menu(self):
        image = (ui.symbol("exclamationmark.triangle.fill", "Harness Discussion") if self._missing
                 else self._mark)
        image.setTemplate_(not self._missing)
        self.item.button().setImage_(image)
        self.item.button().setToolTip_("Harness Discussion needs Accessibility permission" if self._missing
                                       else "Harness Discussion")
        self.permission_item.setHidden_(not self._missing)
        self.error_item.setHidden_(self._error is None)
        self.error_item.setTitle_(f"Last error: {self._error or ''}")
        app = self.app
        if app is None:  # the first call comes from __init__, before App exists
            return
        p = app.popup
        source = app.chain.active_name or app.chain.last or app.chain.order()[0].name  # the fallback speaking now
        self.status_item.setTitle_("Paused" if p.paused else f"Reading with {source}" if p.playing
                                   else "Ready: select text to read")
        self.pause_item.setTitle_("Resume" if p.paused else "Pause")
        self.pause_item.setHidden_(not p.playing)
        self.stop_item.setHidden_(not p.playing)
        self._fill(self.speed_menu, [(s, settings.speed_label(s)) for s in settings.SPEED_PRESETS],
                   app.cfg["speed"], self._pick_speed)
        choices = app._source_choices()
        ready = [(name, name) for name, fix in choices if not fix]
        fixes = [(name, f"{name}: {fix}") for name, fix in choices if fix]
        self._fill(self.source_menu, ready + ([None] if ready and fixes else []) + fixes,
                   app.chain.order()[0].name, self._pick_source)
        self.listen_item.setState_(1 if self.trigger else 0)

    def set_permission_missing(self, missing):
        self._missing = missing
        if missing:
            for shortcut in self.shortcuts.values():
                if shortcut.on_release is not None:
                    shortcut.stop()
        self.refresh_menu()
        win = self.app.settings_win
        if win and win.alive:
            win.refresh_permission()

    def engines(self):
        return [CursorEngine(), CodexEngine(), KokoroEngine(), MacVoiceEngine()]

    def make_popup(self, speed, **callbacks):
        return Popup(speed, **callbacks)

    def open_settings(self, welcome=False):
        return SettingsWindow(self.app, lambda: self.watcher and self.watcher.show_guide(), welcome)

    def read_selection(self, clipboard_fallback):
        return selection.read(clipboard_fallback)

    def dictation_target(self):
        """Freeze identity without Accessibility traversal or clipboard capture."""
        try:
            pid = edit._frontmost_pid()
            if not pid or pid == os.getpid():
                return None
            window = edit._window_number(pid)
            return (pid, window) if window else None
        except Exception:
            return None

    def start_trigger(self, on_selection, on_press):
        self._trigger_args = on_selection, on_press
        if self.trigger is None:
            self.trigger = SelectionTrigger(on_selection, on_press)
            self.trigger.start()

    def stop_trigger(self):
        if self.trigger:
            self.trigger.stop()
            self.trigger = None

    def set_hotkey(self, spec, on_hotkey):
        """Replaces the read shortcut; "" turns it off."""
        if self.hotkey:
            self.hotkey.stop()
        self.hotkey = HotKey(spec, on_hotkey) if spec else None

    def set_shortcut(self, name, spec, fn, on_release=None, *, on_activity=None, on_cancel=None):
        """Replaces one of the writer's shortcuts; "" turns it off. on_release makes it a hold shortcut."""
        old = self.shortcuts.pop(name, None)
        if old:
            old.stop()
        if spec:
            options = {"on_activity": on_activity} if on_activity is not None else {}
            if on_cancel is not None:
                options["on_cancel"] = on_cancel
            self.shortcuts[name] = HotKey(spec, fn, on_release, **options)

    def mic_permission(self, then):
        """Calls then(granted) on the main thread, asking the first time."""
        import AVFoundation
        device, media = AVFoundation.AVCaptureDevice, AVFoundation.AVMediaTypeAudio
        status = device.authorizationStatusForMediaType_(media)
        if status == AVFoundation.AVAuthorizationStatusNotDetermined:
            device.requestAccessForMediaType_completionHandler_(
                media, lambda granted: self.app.events.put(("call", lambda: then(bool(granted)))))
        else:
            then(status == AVFoundation.AVAuthorizationStatusAuthorized)

    def mic_status(self):
        import AVFoundation
        status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeAudio)
        return {AVFoundation.AVAuthorizationStatusAuthorized: "ready",
                AVFoundation.AVAuthorizationStatusNotDetermined: "not_asked"}.get(status, "denied")

    def open_mic_settings(self):
        AppKit.NSWorkspace.sharedWorkspace().openURL_(AppKit.NSURL.URLWithString_(
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"))

    def make_toast(self):
        return Toast()

    def make_review(self):
        return Review()

    def esc_watcher(self, fn):
        return EscWatcher(fn)

    def copy_text(self, text, *, cancelled=lambda: False):
        if cancelled():
            return False
        board = AppKit.NSPasteboard.generalPasteboard()
        if cancelled():
            return False
        board.clearContents()
        return bool(board.setString_forType_(text, AppKit.NSPasteboardTypeString))

    def pick_mode(self, names, current, pick):
        """The mode menu at the pointer, for a double press of the rewrite shortcut."""
        menu = build_menu([(n, lambda n=n: pick(n), n == current) for n in names])
        menu.popUpMenuPositioningItem_atLocation_inView_(None, AppKit.NSEvent.mouseLocation(), None)

    def pointer(self):
        return top_left(AppKit.NSEvent.mouseLocation())

    def after(self, ms, fn):
        ui.timer(ms / 1000, fn)

    def notify(self, message):
        self._error = message[:80]
        self.refresh_menu()

    def confirm(self, title, message):
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.addButtonWithTitle_("Download")
        alert.addButtonWithTitle_("Cancel")
        self.ns.activateIgnoringOtherApps_(True)  # an accessory app's alert opens behind the active app otherwise
        return alert.runModal() == AppKit.NSAlertFirstButtonReturn

    def run(self, pump):
        def tick():
            pump()
            if self.app._closing:
                timer.invalidate()
        timer = ui.timer(0.03, tick, repeats=True)
        self.ns.run()

    def quit(self):
        AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(self.item)
        self.ns.terminate_(None)
