"""Check as you type, the platform-free part: the line at the caret, UTF-16 offsets, and suggestions from cached
rewrites. A suggestion is derived from the current text on every poll, so it never points at text that changed.

Offsets are UTF-16 code units of the field's text as the platform reports it: Windows folds line breaks to "\\n",
macOS keeps the raw "\\r\\n" or "\\r", so all three end a line here.
"""
import re
import threading
from collections import OrderedDict, namedtuple

from . import writer

POLL_S, PAUSE_S = 0.5, 1.5
LIVE_MAX_DOC = 20_000  # ponytail: longer fields are not checked; reading them every poll costs too much
MAX_PARAGRAPH, MIN_WORDS = 2_000, 3
MAX_SUGGESTIONS = 40
CACHE_SIZE = 200

# key identifies a suggestion across polls: (line, offset in line, old, new)
Suggestion = namedtuple("Suggestion", "key start end old new")
_LAST_WORD, _FIRST_WORD = re.compile(r"\S+\s*$"), re.compile(r"^\s*\S+")
_BREAK = re.compile(r"\r\n|\r|\n")


def units(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def text_at(text: str, start: int, end: int) -> str:
    raw = text.encode("utf-16-le", "surrogatepass")
    return raw[start * 2:end * 2].decode("utf-16-le", "surrogatepass")


def lines(text):
    """(start, line) for each line, start in UTF-16 units."""
    at, last = 0, 0
    for m in _BREAK.finditer(text):
        line = text[last:m.start()]
        yield at, line
        at += units(line) + units(m.group())
        last = m.end()
    yield at, text[last:]


def paragraph_at(text: str, caret: int) -> tuple[int, int]:
    """[start, end) of the line holding `caret`."""
    for start, line in lines(text):
        end = start + units(line)
        if caret <= end:
            break
    return start, end


def checkable(line: str) -> bool:
    core = line.strip()
    return len(core.split()) >= MIN_WORDS and units(core) <= MAX_PARAGRAPH


class Cache:
    """Rewrites by (line, mode), least recently used dropped first. Memory only, shared by the poll and check
    threads."""

    def __init__(self, size=CACHE_SIZE):
        self.size, self._items, self._lock = size, OrderedDict(), threading.Lock()

    @staticmethod
    def _key(line, mode):
        return line, mode.name, mode.instructions

    def put(self, line, mode, rewrite):
        with self._lock:
            key = self._key(line, mode)
            self._items[key] = rewrite
            self._items.move_to_end(key)
            while len(self._items) > self.size:
                self._items.popitem(last=False)

    def get(self, line, mode):
        with self._lock:
            key = self._key(line, mode)
            if key not in self._items:
                return None
            self._items.move_to_end(key)
            return self._items[key]

    def has(self, line, mode):
        with self._lock:
            return self._key(line, mode) in self._items

    def clear(self):
        with self._lock:
            self._items.clear()


def changes(core: str, new: str) -> list[tuple[int, str, str]]:
    """(offset in core, old, new) per change. Changes split only by spaces are one change. An insertion or a
    deletion takes in the word before it (or after it, at the start), so every change covers text that can be
    underlined and never pastes nothing."""
    parts = []  # [old, new, changed]
    for a, b in writer.hunks(core, new):
        if a == b:
            parts.append([a, b, False])
        elif len(parts) >= 2 and not parts[-1][2] and not parts[-1][0].strip() and parts[-2][2]:
            gap = parts.pop()[0]
            parts[-1][0] += gap + a
            parts[-1][1] += gap + b
        else:
            parts.append([a, b, True])
    out, at = [], 0
    for i, (a, b, changed) in enumerate(parts):
        if changed and not (a.strip() and b.strip()):
            before = _LAST_WORD.search(parts[i - 1][0]) if i else None
            after = _FIRST_WORD.match(parts[i + 1][0]) if i + 1 < len(parts) else None
            if before:
                word = before.group()
                out.append((at - units(word), word + a, word + b))
            elif after:
                word = after.group()
                out.append((at, a + word, b + word))
        elif changed:
            out.append((at, a, b))
        at += units(a)
    return out


def applied(cache: Cache, mode, suggestion: Suggestion) -> None:
    """After `suggestion` was applied, its edited line keeps the same rewrite, so its other suggestions stay
    and it is not checked again."""
    core, offset, old, new = suggestion.key
    rewrite = cache.get(core, mode)
    if rewrite is None:
        return
    edited = text_at(core, 0, offset) + new + text_at(core, offset + units(old), units(core))
    cache.put(edited, mode, rewrite)


def suggestions(text: str, cache: Cache, dismissed, mode) -> list[Suggestion]:
    """Suggestions for every line whose rewrite in `mode` is cached, in document order."""
    out = []
    for start, line in lines(text):
        core = line.strip()
        new = cache.get(core, mode) if core else None
        if new is None or new == core:
            continue
        at = start + units(line[:line.index(core)])
        for offset, old, replacement in changes(core, new):
            key = (core, offset, old, replacement)
            if key not in dismissed:
                out.append(Suggestion(key, at + offset, at + offset + units(old), old, replacement))
    return out
