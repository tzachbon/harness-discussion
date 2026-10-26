"""Captures the website's Mac screenshots and clips from the production AppKit windows.

Run on a Mac: `uv run python packaging/capture_site_mac.py [scene ...]`, where a scene is one of SCENES (default all).
The windows open on the main screen without taking focus. Only this process's own windows are captured, so no
Screen Recording access is needed and nothing else on the screen can leak in. Sources show sample statuses,
callbacks do nothing, and nothing touches the network, a sign-in, or the clipboard. Writes docs/shots/.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest import mock

import AppKit
import Quartz
from PyObjCTools import AppHelper

from harness_discussion import settings, tour, tour_copy, tour_view, writer
from harness_discussion.mac import popup as mac_popup
from harness_discussion.mac import review as mac_review
from harness_discussion.mac import settings_ui, stt_page, ui
from harness_discussion.mac import toast as mac_toast
from harness_discussion.mac import tour as mac_tour

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "shots")
RAW = os.path.join(tempfile.gettempdir(), "harness-site-capture")
SCREEN_H = AppKit.NSScreen.screens()[0].frame().size.height
CFG = dict(settings.DEFAULTS, hotkey="<cmd>+<alt>+r", rewrite_hotkey="<cmd>+<alt>+e", dictate_hotkey="<cmd>+<alt>+d",
           caption_background=True)
STATUSES = {"Cursor": "ready", "Codex": "ready", "Kokoro": "not_downloaded", "macOS": "ready"}
WRITER_STATUSES = {"Cursor": "ready", "Codex": "ready", "Local": "missing"}
STT_STATUSES = {"Local": "missing", "Cursor": "ready", "Codex": "ready"}
READ_TEXT = tour_copy.READ + " Pause, skip a sentence, or change the speed from the bar."
CAPTIONS = ("Harness Discussion reads any text you select, in any app.",
            "It reads the opening first, so you hear it within a second, then continues sentence by sentence.",
            "Pause, skip a sentence, or change the speed from the bar.")
MODES = [m.name for m in writer.BUILTIN_MODES]
BACKDROP = (0x1c, 0x1c, 0x1f)
FPS = 30


def noop(*_a, **_k):
    return None


def top_left(frame):
    """A Cocoa frame in global coordinates with a top-left origin, as Quartz window lists use."""
    return ((frame.origin.x, SCREEN_H - frame.origin.y - frame.size.height), (frame.size.width, frame.size.height))


def grab(wins, rect=Quartz.CGRectNull):
    """Visible windows of this process, front to back, composited by the window server with their shadows.
    CGRectNull crops to the windows and leaves everything around them transparent."""
    ids = [w.windowNumber() for w in wins if w.isVisible()]
    image = Quartz.CGWindowListCreateImageFromArray(rect, ids, Quartz.kCGWindowImageDefault)
    return AppKit.NSBitmapImageRep.alloc().initWithCGImage_(image)


def save(rep, path):
    rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {}).writeToFile_atomically_(path, True)


def still(name, wins):
    save(grab(wins), os.path.join(OUT, name))


class Recording:
    """Grabs `wins()` inside `rect` FPS times a second, then encodes a silent H.264 MP4 and a poster frame."""

    def __init__(self, name, wins, rect):
        self.name, self.wins, self.rect = name, wins, rect
        self.dir = os.path.join(RAW, name)
        os.makedirs(self.dir)
        self.frames = []
        self.timer = ui.timer(1 / FPS, self._tick, repeats=True)

    def _tick(self):
        path = os.path.join(self.dir, f"{len(self.frames):05d}.png")
        save(grab(self.wins(), self.rect), path)
        self.frames.append((path, time.monotonic()))

    def finish(self):
        self.timer.invalidate()
        listing = os.path.join(self.dir, "frames.txt")
        ends = [at for _, at in self.frames[1:]] + [self.frames[-1][1] + 1 / FPS]
        with open(listing, "w") as f:
            f.writelines(f"file '{path}'\nduration {end - at:.4f}\n" for (path, at), end in zip(self.frames, ends))
            f.write(f"file '{self.frames[-1][0]}'\n")
        mp4 = os.path.join(OUT, f"{self.name}.mp4")
        # ffmpeg with fixed arguments built here; nothing comes from the user or the command line
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing,
                        "-vf", f"fps={FPS},scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "slow",
                        "-crf", "22", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", mp4], check=True)
        save(AppKit.NSBitmapImageRep.imageRepWithContentsOfFile_(self.frames[-1][0]),
             os.path.join(OUT, f"{self.name}-poster.png"))


def backdrop(frame, pad=40):
    """A plain panel behind a scene, the frame of its clip, so rounded windows never show the desktop."""
    rect = ((frame.origin.x - pad, frame.origin.y - pad), (frame.size.width + 2 * pad, frame.size.height + 2 * pad))
    win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        rect, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False)
    win.setBackgroundColor_(AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(*(c / 255 for c in BACKDROP), 1))
    win.setReleasedWhenClosed_(False)
    win.orderFrontRegardless()
    return win


class Document:
    """A plain Notes window holding sample text, standing in for the app the user selects in. Its selection is drawn
    with the system selection color, so it looks selected without this process taking focus."""

    def __init__(self, text, size=(680, 250), at=(260, 260)):
        style = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
                 | AppKit.NSWindowStyleMaskMiniaturizable | AppKit.NSWindowStyleMaskResizable)
        rect = ((at[0], SCREEN_H - at[1] - size[1]), size)
        win = self.win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False)
        win.setTitle_("Notes")
        win.setReleasedWhenClosed_(False)
        win.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameAqua))
        tv = self.text = AppKit.NSTextView.alloc().initWithFrame_(((0, 0), size))
        tv.setTextContainerInset_((26, 24))
        tv.setEditable_(False)
        tv.setSelectable_(False)
        tv.setRichText_(False)
        self.set(text)
        win.setContentView_(tv)
        win.orderFrontRegardless()

    def set(self, text):
        self.text.setString_(text)
        self.text.setFont_(AppKit.NSFont.systemFontOfSize_(16))
        self.text.setTextColor_(AppKit.NSColor.textColor())

    def select(self, start, length):
        lm, key = self.text.layoutManager(), AppKit.NSBackgroundColorAttributeName
        lm.removeTemporaryAttribute_forCharacterRange_(key, (0, len(self.text.string())))
        if length:
            lm.addTemporaryAttribute_value_forCharacterRange_(
                key, AppKit.NSColor.selectedTextBackgroundColor(), (start, length))

    def below(self, start, length, bar, gap=10):
        """Puts `bar` under the selection's last line, near its end, as the app does."""
        rect, _ = self.text.firstRectForCharacterRange_actualRange_((start + length - 1, 1), None)
        frame, doc = bar.frame(), self.win.frame()
        x = max(doc.origin.x + 18, min(rect.origin.x - 60, doc.origin.x + doc.size.width - frame.size.width - 18))
        bar.setFrameOrigin_((x, rect.origin.y - frame.size.height - gap))


