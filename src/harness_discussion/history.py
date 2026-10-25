"""Recent dictations on disk, so a transcript survives a failed tidy, a lost paste or a crash."""
import json
import os
import threading
import time

from . import settings

PATH = os.path.join(settings.DIR, "dictation-history.json")
LIMIT = 10  # ponytail: 10 recordings of at most 60 s stay under ~20 KB, rewritten whole; move to sqlite3 for full search
_lock = threading.Lock()  # the worker adds, the main thread reads and clears

def load() -> list:
    """[{"at", "raw", "text"}], newest first. "text" is the tidied result, or None until tidy finishes."""
    try:
        with open(PATH, encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, ValueError):
        return []
    return [i for i in items if isinstance(i, dict) and isinstance(i.get("raw"), str)] if isinstance(items, list) else []

def _save(items):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items[:LIMIT], f, ensure_ascii=False)
    os.replace(tmp, PATH)

def add(raw) -> float:
    """Saves the transcript before tidy runs. Returns its key for finish()."""
    at = time.time()
    with _lock:
        _save([{"at": at, "raw": raw, "text": None}, *load()])
    return at

def finish(at, text) -> None:
    with _lock:
        _save([{**i, "text": text} if i.get("at") == at else i for i in load()])

def clear() -> None:
    with _lock:
        try:
            os.remove(PATH)
        except FileNotFoundError:
            pass

def rows() -> list:
    """[(preview, text, original)] newest first. original is the raw transcript when tidy changed it, else None."""
    out = []
    for i in load():
        text = i["text"] if isinstance(i.get("text"), str) else i["raw"]
        out.append((_preview(text), text, i["raw"] if text != i["raw"] else None))
    return out

def entries() -> list:
    """[(label, text)] for a menu: each dictation, then its original when tidy changed it."""
    out = []
    for preview, text, original in rows():
        out.append((preview, text))
        if original is not None:
            out.append(("    Original: " + _preview(original), original))
    return out

def _preview(text, n=50):
    line = " ".join(text.split())
    return line if len(line) <= n else line[:n - 1] + "…"
