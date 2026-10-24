"""App's ⏮ and ⏭, and the read shortcut."""
import threading

from . import sentences


def offset(text, units, index):
    """Where units[index] starts in text; units come from sentences.split, so each is found in order."""
    at = 0
    for unit in units[:index]:
        at = text.index(unit, at) + len(unit)
    return text.index(units[index], at) if index < len(units) else len(text)


class Skips:
    """Mixed into core.App, which owns popup, host, cfg and the read state."""

    def skip(self, delta):
        """⏮ (-1) and ⏭ (+1): read again from the previous or next sentence; past the last one stops."""
        if not self._read_options:
            return
        index = max(0, self._sentence + delta)
        if index >= len(self._sentences):
            self.stop()
            return
        self.play(self._read_text, self._read_options[0], start=index)
        self.popup.set_playing(True)
        self.popup.schedule_hide()  # cancels the hide timer that stop() set
        self.popup.set_loading(True)

    def _track_at(self, index, chars):
        """An engine's reported position: exact, so "Go. Go." is told apart, and text matching stops for this read."""
        from .core import PIECE_CHARS
        at = self._read_start + (sentences.index_at(self._units[0], chars) if index == 0 else 0)
        # later units pack fragments of the remaining sentences, so walk those rather than split the units again
        opening = len(sentences.split(self._units[0])) or 1
        frags = [(i, f) for i in range(self._read_start + opening, len(self._sentences))
                 for f in sentences._fit(self._sentences[i], PIECE_CHARS)]
        k = 0
        for n, unit in enumerate(self._units[1:index + 1], 1):
            pos = 0
            while k < len(frags) and (found := unit.find(frags[k][1], pos)) >= 0 and (n < index or found <= chars):
                at, pos, k = frags[k][0], found + len(frags[k][1]), k + 1
        self._sentence, self._tracked, self._by_position = min(at, len(self._sentences) - 1), True, True

    def _track(self, caption):
        """Moves the skip position to the sentence shown; a caption that matches none (Codex rewords) keeps it.

        After the first match the search starts past the current sentence, so "Go. Go." moves to the second one."""
        if self._by_position:
            return
        start = self._sentence + self._tracked
        if caption in self._sentences[start:]:
            self._sentence, self._tracked = self._sentences.index(caption, start), True

    def _hotkey(self):
        """A press stops a read, cancels a selection read still in flight, or starts one."""
        if self._selection_busy():
            return
        if self.popup.playing:
            self.popup.close()
        elif self._hotkey_wait:
            self._hotkey_wait = None
        else:
            wait = self._hotkey_wait = object()
            threading.Thread(target=self._hotkey_read, args=(wait, *self.host.pointer()), daemon=True).start()

    def _hotkey_read(self, wait, x, y):  # worker thread
        if wait is not self._hotkey_wait or self._selection_busy():
            return
        try:
            text = self.host.read_selection(self.cfg["clipboard_fallback"])
        except Exception:
            text = None
        self.events.put(("hotkey_read", wait, text, x, y))

    def _hotkey_shown(self, wait, text, x, y):
        if wait is not self._hotkey_wait or self._selection_busy():
            return  # cancelled by another press
        self._hotkey_wait = None
        if text and not self.popup.playing:
            self.popup.show(text, x, y)
            self.popup.play()
