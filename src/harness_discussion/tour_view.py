"""The tour's view model: every step's copy and state in one dict, so the Mac and Windows windows render it
and never decide copy."""
from . import hotkeys, sources, tour, tour_copy, writer
from .settings import status_text

DONE_LABELS = ("Voice", "Read", "Rewrite", "Review", "Dictation", "Check as I type", "About you")
WRITER_FIX = {"Cursor": "Sign in to Cursor", "Codex": "Sign in to Codex", "Local": "Download Local…"}


def _row(name, sub=None, pill=None, ok=False, actions=()):
    return {"name": name, "sub": sub, "pill": pill, "ok": ok, "actions": list(actions)}


def _lead(cfg, step):
    _, lead, plain = tour_copy.COPY[step]
    for key, name in (("rewrite_hotkey", "rewrite"), ("dictate_hotkey", "dictate")):
        if "{" + name + "}" in lead:
            label = hotkeys.label(cfg[key])
            return plain if label == "Off" else lead.replace("{" + name + "}", label)
    return lead


def _writer_card(s):
    return _row(*tour_copy.NO_SOURCE,
                actions=[(WRITER_FIX[n], ("fix_writer", (n, False)))
                         for n, _ in s.cfg["writer_sources"] if s.writer_statuses.get(n) != "ready"])


def _stt_card(s):
    return _row(*tour_copy.NO_STT,
                actions=[(("Download Local" if n == "Local" else f"Sign in to {n}"), ("fix_writer", (n, True)))
                         for n, _ in s.cfg["stt_sources"] if s.stt_statuses.get(n) != "ready"])


def _dot(i, index, progress):
    return "now" if i == index else {"tried": "tried", "skipped": "skipped"}.get(progress.get(tour.STEPS[i]), "")


def view(index, s) -> dict:
    """The view dict for step `index`; len(tour.STEPS) is the Done screen."""
    if index >= len(tour.STEPS):
        return _done(s)
    step = tour.STEPS[index]
    tried = s.progress.get(step) == tour.TRIED
    v = {"index": index, "count": len(tour.STEPS), "step": step,
         "title": tour_copy.COPY[step][0], "lead": _lead(s.cfg, step),
         "rows": [], "quote": None, "box": None, "status": None, "dest": None, "keep": None,
         "shortcuts": [], "sample": step in s.sample_used,
         "ready": tried or (step == "welcome" and not s.mac),
         "dots": [_dot(i, index, s.progress) for i in range(len(tour.STEPS))], "done": None}
    _STEPS[step](v, s, tried)
    return v


def _welcome(v, s, tried):
    if not s.mac:
        return
    if s.trusted:
        v["rows"].append(_row("Accessibility access", "Needed for the shortcuts and to read the text you select.",
                              pill="Allowed", ok=True))
    else:
        v["rows"].append(_row("Accessibility access",
                              "Harness Discussion was updated, so macOS needs permission again." if s.stale
                              else "Needed for the shortcuts and to read the text you select.", pill="Waiting…",
                              actions=[("Fix permission" if s.stale else "Allow access", ("allow", None))]))


def _voice(v, s, tried):
    for name, _ in s.cfg["sources"]:
        status = s.statuses.get(name)
        fix = sources.fix(name, status)
        v["rows"].append(_row(name, status_text(status), ok=status == "ready",
                              actions=[(fix[0], ("fix_source", (name,)))] if fix else []))
    if tried:
        v["status"] = (f"✓ Played with {s.voice_source or 'a local voice'}", True)
    v["dest"] = (f"Read by {s.voice_source}. Online voices get only this sample text."
                 if s.voice_source else tour_copy.NO_VOICE)


def _read(v, s, tried):
    v["box"], v["shortcuts"] = {"text": tour_copy.READ, "select": None}, [("hotkey", None)]
    if tried:
        text = "✓ You played it from the bar."
        label = hotkeys.label(s.cfg["hotkey"])
        if label != "Off":
            text += f" Next time: {label} reads a selection without the bar."
        v["status"] = (text, True)
    v["dest"] = (f"Read by {s.voice_source}. Online voices get only the text you select here."
                 if s.voice_source else tour_copy.NO_VOICE)


def _rewrite(v, s, tried):
    v["box"], v["shortcuts"] = {"text": tour_copy.REWRITE, "select": "all"}, [("rewrite_hotkey", None)]
    if s.writer_source is None:
        v["rows"].append(_writer_card(s))
    if tried:
        v["status"] = (("✓ Rewritten in place, using a sample result.", True) if v["sample"]
                       else ("✓ Rewritten in place. Undo puts your words back.", True))
    v["dest"] = _with_about(f"Sent to {s.writer_source}, the first ready writing source. Only this sample text is sent."
                            if s.writer_source
                            else "No writing source is ready, so the rewrite is a sample. Only this sample text is sent.",
                            s, "Only this sample text is sent")
    if "writer" in s.sandboxed:
        v["keep"] = {"label": "Keep Writing on in all apps", "action": "keep_writer",
                     "note": "Check as I type comes on with it." if s.cfg["live_check"] else None}
    else:
        label = hotkeys.label(s.cfg["rewrite_hotkey"])
        v["keep"] = {"text": f"Writing is on. {label} works in any app." if label != "Off"
                    else "Writing is on. ✎ on the bar works in any app."}


