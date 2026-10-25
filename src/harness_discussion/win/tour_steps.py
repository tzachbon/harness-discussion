"""The parts of a Windows tour step, drawn from tour_view's dict. Each part wires its buttons to the app."""
import tkinter as tk
from tkinter import messagebox

from .. import hotkeys, writer_settings
from .bar_parts import HUD_BG as CARD, HUD_CHIP as CHIP, HUD_FG as FG, HUD_HOVER as HOVER, HUD_MUTED as MUTED
from .practice_field import PracticeField
from .shortcut_row import TITLES

BG, PRIMARY, PRIMARY_HOVER, FAINT = "#1c1c1f", "#0a84ff", "#3498ff", "#7c7c85"
SUCCESS, WARNING, ERROR, KNOB, DIVIDER = "#30d158", "#ff9f0a", "#ff6961", "#d6d6db", "#38383c"
WRAP, NARROW = "440p", "220p"  # NARROW fits the Done screen's shortcut column


def clear(frame):
    for child in frame.winfo_children():
        child.destroy()
    frame.configure(width=1, height=1)  # pack keeps an emptied frame's old size


def text(w, parent, s, size=10, fg=FG, bold=False, wrap=WRAP):
    return tk.Label(parent, text=s, bg=parent["bg"], fg=fg, font=(w.fonts[1], size, "bold" if bold else "normal"),
                    justify="left", anchor="w", wraplength=wrap, padx=0, pady=0)


def button(w, parent, title, command, kind="chip"):
    """A label button with the bar's hover fill; it acts on release over itself, as the bar's buttons do."""
    bg = parent["bg"]
    normal, hover, fg, hover_fg = {"chip": (CHIP, HOVER, FG, FG), "primary": (PRIMARY, PRIMARY_HOVER, FG, FG),
                                   "ghost": (bg, HOVER, MUTED, FG), "link": (bg, bg, MUTED, FG)}[kind]
    b = tk.Label(parent, text=title, bg=normal, fg=fg, font=(w.fonts[1], 10), cursor="hand2",
                 padx=0 if kind == "link" else "8p", pady=0 if kind == "link" else "2p")
    b.bind("<Enter>", lambda _e: b.configure(bg=hover, fg=hover_fg))
    b.bind("<Leave>", lambda _e: b.configure(bg=normal, fg=fg))
    b.bind("<ButtonRelease-1>", lambda e: command() if b.winfo_containing(e.x_root, e.y_root) is b else None)
    return b


def action(w, name, arg=None):
    return lambda: w.app.tour_action(name, arg) if arg is not None else w.app.tour_action(name)


def header(w, parent, v):
    if v["done"] is None:
        text(w, parent, f"Step {v['index'] + 1} of {v['count']}", 9, FAINT).pack(side="left")
        button(w, parent, "Skip tour", w.app.tour_skip_all, "link").pack(side="right")


def heading(w, parent, v):
    text(w, parent, v["title"], 16, bold=True).pack(anchor="w", pady=("6p", "3p"))
    if v["lead"]:
        text(w, parent, v["lead"], fg=MUTED).pack(anchor="w", pady=(0, "8p"))


def rows(w, parent, v):
    """All card rows in one card, split by dividers. Two or more actions go on their own line under the text."""
    card = tk.Frame(parent, bg=CARD, padx="9p", pady="2p") if v["rows"] else None
    if card:
        card.pack(fill="x", pady=(0, "6p"))
    for i, row in enumerate(v["rows"]):
        if i:
            tk.Frame(card, bg=DIVIDER, height=1).pack(fill="x")
        line = tk.Frame(card, bg=CARD)
        line.pack(fill="x", pady="4p")
        names = tk.Frame(line, bg=CARD)
        names.pack(side="left", fill="x", expand=True)
        text(w, names, row["name"], wrap="270p").pack(anchor="w")
        if row["sub"]:
            text(w, names, row["sub"], 9, FAINT, wrap="270p").pack(anchor="w")
        below = len(row["actions"]) > 1
        holder = tk.Frame(card, bg=CARD) if below else line
        if below:
            holder.pack(fill="x", pady=(0, "4p"))
        for title, (name, arg) in row["actions"] if below else reversed(row["actions"]):
            button(w, holder, title, action(w, name, arg)).pack(side="left" if below else "right",
                                                                padx=(0, "6p") if below else ("6p", 0))
        if row["pill"]:
            pill = text(w, line, row["pill"], 9, SUCCESS if row["ok"] else WARNING)
            pill.configure(bg=CHIP, padx="6p")
            pill.pack(side="right", padx=("6p", 0))
    if v["step"] == "voice":  # the sample button is the step's practice; the view has no row for it
        button(w, parent, "▶ Play again" if v["status"] else "▶ Play a sample", action(w, "play"),
               "chip" if v["status"] else "primary").pack(anchor="w", pady=(0, "6p"))


def shortcuts(w, parent, v):
    """One row per shortcut; the recorder (shortcut_row.ShortcutRow) owns the label and error texts."""
    for key, sub in v["shortcuts"]:
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=(0, "2p"))
        text(w, row, TITLES[key], fg=MUTED, wrap=0).pack(side="left")
        label = text(w, row, hotkeys.label(w.cfg[key]), bold=True, wrap=0)
        label.pack(side="left", padx="8p")
        button(w, row, "Change…", lambda key=key: w._record_hotkey(key)).pack(side="left")
        if sub:
            text(w, parent, sub, 9, FAINT, wrap=NARROW).pack(anchor="w")
        error = text(w, parent, "", 9, ERROR, wrap=NARROW)
        error.pack(anchor="w")
        w.shortcuts[key], w._shown[key] = {"label": label, "error": error}, w.cfg[key]


