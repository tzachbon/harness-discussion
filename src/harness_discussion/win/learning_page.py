"""Dictation vocabulary controls in the existing native Windows settings style."""
import tkinter as tk
from tkinter import messagebox, ttk

from .. import learning
from .sources_ui import CARD


class LearningSection:
    def _learning_section(self, page):
        card = self._card(page, "Learned words")
        self.learning_on = tk.BooleanVar(self.win, self.cfg.get("dictation_learn", True))
        self.learning_check = ttk.Checkbutton(card, text="Learn from dictation corrections", style="Card.TCheckbutton",
                        variable=self.learning_on,
                        command=lambda: self._set("dictation_learn", self.learning_on.get()))
        self.learning_check.pack(anchor="w")
        ttk.Label(card, text="Learns names and terms you correct in the same field for 30 seconds after "
                  "dictation. Saved on this PC. Cloud dictation or tidy can receive saved words. "
                  "One word or short phrase per line; remove a line to forget it.",
                  style="Sub.Card.TLabel", wraplength=440).pack(anchor="w", pady=(4, 6))
        self.words_text = tk.Text(card, height=4, width=42, wrap="word", name="dictation_words")
        self.words_text.pack(fill="x")
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="Save words", command=self._save_words).pack(side="left")
        ttk.Button(row, text="Clear words", command=self._clear_words).pack(side="right")
        self.refresh_words()

    def refresh_words(self, words=None):
        if words is not None:
            self.cfg["dictation_words"] = words
        if hasattr(self, "words_text"):
            self.words_text.delete("1.0", "end")
            self.words_text.insert("1.0", "\n".join(self.cfg.get("dictation_words", [])))

    def _save_words(self):
        words = [w.strip() for w in self.words_text.get("1.0", "end").splitlines() if w.strip()]
        if len(words) > learning.MAX_WORDS or any(not learning.normalize([w]) for w in words):
            messagebox.showerror("Learned words", "Use up to 200 names or short terms, up to 60 characters "
                                 "each, with letters, spaces, apostrophes or hyphens.", parent=self.win)
            return
        self._set("dictation_words", learning.normalize(words))
        self.refresh_words()

    def _clear_words(self):
        self._set("dictation_words", [])
        self.refresh_words()

