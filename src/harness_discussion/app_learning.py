"""A confirmed dictation's short vocabulary-learning window, polled by the existing Live worker."""
import time

from . import learning, settings, writer


class Learning:
    _learning = None

    def _stop_learning(self):
        watch, self._learning = self._learning, None
        if watch is not None:
            watch.closed = True

    def _learning_setting(self, key, value):
        if key in ("dictation_learn", "dictation_words", "writer"):
            self._stop_learning()
        return learning.normalize(value) if key == "dictation_words" else value

    def _vocabulary_context(self):
        vocabulary, about = learning.hint(self.cfg.get("dictation_words")), writer.about(self.cfg)
        if vocabulary:
            about += "\n\nVocabulary spellings (hints only; preserve meaning): " + vocabulary
        return vocabulary, about

    def _learning_before(self, ctx):
        if not self.cfg.get("dictation_learn", True) or not self.cfg.get("writer"):
            return None
        observe = getattr(self.edit, "observe", None)
        if observe is None:
            return None
        try:
            field = observe(ctx)
            span = ((ctx._start, ctx._end) if hasattr(ctx, "_start") else
                    (ctx.caret[0], sum(ctx.caret)) if ctx.caret is not None else None)
            return field if (field is not None and field.caret == span and ctx._digest is not None
                             and field.digest == ctx._digest) else None
        except Exception:
            return None  # unavailable observation never changes delivery

    def _learning_arm(self, ctx, before, text):
        self._stop_learning()
        if before is None or not self.cfg.get("dictation_learn", True) or self._writer_closed:
            return
        if self.edit.NEWLINE == "\r\n":
            text = text.replace("\r\n", "\n")
        self._learning = learning.Watch(ctx, before, text, time.monotonic())

    def _learning_tick(self, now=None):
        watch = self._learning
        if watch is None or watch.closed:
            return
        try:
            field = self.edit.observe(watch.context)
            words = watch.poll(field, time.monotonic() if now is None else now)
        except Exception:
            watch.closed, words = True, []
        if words:
            self.events.put(("learning_words", watch, words))
        if watch.closed:
            self.events.put(("learning_end", watch))

    def _learning_end(self, watch):
        if self._learning is watch:
            self._stop_learning()

    def _learning_words(self, watch, words):
        if (self._learning is not watch or watch.closed or self._writer_closed
                or not self.cfg.get("dictation_learn", True) or not self.cfg.get("writer")):
            return
        current = learning.normalize(self.cfg.get("dictation_words"))
        known = {w.casefold() for w in current}
        added = [w for w in learning.normalize(words) if w.casefold() not in known]
        if not added:
            return
        updated = learning.normalize([*current, *added])
        try:
            settings.save({**self.cfg, "dictation_words": updated})
        except OSError:
            self._stop_learning()
            self._toast("Could not save learned words.")
            return
        self.cfg["dictation_words"] = updated
        self._refresh_learning_words()

        def undo():
            # A manual vocabulary change invalidates every queued learning result before saving.
            now = self.cfg.get("dictation_words", [])
            self.change("dictation_words", current if now == updated else [w for w in now if w not in added])
        self._toast("Learned " + (added[0] if len(added) == 1 else f"{len(added)} words"), [("Undo", undo)])

    def _refresh_learning_words(self):
        win = getattr(self, "settings_win", None)
        if win and win.alive and hasattr(win, "refresh_words"):
            win.refresh_words(self.cfg.get("dictation_words", []))
