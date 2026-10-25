"""Settings shortcut rows (read, rewrite, dictate), with Change… recording the next key, alone or with modifiers."""
import ctypes
import tkinter as tk
from tkinter import ttk

from .. import hotkeys
from .sources_ui import CARD
from .trigger import MOD_VKS, VK

MOD_NAMES = {vk: name for name, codes in {"shift": (0x10, 0xA0, 0xA1), "ctrl": (0x11, 0xA2, 0xA3),
                                         "alt": (0x12, 0xA4, 0xA5), "cmd": (0x5B, 0x5C)}.items() for vk in codes}
MOD_SIDES = {0x10: (0xA0, 0xA1), 0x11: (0xA2, 0xA3), 0x12: (0xA4, 0xA5)}
TK_MODS = {"shift": 0x1, "ctrl": 0x4, "alt": 0x20000}  # Tk's event.state bits on Windows
KEYS = {vk: name for name, vk in VK.items()}  # Tk's keycode is the virtual key, the same under any layout
TITLES = {"hotkey": "Read shortcut", "rewrite_hotkey": "Rewrite shortcut", "dictate_hotkey": "Hold to dictate"}


def win_down():  # Tk's event.state has no bit for the Windows key
    return any(ctypes.windll.user32.GetKeyState(vk) & 0x8000 for vk in (0x5B, 0x5C))


class ShortcutRow:
    """Mixed into SettingsWindow, which owns win, cfg, on_record and _set."""

    def _shortcut_row(self, card, key="hotkey",
                      note="Reads the selected text without clicking the bar. Pressing it again stops the read."):
        if not hasattr(self, "shortcuts"):
            self.shortcuts = {}
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(12, 0))
        ttk.Label(row, text=TITLES[key], style="Card.TLabel").pack(side="left")
        widgets = self.shortcuts[key] = {
            "off": ttk.Button(row, text="Off", command=lambda: self._set_hotkey("", key)),
            "change": ttk.Button(row, text="Change…", command=lambda: self._record_hotkey(key)),
            "label": ttk.Label(row, text=hotkeys.label(self.cfg[key]), style="Card.TLabel")}
        widgets["off"].pack(side="right")
        widgets["change"].pack(side="right", padx=(0, 8))
        widgets["label"].pack(side="right", padx=(0, 8))
        widgets["error"] = ttk.Label(card, style="Sub.Card.TLabel", foreground="#c42b1c")
        widgets["error"].pack(anchor="w")
        if key == "hotkey":
            self.hotkey_lbl = widgets["label"]
        ttk.Label(card, text=note, style="Sub.Card.TLabel", wraplength=440).pack(anchor="w", pady=(4, 0))

    def _record_hotkey(self, key="hotkey"):
        """Change…: the next key, alone or with modifiers, becomes the shortcut; Esc keeps the old one."""
        self._stop_recording()
        self._recording = key
        self._held_mods, self._peak_mods = {}, set()
        self.shortcuts[key]["label"]["text"] = "Press keys…"
        self.shortcuts[key]["error"]["text"] = ""
        self.on_record(True)  # so pressing a current shortcut records it instead of acting on it
        self.win.focus_force()
        self.win.bind("<KeyPress>", self._hotkey_key)
        self.win.bind("<KeyRelease>", self._hotkey_release)

    def _hotkey_key(self, event):
        if event.keysym == "Escape":
            self._set_hotkey(self.cfg[self._recording], self._recording)
        elif event.keycode in MOD_NAMES:
            self._held_mods[event.keycode] = MOD_NAMES[event.keycode]
            mods = set(self._held_mods.values())
            if len(mods) >= len(self._peak_mods):
                self._peak_mods = mods
            self.shortcuts[self._recording]["label"]["text"] = hotkeys.label(hotkeys.spec(mods)) + "…"
        elif event.keycode not in MOD_VKS:  # a held modifier waits for the key
            key = KEYS.get(event.keycode) or f"key_{event.keycode}"
            mods = set(self._held_mods.values()) | {m for m, bit in TK_MODS.items() if event.state & bit}
            if win_down():
                mods.add("cmd")
            self._set_hotkey(hotkeys.spec(mods, key), self._recording)
        return "break"

    def _hotkey_release(self, event):
        # Tk uses one keycode for both physical sides of Ctrl, Shift and Alt.
        if not any(ctypes.windll.user32.GetKeyState(vk) & 0x8000 for vk in MOD_SIDES.get(event.keycode, ())):
            self._held_mods.pop(event.keycode, None)
        if event.keycode in MOD_NAMES and not self._held_mods and self._peak_mods:
            self._set_hotkey(hotkeys.spec(self._peak_mods), self._recording)
        return "break"

    def _stop_recording(self):
        self._recording = None
        self.win.unbind("<KeyPress>")
        self.win.unbind("<KeyRelease>")
        for key, widgets in self.shortcuts.items():
            widgets["label"]["text"] = hotkeys.label(self.cfg[key])

    def _set_hotkey(self, spec, key="hotkey"):
        self._stop_recording()
        used = hotkeys.conflict(self.cfg, key, spec)
        if used:  # two shortcuts on one chord would both fire
            self.shortcuts[key]["error"]["text"] = f"{TITLES[used]} already uses {hotkeys.label(spec)}."
            spec = self.cfg[key]
        if spec != self.cfg[key]:
            self._set(key, spec)
        for k, widgets in self.shortcuts.items():  # also ends a recording another row started
            widgets["label"]["text"] = hotkeys.label(self.cfg[k])
        self.on_record(False)