def make_popup():
    p = mac_popup.Popup(1.0, on_play=noop, on_stop=noop, on_pause=noop, on_resume=noop, on_lang=noop, on_speed=noop,
                        on_settings=noop, on_source=noop, sources=lambda: [(n, None) for n in ("Cursor", "Codex")],
                        on_skip=noop, on_rewrite=noop, modes=lambda: MODES)
    p.configure_captions(CFG["caption_font_size"], CFG["caption_background"], CFG["caption_background_opacity"])
    p.set_source("Cursor")
    p.set_last_mode("Fix errors")
    return p


def show_bar(p, doc, start, length, editable=False, gap=10):
    p.show(doc.text.string(), 0, 0, editable=editable)
    p.cancel_hide()
    doc.below(start, length, p.win, gap)


def reading():
    doc, p = Document(READ_TEXT, size=(680, 270)), make_popup()
    sel = len(tour_copy.READ)
    doc.select(0, sel)
    show_bar(p, doc, 0, sel, gap=54)
    yield 1.0
    still("mac-bar.png", [p.win, doc.win])
    p.set_playing(True)
    p.set_caption(CAPTIONS[0])
    yield 1.0
    still("mac-playing.png", [p.caption.win, p.win, doc.win])
    p.set_playing(False)
    p.hide()
    doc.select(0, 0)
    back = backdrop(doc.win.frame())
    doc.win.orderFrontRegardless()
    yield 0.5
    rec = Recording("read", lambda: [p.caption.win, p.win, doc.win, back], top_left(back.frame()))
    yield 0.8
    doc.select(0, len(READ_TEXT))
    yield 0.7
    show_bar(p, doc, 0, len(READ_TEXT), gap=54)
    yield 1.2
    p.set_playing(True)
    p.set_loading(True)
    yield 0.6
    p.set_loading(False)
    for caption in CAPTIONS:
        p.set_caption(caption)
        yield 2.4 if len(caption) < 70 else 5.4
    p.set_playing(True, paused=True)
    yield 1.2
    p.set_playing(False)
    yield 1.0
    rec.finish()
    p.hide()
    back.close()
    doc.win.close()


