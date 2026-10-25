"""The Get Started tour window on macOS. It draws tour_view's dict (tour_steps) and never decides copy; its shortcut
rows are the Settings recorder (ReadingPage)."""
import os

import AppKit
import objc
from Foundation import NSObject

from . import permission, selection, tour_steps, ui
from .practice_field import rgb
from .reading_page import ReadingPage
from .tour_steps import INSET, WIDTH

HEIGHT = 540
TOP = 34  # clears the traffic lights, which the content runs under
MAIN = ("tile", "title", "lead", "shortcuts", "rows", "quote", "box", "status", "dest", "keep", "done")
REDRAWN = ("head", "tile", "title", "lead", "rows", "quote", "status", "dest", "keep", "done", "foot")  # by refresh
PLAIN = AppKit.NSEventModifierFlagCommand | AppKit.NSEventModifierFlagOption | AppKit.NSEventModifierFlagControl


class TourNSWindow(AppKit.NSWindow):
    def performKeyEquivalent_(self, event):
        box = self.tour.box
        if (box is not None and self.firstResponder() == box.text_view and not event.modifierFlags() & PLAIN
                and event.charactersIgnoringModifiers() == "\r"):
            return False  # Return in the box types a newline instead of pressing the default button, Next
        return objc.super(TourNSWindow, self).performKeyEquivalent_(event)


class TourDelegate(NSObject):
    def initWithTour_(self, tour):
        self = objc.super(TourDelegate, self).init()
        if self is not None:
            self.tour = tour
        return self

    def windowWillClose_(self, _note):
        self.tour._window_closed()


def _inside(rect, x, y):
    left, top, right, bottom = rect
    return left <= x < right and top <= y < bottom


class TourWindow(ReadingPage):
    def __init__(self, app):
        self.app, self.box, self.field, self.keep_switch, self.done_rows = app, None, None, None, []
        # not `_closed`: ReadingPage keeps its close-observer token there and never clears it
        self._recorder, self._closing_by_app, self._poll, self._waited = None, False, None, False
        self.shortcut_labels, self.shortcut_hints, self._shown = {}, {}, {}
        self.slots = {name: ui.stack([], spacing=8) for name in (*MAIN, "head", "side", "foot")}
        main = ui.stack([self.slots[n] for n in MAIN], spacing=12)
        body = ui.stack([main, self.slots["side"]], vertical=False, spacing=tour_steps.GAP)
        body.setAlignment_(AppKit.NSLayoutAttributeTop)
        rest = AppKit.NSView.alloc().init()  # takes the spare height, so the footer sits at the bottom
        rest.setContentHuggingPriority_forOrientation_(1, AppKit.NSLayoutConstraintOrientationVertical)
        self.content = ui.stack([self.slots["head"], body, rest, self.slots["foot"]], spacing=14)
        self.content.setEdgeInsets_((TOP, INSET, 18, INSET))
        self.content.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        self.content.heightAnchor().constraintGreaterThanOrEqualToConstant_(HEIGHT).setActive_(True)
        style = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
                 | AppKit.NSWindowStyleMaskMiniaturizable | AppKit.NSWindowStyleMaskFullSizeContentView)
        win = self.win = TourNSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (WIDTH, HEIGHT)), style, AppKit.NSBackingStoreBuffered, False)
        win.tour = self
        win.setTitle_("Harness Discussion · Get started")
        win.setTitlebarAppearsTransparent_(True)
        win.setTitleVisibility_(AppKit.NSWindowTitleHidden)
        win.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua))
        win.setBackgroundColor_(rgb(0x1c, 0x1c, 0x1f))
        win.setReleasedWhenClosed_(False)
        win.setContentView_(self.content)
        self._delegate = TourDelegate.alloc().initWithTour_(self)  # AppKit holds delegates weakly
        win.setDelegate_(self._delegate)

    @property
    def cfg(self):
        return self.app.cfg

    def _set(self, key, spec):  # the recorder has already refused a shortcut another row uses
        self.app.tour_action("shortcut", (key, spec))

    @property
    def alive(self):
        return bool(self.win.isVisible())

    def show(self, view):
        self._stop_recording()
        self._stop_poll()
        self._closing_by_app = False
        self.shortcut_labels, self.shortcut_hints, self._shown = {}, {}, {}
        done = bool(view["done"])
        width = tour_steps.SIDE if done else tour_steps.COL
        self._fill("side" if done else "shortcuts", tour_steps.shortcuts(self, view, width))
        self._fill("shortcuts" if done else "side", [])
        self._fill("box", tour_steps.box(self, view, tour_steps.COL))
        self._draw(view)
        if not self.win.isVisible():
            self.win.center()
        ui.show(self.win)
        if self.box is not None:
            self.win.makeFirstResponder_(self.box.text_view)
        if view["step"] == "welcome" and not view["ready"]:
            self._waited = False
            self._poll = ui.timer(1, self._poll_trust, repeats=True)

    def refresh(self, view):
        """Redraws in place. The box and the shortcut rows stay; a row's label changes only with its setting, so a
        "Used by …" message or a recording survives."""
        self._draw(view)
        if self._recorder:
            return
        for key in self.shortcut_labels:
            if self.cfg[key] != self._shown.get(key):
                self._show_shortcut(key)
                self._shown[key] = self.cfg[key]

    def focus(self):
        ui.show(self.win)

    def close(self):
        self._stop_recording()
        self._stop_poll()
        self._closing_by_app = True
        self.win.close()

    def frontmost(self):  # any thread
        return selection._frontmost_pid() == os.getpid()

    def hit(self, x, y):
        """A screen point (top-left origin): "box", "window" or None."""
        if not self.alive:
            return None
        if self.box is not None and _inside(self.box.screen_rect(), x, y):
            return "box"
        f = self.win.frame()
        height = AppKit.NSScreen.screens()[0].frame().size.height
        top = height - f.origin.y - f.size.height
        return "window" if _inside((f.origin.x, top, f.origin.x + f.size.width, top + f.size.height), x, y) else None

    def _draw(self, view):
        width = tour_steps.LEFT if view["done"] else tour_steps.COL
        for name in REDRAWN:
            self._fill(name, getattr(tour_steps, name)(self, view, width))
        f = self.win.frame()
        self.win.setContentSize_(self.content.fittingSize())
        self.win.setFrameTopLeftPoint_((f.origin.x, f.origin.y + f.size.height))  # grows down, not up

    def _fill(self, name, views):
        slot = self.slots[name]
        for view in list(slot.arrangedSubviews()):
            slot.removeArrangedSubview_(view)
            view.removeFromSuperview()
        for view in views:
            slot.addArrangedSubview_(view)
        slot.setHidden_(not views)

    def _poll_trust(self):
        """Step 1 ticks itself when macOS grants access, then comes back in front of System Settings: the app has
        no Dock icon to click."""
        if not permission.trusted():
            self._waited = True
            return
        self._stop_poll()
        self.app.tour_mark("welcome")
        if self._waited:
            ui.show(self.win)

    def _stop_poll(self):
        if self._poll:
            self._poll.invalidate()
            self._poll = None

    def _window_closed(self):
        self._stop_poll()
        if not self._closing_by_app:
            self.app.tour_closed()
