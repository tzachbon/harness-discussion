"""Settings → Dictation → Recent dictations on Windows: the switch and the saved list with Copy."""
import tkinter as tk
from tkinter import ttk

from .. import history
from .sources_ui import CARD

class HistorySection:
    """Mixed into SettingsWindow, which owns win, cfg, _card, _set, on_copy and on_history_changed."""

    def _history_section(self, page):
        card = self._card(page, "Recent dictations")
        self.history_on = tk.BooleanVar(self.win, self.cfg["dictation_history"])
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

    def _clear_history(self):
        history.clear()
        self.on_history_changed()
