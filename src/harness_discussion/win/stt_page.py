"""Build the Windows speech-to-text server controls."""
import tkinter as tk
from tkinter import ttk

from .sources_ui import CARD


def build_page(window, page):
    card = window._card(page, "OpenAI-compatible server")
    window.stt_on = tk.BooleanVar(window.win, window.cfg.get("stt_server", False))
    ttk.Checkbutton(card, text="Run the speech-to-text server for other apps, such as OpenWhispr",
                    style="Card.TCheckbutton", variable=window.stt_on,
                    command=window._stt_toggled).pack(anchor="w", pady=(0, 6))
    window.stt_values = {name: tk.StringVar(window.win) for name in ("Server URL", "Model")}
    window.stt_copy_buttons = []
    for name, value in window.stt_values.items():
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=name, style="Card.TLabel", width=10).pack(side="left")
        ttk.Entry(row, textvariable=value, state="readonly", width=34).pack(side="left", fill="x", expand=True, padx=8)
        button = ttk.Button(row, text="Copy", command=lambda name=name: window._copy_stt(name))
        button.pack(side="right")
        window.stt_copy_buttons.append(button)
    ttk.Label(card, text="Paste into OpenWhispr → Self-Hosted. No API key needed. The URL stays\n"
              "the same after restarts. Transcription only: JSON or text, up to 60 seconds.",
              style="Sub.Card.TLabel").pack(anchor="w", pady=(6, 0))
    row = tk.Frame(card, bg=CARD)
    row.pack(fill="x", pady=(6, 0))
    window.stt_status = ttk.Label(row, style="Sub.Card.TLabel", wraplength=335)
    window.stt_status.pack(side="left", fill="x", expand=True)
    ttk.Button(row, text="Refresh", command=window._refresh_stt).pack(side="right")
