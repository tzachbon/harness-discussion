"""Drawing the tour's view dict on macOS, one function per part. Each takes (window, view, width), returns the views
for its slot, and wires its buttons to app.tour_action or the app's tour_* navigation."""
import AppKit

from .. import writer_settings as ws
from . import ui
from .practice_field import PracticeField, rgb
from .reading_page import SHORTCUT_TITLES, WINDOW_HINT

WIDTH, INSET = 600, 22
COL = WIDTH - 2 * INSET  # the main column on steps
LEFT, GAP = 250, 12  # the Done screen's checklist, then its shortcut sheet
SIDE = COL - LEFT - GAP

FG, MUTED, FAINT = (255, 255, 255), (0xb8, 0xb8, 0xbf), (0x7c, 0x7c, 0x85)
CARD, CHIP, LINE = (0x26, 0x26, 0x29), (0x3b, 0x3b, 0x40), (255, 255, 255, 0.08)
PRIMARY, SUCCESS, WARNING = (0x0a, 0x84, 0xff), (0x30, 0xd1, 0x58), (0xff, 0x9f, 0x0a)
TILES = {"welcome": ("speaker.wave.2.fill", (0x5e, 0x5c, 0xe6)), "voice": ("waveform", (0xbf, 0x5a, 0xf2)),
         "read": ("play.fill", PRIMARY), "rewrite": ("pencil", WARNING), "review": ("plusminus", SUCCESS),
         "dictate": ("mic.fill", (0xff, 0x37, 0x5f)), "live": ("checkmark", (0x64, 0xd2, 0xff)),
         "about": ("person.fill", (0x8e, 0x8e, 0x93))}
DOTS = {"tried": ("●", SUCCESS), "skipped": ("●", FAINT), "now": ("▬", FG)}  # anything else: not reached


def text(s, size=13, color=FG, bold=False, width=None):
    label = ui.label(s, size, bold=bold, width=width)
    label.setTextColor_(rgb(*color))
    return label


def _attributed(s, color, size):
    return AppKit.NSAttributedString.alloc().initWithString_attributes_(
        s, {AppKit.NSForegroundColorAttributeName: rgb(*color),
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(size)})


def link(title, fn, color=MUTED, size=12):
    b = ui.button(title, fn)
    b.setBordered_(False)
    b.setAttributedTitle_(_attributed(title, color, size))
    return b


def small(title, fn, primary=False):
    b = ui.button(title, fn)
    b.setControlSize_(AppKit.NSControlSizeSmall)
    if primary:
        b.setBezelColor_(rgb(*PRIMARY))
    return b


def spacer():
    view = AppKit.NSView.alloc().init()
    view.setContentHuggingPriority_forOrientation_(1, AppKit.NSLayoutConstraintOrientationHorizontal)
    return view


def line(views, width, spacing=8):
    row = ui.stack(views, vertical=False, spacing=spacing)
    row.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
    return row


def card(views, width):
    box = ui.card(views, width=width)
    box.setFillColor_(rgb(*CARD))
    box.setBorderColor_(rgb(*LINE))
    return box


def pill(s, ok):
    color = SUCCESS if ok else WARNING
    label = text(f"  {s}  ", 11, color)
    label.setWantsLayer_(True)
    label.layer().setCornerRadius_(8)
    label.layer().setBackgroundColor_(rgb(*color, 0.18).CGColor())
    return label


def head(t, v, width):
    step = f"Step {v['index'] + 1} of {v['count']}" if v["step"] else "Done"
    skip = [] if v["done"] else [link("Skip tour", t.app.tour_skip_all)]
    return [line([text(step, 12, FAINT), spacer(), *skip], COL)]


def tile(t, v, width):
    if not v["step"]:
        return []
    symbol, color = TILES[v["step"]]
    return [ui.image_view(ui.tile(symbol, rgb(*color), 40), 40)]


def title(t, v, width):
    return [text(v["title"], 22, bold=True, width=width)]


def lead(t, v, width):
    return [text(v["lead"], 13, MUTED, width=width)] if v["lead"] else []


def shortcuts(t, v, width):
    """One row per shortcut: title, the current label and Change…, which records the way Settings does."""
    done = bool(v["done"])
    rows = []
    for key, sub in v["shortcuts"]:
        label = t.shortcut_labels[key] = text("", 13, bold=True)
        t.shortcut_hints[key] = text(WINDOW_HINT, 11, FAINT, width=width - (28 if done else 0))
        name = text(SHORTCUT_TITLES[key], 12 if done else 13, MUTED)  # 12 pt fits the sheet beside the checklist
        if sub:
            name = ui.stack([name, text(sub, 11, FAINT)], spacing=1)
        change = small("Change…", lambda key=key: t._record_hotkey(key))
        row = line([name, spacer(), label, change], width - 28) if done else \
            ui.stack([name, label, change], vertical=False, spacing=10)
        t._show_shortcut(key)
        t._shown[key] = t.cfg[key]
        rows.append(ui.stack([row, t.shortcut_hints[key]], spacing=4))  # the stack drops the hint while it's hidden
    if done and rows:
        return [card(rows, width), text(v["done"]["more"], 12, MUTED, width=width)]
    return rows