def rewriting():
    doc, p = Document(tour_copy.REWRITE, size=(680, 170)), make_popup()
    toast, review = mac_toast.Toast(), mac_review.Review()
    doc.select(0, len(tour_copy.REWRITE))
    show_bar(p, doc, 0, len(tour_copy.REWRITE), editable=True)
    yield 1.0
    still("mac-writing.png", [p.win, doc.win])
    p.hide()
    doc.select(0, 0)
    f, r, gap = doc.win.frame(), review.win.frame(), 28
    review_at = (f.origin.x + (f.size.width - r.size.width) / 2, f.origin.y - gap)
    back = backdrop(AppKit.NSMakeRect(min(f.origin.x, review_at[0]), f.origin.y - gap - r.size.height,
                                      max(f.size.width, r.size.width), f.size.height + gap + r.size.height))
    doc.win.orderFrontRegardless()
    yield 0.5
    rec = Recording("rewrite", lambda: [review.win, toast.win, p.win, doc.win, back], top_left(back.frame()))
    yield 0.8
    doc.select(0, len(tour_copy.REWRITE))
    yield 0.7
    show_bar(p, doc, 0, len(tour_copy.REWRITE), editable=True)
    yield 1.4
    bar = p.win.frame()
    toast_at = (bar.origin.x, SCREEN_H - bar.origin.y + 6)
    p.set_rewriting(True)
    toast.show("Rewriting · Fix errors", *toast_at, [("Cancel", noop)], busy=True)
    yield 1.6
    fixed = tour_copy.sample_fix(tour_copy.REWRITE)
    p.set_rewriting(False)
    p.hide()
    doc.set(fixed)
    doc.select(0, 0)
    toast.show("Fix errors · Cursor", *toast_at,
               [("Undo", noop), ("Review", noop), ("Mode", [(m, noop, m == "Fix errors") for m in MODES]), ("×", noop)])
    toast.cancel_hide()
    yield 1.4
    still("mac-rewrite-toast.png", [toast.win, doc.win])
    yield 0.6
    review.show(tour_copy.REWRITE, fixed, noop, noop)
    review.win.setFrameTopLeftPoint_(review_at)
    yield 2.4
    review.toggle(1)
    yield 1.6
    review.toggle(1)
    yield 1.4
    rec.finish()
    still("mac-review.png", [review.win])
    toast.hide()
    review.hide()
    back.close()
    doc.win.close()


def settings_app():
    engines = [SimpleNamespace(name="Cursor", voices=(("ara", "Ara"), ("eve", "Eve")), default_voice="ara"),
               SimpleNamespace(name="Codex"),
               SimpleNamespace(name="Kokoro", voices=(("af_heart", "Heart (US)"),), default_voice="af_heart"),
               SimpleNamespace(name="macOS")]
    return SimpleNamespace(
        cfg=dict(CFG), statuses=STATUSES, writer_ready=True, chain=SimpleNamespace(engines=engines), stt_error=None,
        check_update=noop, update_state=lambda: ("", False), available_version=lambda: None,
        summarize_state=lambda: ("missing", None), summarize_action=noop, change=noop, play=noop,
        writer_statuses=WRITER_STATUSES, stt_statuses=STT_STATUSES,
        writer_models={"Cursor": [{"id": "fast", "label": "Fast", "efforts": []}],
                       "Codex": [{"id": "gpt-5", "efforts": ["low", "medium"]}]},
        fix_writer=noop, apply=noop, host=mock.Mock(mic_status=lambda: "ready"))


