"""Settings → General → About you on Windows: the note sent with every prompt, its switch, and its limit."""
import tkinter as tk
from tkinter import messagebox, ttk

from .. import writer

class AboutSection:
    """Mixed into SettingsWindow, which owns win, cfg, _card and _set."""

    def _about_section(self, page, text):
        card = self._card(page, "About you")
        self.about_on = tk.BooleanVar(self.win, self.cfg.get("about_me_on", True))
        ttk.Checkbutton(card, text="Add this and today's date to writing and summary prompts",
                        style="Card.TCheckbutton", variable=self.about_on,
                        command=lambda: self._set("about_me_on", self.about_on.get())).pack(anchor="w")
        self.about_text = tk.Text(card, height=3, width=40, wrap="word", font=(text, 10), relief="solid", bd=1)
        self.about_text.insert("1.0", self.cfg.get("about_me", ""))
        self.about_text.bind("<FocusOut>", lambda _e: self._about_changed())
        self.about_text.bind("<KeyRelease>", lambda _e: self._show_about_count())
        self.about_text.pack(fill="x", pady=(8, 0))
        self.about_count = ttk.Label(card, style="Sub.Card.TLabel")
        self.about_count.pack(anchor="e")
        ttk.Label(card, text="• Who you are and what you do\n• Your working days and hours\n• How you like to write\n"
                  "The date and time zone are added for you.",
                  style="Sub.Card.TLabel", wraplength=440).pack(anchor="w", pady=(4, 0))
        self.about_uncapped = tk.BooleanVar(self.win, self.cfg.get("about_me_uncapped", False))
        ttk.Checkbutton(card, text=f"Allow more than {writer.ABOUT_CHARS} characters (slower)",
                        style="Card.TCheckbutton", variable=self.about_uncapped,
                        command=self._about_uncapped_changed).pack(anchor="w", pady=(10, 0))
        self._show_about_count()

    def _show_about_count(self):
        n, limit = len(self.about_text.get("1.0", "end-1c").strip()), writer.about_limit(self.cfg)
        self.about_count.configure(text=f"{n} / {limit}" + (" · the rest is not sent" if n > limit else ""),
                                   foreground="#c42b1c" if n > limit else "#5f5f5f")

    def _about_uncapped_changed(self):
        on = self.about_uncapped.get()
        if on and not messagebox.askokcancel("Turn off the limit?", writer.ABOUT_WARNING, icon="warning",
                                             parent=self.win):
            self.about_uncapped.set(False)
            return
        self._set("about_me_uncapped", on)
        self._about_changed()
        self._show_about_count()

    def _about_changed(self):
        value = self.about_text.get("1.0", "end").strip()[:writer.about_limit(self.cfg)]
        if value != self.cfg.get("about_me", ""):
            self._set("about_me", value)
