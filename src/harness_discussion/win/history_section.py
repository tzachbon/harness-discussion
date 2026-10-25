"""Settings → Dictation → Recent dictations on Windows: the switch and the saved list with Copy."""
import tkinter as tk
from tkinter import ttk

from .. import history
from .sources_ui import CARD

class HistorySection:
    """Mixed into SettingsWindow, which owns win, cfg, _card, _set, on_copy and on_history_changed."""

    def _history_section(self, page):
        card = self._card(page, "Recent dictations")
        self.history_on = tk.BooleanVar(self.win, self.cfg.get("dictation_history", True))
        ttk.Checkbutton(card, text="Keep recent dictations", style="Card.TCheckbutton", variable=self.history_on,
                        command=self._history_toggled).pack(anchor="w")
        ttk.Label(card, text="The last 10, raw and tidied, below and under Recent Dictations in the tray menu. "
                  "Off deletes them.", style="Sub.Card.TLabel", wraplength=440).pack(anchor="w", pady=(4, 0))
        self.history_card = tk.Frame(card, bg=CARD)
        self.history_card.pack(fill="x", pady=(10, 0))
        self.refresh_history()

    def _history_toggled(self):
        on = self.history_on.get()
        self._set("dictation_history", on)
        if not on:
            history.clear()  # off deletes what was saved
        self.on_history_changed()

    def refresh_history(self):
        """Each saved dictation with Copy, and Original when tidy changed it."""
        for child in self.history_card.winfo_children():
            child.destroy()
        rows = history.rows() if self.cfg.get("dictation_history", True) else []
        for preview, text, original in rows:
            row = tk.Frame(self.history_card, bg=CARD)
            row.pack(fill="x", pady=(0, 6))
            ttk.Label(row, text=preview, style="Card.TLabel", width=40).pack(side="left")
            if original is not None:
                ttk.Button(row, text="Original", command=lambda t=original: self.on_copy(t)).pack(side="right")
            ttk.Button(row, text="Copy", command=lambda t=text: self.on_copy(t)).pack(side="right", padx=(6, 6))
        if rows:
            ttk.Button(self.history_card, text="Clear", command=self._clear_history).pack(anchor="e", pady=(6, 0))
        else:
            ttk.Label(self.history_card, text="No dictations yet." if self.cfg.get("dictation_history", True)
                      else "Turn on Keep recent dictations to save them here.",
                      style="Sub.Card.TLabel").pack(anchor="w")

    def _clear_history(self):
        history.clear()
        self.on_history_changed()