def _wide(views):
    return sum(v.fittingSize().width + 8 for v in views)


def _row(t, r, width):
    pills = [pill(r["pill"], r["ok"])] if r["pill"] else []
    buttons = [small(label, lambda a=action: t.app.tour_action(*a), primary=not r["ok"] and i == 0)
               for i, (label, action) in enumerate(r["actions"])]
    under = _wide(buttons) > width / 2  # several sign-in buttons go under the text, so the name keeps its line
    beside = pills + ([] if under else buttons)
    name_w = width - _wide(beside)
    left = [text(r["name"], 13, width=name_w)]
    if r["sub"]:
        left.append(text(r["sub"], 12, SUCCESS if r["ok"] and not r["pill"] else FAINT, width=name_w))
    row = line([ui.stack(left, spacing=2), spacer(), *beside], width)
    return ui.stack([row, ui.stack(buttons, vertical=False)], spacing=8) if under else row


def rows(t, v, width):
    return [card([_row(t, r, width - 28) for r in v["rows"]], width)] if v["rows"] else []


def quote(t, v, width):
    if not v["quote"]:
        return []
    bar = AppKit.NSBox.alloc().init()
    bar.setBoxType_(AppKit.NSBoxCustom)
    bar.setBorderWidth_(0)
    bar.setFillColor_(rgb(*PRIMARY))
    bar.widthAnchor().constraintEqualToConstant_(3).setActive_(True)
    bar.heightAnchor().constraintEqualToConstant_(24).setActive_(True)
    return [ui.stack([bar, text(f"“{v['quote']}”", 16, width=width - 15)], vertical=False, spacing=12)]


def box(t, v, width):
    """The practice box, reset to the step's sample, and Start over under it."""
    if not v["box"]:
        t.box = None
        return []
    if t.field is None:
        t.field = PracticeField(t.app, width)
    t.field.load(v["box"]["text"], v["box"]["select"])
    t.box = t.field
    return [t.field.view, line([spacer(), link("Start over", lambda: t.app.tour_action("start_over"))], width)]


def status(t, v, width):
    button = []
    if v["step"] == "voice":
        button = [small("▶ Play again" if v["status"] else "▶ Play a sample", lambda: t.app.tour_action("play"),
                        primary=not v["status"])]
    elif v["step"] == "review" and v["status"]:
        button = [small("Open Review again", lambda: t.app.tour_action("review"))]
    views = []
    if v["status"]:
        s, ok = v["status"]
        views.append(text(s, 13, SUCCESS if ok else MUTED, width=None if button else width))  # short beside a button
    views += button
    return [ui.stack(views, vertical=False, spacing=10)] if views else []


def dest(t, v, width):
    return [text(v["dest"], 12, FAINT, width=width)] if v["dest"] else []


def keep(t, v, width):
    k, t.keep_switch = v["keep"], None
    if k is None:
        return []
    if "text" in k:
        return [text(k["text"], 13, MUTED, width=width)]
    switch = t.keep_switch = ui.switch(False, lambda on: _keep(t, switch, k["action"], on))
    switch.setAccessibilityLabel_(k["label"])
    note = [text(k["note"], 12, FAINT)] if k["note"] else []
    return [ui.stack([switch, text(k["label"], 13), *note], vertical=False, spacing=10)]


def _keep(t, switch, action, on):
    if not on:
        return
    if action == "keep_live":
        names = [n for n, enabled in t.cfg["writer_sources"] if enabled] or ["no source"]
        if not t.app.host.confirm("Check as I type", ws.live_confirm_text(names, "this Mac"), ok="Turn On"):
            switch.setState_(0)
            return
    switch.setEnabled_(False)  # kept: turning it off again is in Settings → Writing
    t.app.tour_action(action)


def done(t, v, width):
    t.done_rows = []
    if not v["done"]:
        return []
    for label, tried, step in v["done"]["rows"]:
        parts = [text("✓" if tried else "–", 12, SUCCESS if tried else FAINT), text(label, 12)]
        if not tried:
            again = small("Try now", lambda step=step: t.app.tour_action("try", step))
            again.setControlSize_(AppKit.NSControlSizeMini)
            parts += [text("skipped", 11, FAINT), spacer(), again]
        t.done_rows.append(line(parts, width - 28, spacing=6))
    return [card(t.done_rows, width)]


def dots(v):
    s = AppKit.NSMutableAttributedString.alloc().init()
    for d in v["dots"]:
        glyph, color = DOTS.get(d, ("●", CHIP))
        s.appendAttributedString_(_attributed(glyph + " ", color, 10))
    return AppKit.NSTextField.labelWithAttributedString_(s)


def foot(t, v, width):
    if v["done"]:
        buttons = [ui.button("Open Settings", lambda: t.app.tour_action("settings")),
                   ui.button("Finish", t.app.tour_finish, primary=True)]
    else:
        buttons = [link("Skip", t.app.tour_skip, size=13), ui.button("Next", t.app.tour_next, primary=v["ready"])]
    return [line([dots(v), spacer(), *buttons], COL)]
