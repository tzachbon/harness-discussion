"""Check as you type on the Mac: where a range of a focused field's text sits on screen, from AX alone."""
import ApplicationServices as AS
from ApplicationServices import (AXUIElementCopyAttributeValue, AXValueCreate, AXValueGetValue, kAXErrorSuccess,
                                 kAXValueCFRangeType)
from CoreFoundation import CFRangeMake

from .. import live

LEAF_DEPTH, LEAF_NODES = 12, 400  # how far into a web field its text nodes are looked for


def _attr(element, name):
    err, value = AXUIElementCopyAttributeValue(element, name, None)
    return value if err == kAXErrorSuccess else None

def _param(element, name, value):
    err, result = AS.AXUIElementCopyParameterizedAttributeValue(element, name, value, None)
    return result if err == kAXErrorSuccess else None

def _cf_range(start, length):
    return AXValueCreate(kAXValueCFRangeType, CFRangeMake(start, length))

def _rect(element, start, length):
    bounds = _param(element, "AXBoundsForRange", _cf_range(start, length))
    ok, r = AXValueGetValue(bounds, AS.kAXValueCGRectType, None) if bounds is not None else (False, None)
    if not ok:
        return None
    return int(r.origin.x), int(r.origin.y), int(r.origin.x + r.size.width), int(r.origin.y + r.size.height)

def _shown(r):
    return r[2] > r[0] and r[3] > r[1]

def _same_row(a, b):
    return abs(a[1] - b[1]) < max(1, min(a[3] - a[1], b[3] - b[1])) / 2

def _rows(element, start, length):
    """One rectangle per visual line of [start, start + length) in `element`, from its bounds alone: [] when it is
    not laid out or not visible, None when unreadable. Chromium's line indexes can't be trusted, so where a piece's
    first and last characters sit on different rows, each row's last character is found by bisection."""
    whole = _rect(element, start, length)
    if whole is None or not _shown(whole):
        return None if whole is None else []
    end, last = start + length, _rect(element, start + length - 1, 1)
    rows, at = [], start
    while at < end:
        first = _rect(element, at, 1)
        if first is None or last is None or not (_shown(first) and _shown(last)):
            return rows + [_rect(element, at, end - at) or whole] if rows else [whole]
        if _same_row(first, last):
            rows.append(whole if at == start else _rect(element, at, end - at) or whole)
            break
        lo, hi = at, end - 2  # the last character of this row
        while lo < hi:
            mid = (lo + hi + 1) // 2
            r = _rect(element, mid, 1)
            if r is None:
                return [whole]
            lo, hi = (mid, hi) if _same_row(first, r) else (lo, mid - 1)
        row = _rect(element, at, lo + 1 - at)
        if row is None:
            return [whole]
        if _shown(row):
            rows.append(row)
        at = lo + 1
    return rows or [whole]

def _by_lines(element, text, start, end):
    """Native text views: split at the field's own line ranges. None when those are inconsistent, as in Chromium."""
    rects, at = [], start
    while at < end:
        line = _param(element, "AXLineForIndex", at)
        line_range = _param(element, "AXRangeForLine", line) if line is not None else None
        ok, rng = AXValueGetValue(line_range, kAXValueCFRangeType, None) if line_range is not None else (False, None)
        if not ok or not rng[0] <= at < rng[0] + rng[1]:
            return None
        stop = min(end, rng[0] + rng[1])
        if str(_param(element, "AXStringForRange", _cf_range(at, stop - at)) or "") != live.text_at(text, at, stop):
            return None
        r = _rect(element, at, stop - at)
        if r is None:
            return None
        if _shown(r):
            rects.append(r)
        at = stop
    return rects

def text_nodes(element, text):
    """(start, node, text) of each text node inside a web field in document order, start in the field's UTF-16
    units, found by matching each node's text in order. [] for a native field."""
    out, pos, seen = [], 0, 0

    def walk(node, depth):
        nonlocal pos, seen
        for child in _attr(node, "AXChildren") or ():
            seen += 1
            if seen > LEAF_NODES:
                return
            if _attr(child, "AXRole") == "AXStaticText":
                value = str(_attr(child, "AXValue") or "")
                at = text.find(value, pos) if value else -1
                if at >= 0:
                    out.append((live.units(text[:at]), child, value))
                    pos = at + len(value)
            elif depth < LEAF_DEPTH:
                walk(child, depth + 1)
    walk(element, 1)
    return out

def _by_leaves(leaves, text, start, end):
    """Web fields (contenteditable): bounds from the text nodes the span covers. None when a non-space part of the
    span is in no node, or a node reads otherwise."""
    rects, covered = [], [False] * (end - start)
    for at, node, value in leaves:
        a, b = max(start, at), min(end, at + live.units(value))
        if a >= b:
            continue
        found = _param(node, "AXStringForRange", _cf_range(a - at, b - a))
        if found is not None and str(found) != live.text_at(text, a, b):
            return None
        rows = _rows(node, a - at, b - a)
        if rows is None:
            return None
        rects += rows
        covered[a - start:b - start] = [True] * (b - a)
    gaps = "".join(live.text_at(text, start + i, start + i + 1) for i, done in enumerate(covered) if not done)
    return rects if any(covered) and not gaps.strip() else None

def _by_range(element, text, start, end):
    """Chromium text areas: one bounds query on the field for the whole span."""
    if str(_param(element, "AXStringForRange", _cf_range(start, end - start)) or "") != live.text_at(text, start, end):
        return None
    return _rows(element, start, end - start)

def place(element, text, start, end, expected, leaves):
    """Rectangles for [start, end), one per line: empty when not visible (or not laid out yet), None when AX cannot
    prove the range reads `expected`. `leaves()` gives the field's text nodes, read once per edit.bounds() call."""
    if live.text_at(text, start, end) != expected:
        return None
    tried = [_by_lines(element, text, start, end)]
    if tried[0]:
        return tried[0]
    for way in (lambda: _by_leaves(leaves(), text, start, end), lambda: _by_range(element, text, start, end)):
        tried.append(way())
        if tried[-1]:
            return tried[-1]
    return [] if any(t is not None for t in tried) else None