def quote(w, parent, v):
    if v["quote"]:
        frame = tk.Frame(parent, bg=BG)
        frame.pack(fill="x", pady=(0, "8p"))
        tk.Frame(frame, bg=PRIMARY, width=3).pack(side="left", fill="y")
        text(w, frame, v["quote"], 12).pack(side="left", padx="9p", pady="4p")


def box(w, parent, v):
    if v["box"] is None:
        return
    field = PracticeField(parent, v["box"])
    field.widget.pack(fill="x")
    # Recorded keys never reach the box: Tk runs these before the Text class binding that inserts them.
    field.widget.bind("<KeyPress>", lambda e: w._hotkey_key(e) if w._recording else None)
    field.widget.bind("<KeyRelease>", lambda e: w._hotkey_release(e) if w._recording else None)
    button(w, parent, "Start over", action(w, "start_over"), "link").pack(anchor="e", pady=("3p", 0))
    w.box = field


def status(w, parent, v):
    if v["status"]:
        line, ok = v["status"]
        text(w, parent, line, fg=SUCCESS if ok else MUTED).pack(anchor="w", pady=("6p", 0))


def dest(w, parent, v):
    if v["dest"]:
        text(w, parent, v["dest"], 9, FAINT).pack(anchor="w", pady=("6p", 0))


def keep(w, parent, v):
    k = v["keep"]
    if not k:
        return
    if "text" in k:
        text(w, parent, k["text"], 9, MUTED).pack(anchor="w", pady=("8p", 0))
        return
    row = tk.Frame(parent, bg=BG)
    row.pack(fill="x", pady=("8p", 0))
    w.keep_switch = Switch(row, lambda: _confirm(w, k["action"]), action(w, k["action"]))
    w.keep_switch.pack(side="left")
    text(w, row, k["label"], wrap=0).pack(side="left", padx=("8p", 0))
    if k["note"]:
        text(w, row, k["note"], 9, FAINT, wrap=0).pack(side="left", padx=("8p", 0))


def _confirm(w, name):
    if name != "keep_live":
        return True
    names = [n for n, on in w.cfg["writer_sources"] if on] or ["no source"]
    return messagebox.askokcancel("Check as I type", writer_settings.live_confirm_text(names, "this PC"),
                                  icon="question", parent=w.win)


class Switch(tk.Canvas):
    """The keep switch. It only turns on: turning a kept feature off again is in Settings."""

    def __init__(self, master, confirm, on_change):
        h = self._height = round(master.winfo_fpixels("14p"))
        w = self._width = round(h * 1.7)  # not self._w: tkinter keeps the widget's path there
        super().__init__(master, width=w, height=h, bg=master["bg"], highlightthickness=0, cursor="hand2")
        self.on, self._confirm, self._on_change = False, confirm, on_change
        self._track = [self.create_oval(0, 0, h, h, outline=""), self.create_oval(w - h, 0, w, h, outline=""),
                       self.create_rectangle(h / 2, 0, w - h / 2, h, outline="")]
        self._knob = self.create_oval(0, 0, 0, 0, fill=KNOB, outline="")
        self.bind("<ButtonRelease-1>", lambda _e: self.click())
        self._draw()

    def click(self):
        if self.on or not self._confirm():
            return
        self.on = True
        self._draw()  # before the action, whose refresh may replace this switch
        self._on_change()

    def _draw(self):
        for item in self._track:
            self.itemconfigure(item, fill=SUCCESS if self.on else CHIP)
        h, shift = self._height, self._width - self._height if self.on else 0
        self.coords(self._knob, shift + 2, 2, shift + h - 2, h - 2)


def done(w, parent, notes, v):
    card = tk.Frame(parent, bg=CARD, padx="9p", pady="4p")
    card.pack(fill="x", pady=(0, "8p"))
    for label, tried, step in v["done"]["rows"]:
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", pady="2p")
        text(w, row, f"✓ {label}" if tried else f"{label} – skipped", fg=SUCCESS if tried else MUTED,
             wrap=0).pack(side="left")
        if not tried:
            button(w, row, "Try now", action(w, "try", step)).pack(side="right")
    text(w, notes, v["done"]["more"], 9, FAINT, wrap="200p").pack(anchor="w", pady=("6p", 0))


def footer(w, parent, v):
    dots = tk.Canvas(parent, bg=BG, highlightthickness=0)
    d = round(parent.winfo_fpixels("5p"))
    x = 0
    for mark in v["dots"]:
        color = {"tried": SUCCESS, "skipped": FAINT, "now": FG}.get(mark, CHIP)
        if mark == "now":
            dots.create_line(x + d / 2, d / 2, x + 2 * d, d / 2, width=d, capstyle="round", fill=color)
            x += d
        else:
            dots.create_oval(x, 0, x + d, d, fill=color, outline="")
        x += d + d * 0.8
    dots.configure(width=round(x), height=d)
    dots.pack(side="left")
    if v["done"] is not None:
        button(w, parent, "Finish", w.app.tour_finish, "primary").pack(side="right")
        button(w, parent, "Open Settings", action(w, "settings")).pack(side="right", padx=(0, "6p"))
    else:
        button(w, parent, "Next", w.app.tour_next, "primary" if v["ready"] else "chip").pack(side="right")
        button(w, parent, "Skip", w.app.tour_skip, "ghost").pack(side="right", padx=(0, "6p"))
