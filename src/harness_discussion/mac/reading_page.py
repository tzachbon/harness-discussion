"""Settings → Reading: copying hidden selections, early speech, and the shortcut rows every page shares."""
import AppKit

from .. import hotkeys
from . import trigger, ui, window_keys

SHORTCUT_TITLES = {"hotkey": "Read shortcut", "rewrite_hotkey": "Rewrite shortcut",
                   "dictate_hotkey": "Dictation shortcut (hold)"}
WINDOW_HINT = ("With Stage Manager on, macOS also switches windows with this shortcut. To stop that, turn off "
               "“Move focus to next window” in System Settings → Keyboard → Keyboard Shortcuts → Keyboard.")


class ReadingPage:
    """Mixed into SettingsWindow, which owns cfg, _set and _recorder."""

    def _reading_page(self):
        from .settings_ui import WIDTH, _card, _divider, _note  # settings_ui imports this module
        hidden, _ = ui.option("Read apps that hide their selection",
                              "Copies the selection with ⌘C, then puts your clipboard back.",
                              self.cfg["clipboard_fallback"], lambda on: self._set("clipboard_fallback", on), WIDTH)
        early, _ = ui.option("Prepare speech when I select text",
                             "When Cursor reads first, sends the opening sentences to Cursor before you press "
                             "Play, so speech starts sooner.", self.cfg["prefetch"], lambda on: self._set("prefetch", on), WIDTH)
        return [_card([hidden, _divider(), early, _divider(), self._shortcut_row()]),
                _note("Reads the selected text without clicking the bar. Pressing it again stops the read.")]

    def _shortcut_row(self, key="hotkey"):
        from .settings_ui import WIDTH, _spacer
        if not hasattr(self, "shortcut_labels"):
            self.shortcut_labels, self.shortcut_hints = {}, {}
        label = self.shortcut_labels[key] = ui.Keys()
        row = ui.stack([ui.label(SHORTCUT_TITLES[key], 13), _spacer(), label.view,
                        ui.button("Change…", lambda: self._record_hotkey(key)),
                        ui.button("Off", lambda: self._set_hotkey("", key))], vertical=False, spacing=8)
        row.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        self.shortcut_hints[key] = ui.label(WINDOW_HINT, 11, secondary=True, width=WIDTH)
        self._show_shortcut(key)
        return ui.stack([row, self.shortcut_hints[key]], spacing=4)  # the stack drops the hint while it's hidden

    def _show_shortcut(self, key, text=None):
        if text:
            self.shortcut_labels[key].say(text)
        else:
            self.shortcut_labels[key].show(self.cfg[key])
        self.shortcut_hints[key].setHidden_(hotkeys.parse(self.cfg[key]) not in window_keys.window_switch_keys())

    @property
    def hotkey_label(self):
        return self.shortcut_labels["hotkey"]

    def _record_hotkey(self, key="hotkey"):
        """Change…: the next key, alone or with modifiers, becomes the shortcut; Esc keeps the old one."""
        if self._recorder:
            return
        self._recording_key = key
        self.shortcut_labels[key].say("Press keys…")
        for name in hotkeys.SHORTCUTS:  # so pressing a bound shortcut records it instead of running or swallowing it
            self.app.apply(name, "")
        peak = set()  # the most modifiers held at once; letting them all go with no key records them alone

        def on_key(event):
            if not self.win.isVisible():  # Settings closed while recording: let keys through again
                self._stop_recording()
                return event
            label = self.shortcut_labels[key]
            if event.type() == AppKit.NSEventTypeFlagsChanged:  # held modifiers show while the key is chosen
                mods = trigger.event_mods(event)
                if not mods and peak:
                    self._set_hotkey(hotkeys.spec(peak), key)
                    return event
                if len(mods) > len(peak):
                    peak.clear()
                    peak.update(mods)
                if mods:
                    label.show(hotkeys.spec(mods), "…")
                else:
                    label.say("Press keys…")
                return event
            if event.keyCode() == 53:  # Esc
                self._stop_recording()
            else:
                self._set_hotkey(trigger.event_spec(event), key)
            return None  # keys pressed while recording don't reach the window
        self._recorder = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskKeyDown | AppKit.NSEventMaskFlagsChanged, on_key)
        # closing Settings with its red button ends recording; the key monitor sees no keys once the app is inactive
        self._closed = AppKit.NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
            AppKit.NSWindowWillCloseNotification, self.win, None, lambda _n: self._stop_recording())

    def _stop_recording(self):
        if self._recorder:
            key = self._recording_key
            AppKit.NSEvent.removeMonitor_(self._recorder)
            self._recorder = None
            AppKit.NSNotificationCenter.defaultCenter().removeObserver_(self._closed)
            for name in hotkeys.SHORTCUTS:
                self.app.apply(name, self.cfg[name])
            self._show_shortcut(key)

    def _set_hotkey(self, spec, key="hotkey"):
        """Saves the shortcut unless another row uses it, in which case the row names that one."""
        taken = hotkeys.conflict(self.cfg, key, spec)
        if spec != self.cfg[key] and not taken:
            self._set(key, spec)
        self._stop_recording()
        self._show_shortcut(key, taken and f"Used by {SHORTCUT_TITLES[taken]}")
