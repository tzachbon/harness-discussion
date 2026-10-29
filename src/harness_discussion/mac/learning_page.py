"""Dictation vocabulary controls in the existing native Mac settings style."""
import AppKit

from .. import learning
from . import ui


class LearningSection:
    def _learning_card(self):
        from .settings_ui import WIDTH, _card, _note
        on, self.learning_switch = ui.option("Learn from dictation corrections",
                "Learns names and terms you correct in the same field for 30 seconds after dictation.",
                self.cfg.get("dictation_learn", True), lambda on: self._set("dictation_learn", on), WIDTH)
        self.words_text = AppKit.NSTextField.alloc().init()
        self.words_text.setUsesSingleLineMode_(False)
        self.words_text.cell().setWraps_(True)
        self.words_text.setAccessibilityLabel_("Dictation vocabulary, one word or short phrase per line")
        self.words_text.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        self.words_text.heightAnchor().constraintEqualToConstant_(70).setActive_(True)
        self.refresh_words()
        return _card([on, _note("Saved on this Mac. Cloud dictation or tidy can receive saved words. "
                     "One word or short phrase per line; remove a line to forget it."), self.words_text,
                     ui.stack([ui.button("Save words", self._save_words),
                               ui.button("Clear words", self._clear_words)], vertical=False)])

    def refresh_words(self, words=None):
        if words is not None:
            self.cfg["dictation_words"] = words
        if hasattr(self, "words_text"):
            self.words_text.setStringValue_("\n".join(self.cfg.get("dictation_words", []) if words is None else words))

    def _save_words(self):
        words = [w.strip() for w in str(self.words_text.stringValue()).splitlines() if w.strip()]
        if len(words) > learning.MAX_WORDS or any(not learning.normalize([w]) for w in words):
            self.app.host.notify("Use up to 200 names or short terms, up to 60 characters each, "
                                 "with letters, spaces, apostrophes or hyphens.")
            return
        self._set("dictation_words", learning.normalize(words))
        self.refresh_words()

    def _clear_words(self):
        self._set("dictation_words", [])
        self.refresh_words()

