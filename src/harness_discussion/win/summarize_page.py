"""The Summarize page of the Windows Settings window: the local model's status, with Download, Cancel or Remove."""
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

from .. import summarize
from .sources_ui import CARD

# Summarize state -> (status, button title or None)
SUMMARY_STATES = {"missing": ("Not downloaded", "Download"), "downloading": ("Downloading {percent}% of {size}", "Cancel"),
                  "ready": ("Ready", "Remove"), "unsupported": ("Not available on this PC", None)}
NO_SUMMARIZER = SimpleNamespace(summarize_state=lambda: ("missing", None), summarize_action=lambda: None)


class SummarizePage:
    """Mixed into SettingsWindow. The summarizer is the app: summarize_state() and summarize_action()."""

    def _summarize_page(self, page, font, summarizer):
        from .settings_ui import PAGE, SUBTLE
        self.summarizer = summarizer or NO_SUMMARIZER
        card = self._card(page, "Local model")
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x")
        about = tk.Frame(row, bg=CARD)
        about.pack(side="left", fill="x", expand=True)
        ttk.Label(about, text=f"{summarize.MODEL_NAME}  ·  {summarize.SIZE_LABEL}  ·  runs on this PC",
                  style="Card.TLabel", font=(font, 10, "bold")).pack(anchor="w")
        self.summary_status = ttk.Label(about, style="Sub.Card.TLabel")
        self.summary_status.pack(anchor="w", pady=(2, 0))
        self.summary_bar = ttk.Progressbar(about, maximum=1.0, length=300)  # packed while downloading
        self.summary_button = ttk.Button(row, command=self.summarizer.summarize_action)
        self.summary_button.pack(side="right")
        ttk.Label(page, text="The ✦ button next to Play summarizes the selection on this PC, then reads the "
                  "summary with your voice sources. The text stays on this PC. The model stays loaded for 5 "
                  f"minutes after a summary. Remove frees the {summarize.SIZE_LABEL}.", background=PAGE,
                  foreground=SUBTLE, font=(font, 9), wraplength=470).pack(anchor="w", pady=(6, 0))
        self.refresh_summarize()

    def refresh_summarize(self):
        state, progress = self.summarizer.summarize_state()
        text, action = SUMMARY_STATES[state if progress is None else "downloading"]
        if progress is None:
            self.summary_bar.pack_forget()
        else:
            text = text.format(percent=int(progress * 100), size=summarize.download_size_label())
            self.summary_bar["value"] = progress
            self.summary_bar.pack(anchor="w", pady=(6, 0))
        self.summary_status["text"] = text
        if action:
            self.summary_button.configure(text=action)
            self.summary_button.pack(side="right")
        else:
            self.summary_button.pack_forget()