def settings_window():
    with mock.patch.object(ui, "show", lambda w: w.orderFrontRegardless()), \
            mock.patch.object(settings_ui.startup, "enabled", return_value=True), \
            mock.patch.object(stt_page, "stt_connection", return_value=("", "")):
        win = settings_ui.SettingsWindow(settings_app(), noop)
    win.win.orderFrontRegardless()
    yield 1.0
    still("mac-settings.png", [win.win])
    win.win.close()


class TourApp:
    """The parts of app_tour.App the tour window calls."""

    def __init__(self):
        self.cfg = dict(CFG)
        self.host = SimpleNamespace(confirm=lambda *a, **k: False)

    tour_action = tour_closed = apply = _selected = tour_next = tour_skip = tour_skip_all = tour_finish = noop


def tour_view_at(app, index):
    """Step `index` with every earlier step tried, as a first run that tries each feature."""
    progress = {step: tour.TRIED for step in tour.STEPS[:index]}
    state = SimpleNamespace(
        mac=True, trusted=True, stale=False, cfg=app.cfg, progress=progress, statuses=STATUSES,
        writer_statuses=WRITER_STATUSES, stt_statuses=STT_STATUSES, sandboxed=set(), voice_source="Cursor",
        writer_source="Cursor", stt_source="Cursor", has_practice_edit=False, review_text=None, sample_used=set())
    return tour_view.view(index, state)


def onboarding():
    with mock.patch.object(ui, "show", lambda w: w.orderFrontRegardless()):
        app = TourApp()
        t = mac_tour.TourWindow(app)
        place = (300, SCREEN_H - 160)
        stills = {"welcome": "tour-welcome.png", "voice": "tour-voice.png", "read": "tour-read.png",
                  "rewrite": "tour-rewrite.png", "live": "tour-live.png", None: "tour-done.png"}
        tallest = 0
        for step, name in stills.items():
            t.show(tour_view_at(app, tour.STEPS.index(step) if step else len(tour.STEPS)))
            t.win.setFrameTopLeftPoint_(place)
            yield 0.8
            still(name, [t.win])
            tallest = max(tallest, t.win.frame().size.height)
        f = t.win.frame()
        back = backdrop(AppKit.NSMakeRect(f.origin.x, place[1] - tallest, f.size.width, tallest))
        t.show(tour_view_at(app, 0))
        t.win.setFrameTopLeftPoint_(place)
        yield 0.5
        rec = Recording("tour", lambda: [t.win, back], top_left(back.frame()))
        yield 0.8
        for index in range(len(tour.STEPS) + 1):
            t.show(tour_view_at(app, index))
            t.win.setFrameTopLeftPoint_(place)
            yield 2.2
        rec.finish()
        back.close()
        t.close()


SCENES = {"reading": reading, "rewriting": rewriting, "settings": settings_window, "onboarding": onboarding}


def main():
    if sys.platform != "darwin":
        sys.exit("macOS only")
    unknown = [name for name in sys.argv[1:] if name not in SCENES]
    if unknown:
        sys.exit(f"unknown scene: {', '.join(unknown)}. Scenes: {', '.join(SCENES)}")
    chosen = [SCENES[name] for name in (sys.argv[1:] or SCENES)]
    os.makedirs(OUT, exist_ok=True)
    shutil.rmtree(RAW, ignore_errors=True)
    os.makedirs(RAW)
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    steps = (delay for scene in chosen for delay in scene())
    failure = []

    def stop():  # not AppHelper.stopEventLoop, which terminates the process before a failure can be raised
        app.stop_(None)
        wake = AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_
        app.postEvent_atStart_(wake(AppKit.NSEventTypeApplicationDefined, (0, 0), 0, 0, 0, None, 0, 0, 0), True)

    def tick():
        try:
            AppHelper.callLater(next(steps), tick)
        except StopIteration:
            stop()
        except Exception as e:  # raised again once the loop stops
            failure.append(e)
            stop()

    AppHelper.callLater(0.3, tick)
    app.run()
    if failure:
        raise failure[0]
    for name in sorted(os.listdir(OUT)):
        print(name)


if __name__ == "__main__":
    main()