def _with_about(dest, s, verb):
    """A destination line that also names the About you context, when prompts carry it."""
    return dest.replace(verb, f"{verb}, with your About you note and today's date", 1) if writer.about(s.cfg) else dest

def _review(v, s, tried):
    if s.has_practice_edit and s.review_text is not None:
        v["box"] = {"text": s.review_text, "select": None}
        v["rows"].append(_row("Click Review on the toast, or", ok=True,
                              actions=[("Open Review", ("review", None))]))
    else:
        v["box"] = {"text": tour_copy.REWRITE, "select": "all"}
        label = hotkeys.label(s.cfg["rewrite_hotkey"])
        v["rows"].append(_row(f"Press {label} first, then Review." if label != "Off"
                              else "Press ✎ on the bar first, then Review."))
    if s.writer_source is None:
        v["rows"].append(_writer_card(s))
    if tried:
        v["status"] = ("✓ You opened Review.", True)


def _dictate(v, s, tried):
    v["box"], v["quote"], v["shortcuts"] = {"text": "", "select": None}, tour_copy.SAY, [("dictate_hotkey", None)]
    if s.mac and not s.trusted:
        v["rows"].append(_row("Dictation needs Accessibility access",
                              actions=[("Allow access", ("allow", None))]))
    if s.stt_source is None:
        v["rows"].append(_stt_card(s))
    if tried:
        v["status"] = (("✓ Typed where your cursor was, using a sample transcript.", True) if v["sample"]
                       else ("✓ Typed where your cursor was.", True))
    if s.stt_source:
        v["dest"] = f"Your recording goes to {s.stt_source}."
        if s.mac:
            v["dest"] += " macOS asks for the microphone the first time."
    else:
        v["dest"] = "No speech source is ready, so the text is a sample transcript. Your recording is not sent."


def _live(v, s, tried):
    v["box"] = {"text": tour_copy.LIVE, "select": "end"}
    if s.writer_source is None:
        v["rows"].append(_writer_card(s))
    if tried:
        v["status"] = ("✓ Applied. Undo is on the toast.", True)
    elif v["sample"]:
        v["status"] = ("Shown with a sample suggestion. Sign in for real ones.", False)
    v["dest"] = (f"Only lines you type in this box are sent, to {s.writer_source}." if s.writer_source else
                 "Only lines you type in this box are sent. No writing source is ready, so the suggestion is a sample.")
    v["dest"] = _with_about(v["dest"], s, "are sent")
    if "live_check" in s.sandboxed:
        v["keep"] = {"label": "Check as I type in all apps", "action": "keep_live", "note": "asks first"}
    elif "writer" in s.sandboxed:
        v["keep"] = {"label": "Keep Writing on in all apps", "action": "keep_writer",
                     "note": "Check as I type comes on with it."}
    else:
        where = f"go to {s.writer_source} after a pause" if s.writer_source \
            else "go to the first ready writing source after a pause"
        v["keep"] = {"text": f"Check as I type is on in all apps: lines you type there {where}. "
                             "Turn it off in Settings → Writing."}


def _about(v, s, tried):
    v["box"] = {"text": s.cfg.get("about_me", ""), "select": "end"}
    if tried:
        v["status"] = ("✓ Saved. Edit it any time in Settings → General.", True)
    v["dest"] = (f"Up to {writer.about_limit(s.cfg)} characters. Sent with every rewrite, Check as I type, "
                 "dictation tidy, and summary to the source that runs it. Turn it off in Settings → General.")

def _done(s):
    first = "Allow access" if s.mac else "Welcome"
    rows = [(first, s.progress.get("welcome") == tour.TRIED, "welcome")]
    rows += [(label, s.progress.get(step) == tour.TRIED, step)
             for label, step in zip(DONE_LABELS, tour.STEPS[1:])]
    return {"index": len(tour.STEPS), "count": len(tour.STEPS), "step": None,
            "title": f"You're set: {tour.tried_count(s.progress)} of {len(tour.STEPS)} tried", "lead": None,
            "rows": [], "quote": None, "box": None, "status": None, "dest": None, "keep": None,
            "shortcuts": [("hotkey", None), ("rewrite_hotkey", "press twice to pick a mode"),
                          ("dictate_hotkey", None)],
            "sample": False, "ready": True,
            "dots": [_dot(i, len(tour.STEPS), s.progress) for i in range(len(tour.STEPS))],
            "done": {"rows": rows, "more": tour_copy.MORE}}


_STEPS = {"welcome": _welcome, "voice": _voice, "read": _read, "rewrite": _rewrite, "review": _review,
          "dictate": _dictate, "live": _live, "about": _about}