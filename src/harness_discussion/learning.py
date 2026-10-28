"""Local vocabulary from bounded edits to confirmed dictation. No native reads, input, network or logging."""
import difflib
import re
import unicodedata

from . import live

MAX_WORDS, MAX_CHARS, MAX_REGION, MAX_TOKENS = 200, 60, 4_000, 300
WINDOW_S, PAUSE_S, SETTLE_S = 30, 1.5, 1
_TERM = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*(?: [^\W\d_]+(?:['’\-][^\W\d_]+)*){0,2}\Z")
_COMMON = set("""a an the and or but for not with you your this that these those his her she he it its they them
we us our from there their here what why when where who which how all would could should will shall can
may might must one two three some any every each other another same new old good bad more most less last
next first second then now today tomorrow yesterday yes no okay like love want need have has had do does
did is are was were be been being get got make made take took give gave come came go went know think say
said tell see look use work time year day thing just also only very much many still too again never ever
always about into onto over under after before between through back up down out in on at to of as so if
than because while until hello hi thanks thank please lets let dont cant wont isnt cannot well right left
me my mine yours ours theirs him myself yourself themselves itself ourselves loves likes wants needs""".split())


def normalize(words):
    """Validated most-recent vocabulary; settings and UI are trust boundaries."""
    clean = {}
    for word in words if isinstance(words, (list, tuple)) else ():
        if not isinstance(word, str):
            continue
        word = unicodedata.normalize("NFC", word.strip())
        if 3 <= len(word) <= MAX_CHARS and _TERM.fullmatch(word):
            clean.pop(word.casefold(), None)
            clean[word.casefold()] = word
    return list(clean.values())[-MAX_WORDS:]


def hint(words):
    selected, size = [], 0
    for word in reversed(normalize(words)):
        if size + len(word) + 2 <= 1200:
            selected.append(word)
            size += len(word) + 2
    return ", ".join(reversed(selected))


def corrections(original, edited):
    if max(live.units(original), live.units(edited)) > MAX_REGION:
        return []
    old, new = ([w.strip('.,!?;:()[]"“”') for w in text.split()] for text in (original, edited))
    if max(len(old), len(new)) > MAX_TOKENS:
        return []
    ops = difflib.SequenceMatcher(None, [w.casefold() for w in old], [w.casefold() for w in new],
                                autojunk=False).get_opcodes()
    if sum(b - a for kind, a, b, _, _ in ops if kind == "replace") > max(3, len(old) / 2):
        return []
    found = []
    for kind, a, b, c, d in ops:
        if kind != "replace" or max(b - a, d - c) > 3:
            continue
        before, after = " ".join(old[a:b]), " ".join(new[c:d])
        if not normalize([before]) or not normalize([after]) or any(w.casefold() in _COMMON for w in new[c:d]):
            continue
        # ponytail: distant mishearings are skipped; add a local classifier only with a correction corpus.
        if difflib.SequenceMatcher(None, before.casefold().replace(" ", ""),
                                   after.casefold().replace(" ", ""), autojunk=False).ratio() >= 0.65:
            found.append(after)
    return normalize(found)


class Watch:
    """One inserted region, anchored to unchanged surroundings; native code binds every read to context."""

    def __init__(self, context, before, text, now):
        self.context, self.before, self.original = context, before, text
        self.started, self.changed_at = now, now
        self.closed, self.confirmed, self.pending = False, False, False
        start, end = before.caret
        self.prefix = live.text_at(before.text, 0, start)
        self.suffix = live.text_at(before.text, end, live.units(before.text))
        self.expected = self.prefix + text + self.suffix
        self.start, self.end = start, start + live.units(text)
        self.value = text
        # Reject native ranges that split a surrogate pair or exceed the bounded document.
        try:
            self.prefix.encode("utf-8")
            self.suffix.encode("utf-8")
        except UnicodeError:
            self.closed = True
        if not (0 <= start <= end <= live.units(before.text)) or live.units(text) > MAX_REGION:
            self.closed = True

    def poll(self, field, now):
        if self.closed:
            return []
        if field is None or now >= self.started + WINDOW_S or (field.app, field.window) != (
                self.before.app, self.before.window):
            self.closed = True
            return []
        if not self.confirmed:
            if field.text == self.expected and field.caret == (self.end, self.end):
                self.confirmed = True
            elif field.text != self.before.text or now > self.started + SETTLE_S:
                self.closed = True
            return []
        text = field.text
        if not text.startswith(self.prefix) or not text.endswith(self.suffix):
            self.closed = True
            return []
        stop = len(text) - len(self.suffix) if self.suffix else len(text)
        region = text[len(self.prefix):stop]
        end = self.start + live.units(region)
        if (stop < len(self.prefix) or live.units(region) > MAX_REGION
                or not self.start <= field.caret[0] <= field.caret[1] <= end):
            self.closed = True
            return []
        if region != self.value:
            self.value, self.changed_at, self.pending = region, now, True
        if self.pending and now - self.changed_at >= PAUSE_S:
            self.pending = False
            return corrections(self.original, region)
        return []
