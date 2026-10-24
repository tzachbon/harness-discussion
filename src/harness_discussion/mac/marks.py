"""Check as you type marks: 2 pt underline panels that ignore the mouse, and a badge with the suggestion count.

Clicks on an underline reach the app under it, so its caret moves as usual; the global mouse monitor reports the
click, and `hit` finds which suggestion's text it landed on. Rectangles arrive in top-left screen points.
"""
import AppKit

from . import ui
from .bar_parts import _cocoa_y

STRIP = 2
REMOVED = AppKit.NSColor.systemRedColor()

def _panel(color, ignores_mouse):
    style = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
    p = ui.Panel.alloc().initWithContentRect_styleMask_backing_defer_(((0, 0), (10, STRIP)), style,
                                                                       AppKit.NSBackingStoreBuffered, False)
    p.setLevel_(AppKit.NSStatusWindowLevel)
    p.setFloatingPanel_(True)
    p.setHidesOnDeactivate_(False)
    p.setHasShadow_(False)
    p.setOpaque_(True)
    p.setBackgroundColor_(color)
    p.setIgnoresMouseEvents_(ignores_mouse)
    p.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                             | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
                             | AppKit.NSWindowCollectionBehaviorTransient)
    return p

class Marks:
    def __init__(self):
        self._strips, self._words, self._badge_at = [], {}, None
        self.badge = _panel(AppKit.NSColor.controlAccentColor(), False)
        self.count = ui.label("", size=11, bold=True)
        self.count.setTextColor_(AppKit.NSColor.whiteColor())
        self.count.setAlignment_(AppKit.NSTextAlignmentCenter)
        self.badge.setContentView_(self.count)

    def _strip(self, i):
        while len(self._strips) <= i:
            self._strips.append(_panel(REMOVED, True))
        return self._strips[i]

    def show(self, strips, badge):
        """strips: [(key, (left, top, right, bottom))] of each underlined line of text. badge: (x, y, count) or None,
        with x the right edge it hangs from."""
        self._words = {}
        for i, (key, (left, top, right, bottom)) in enumerate(strips):
            self._words.setdefault(key, []).append((left, top, right, bottom))
            p = self._strip(i)
            p.setFrame_display_(((left, _cocoa_y(bottom + 1)), (max(1, right - left), STRIP)), True)
            p.orderFrontRegardless()
        for p in self._strips[len(strips):]:
            p.orderOut_(None)
        if badge:
            x, y, count = badge
            self.count.setStringValue_(str(count))
            width, height = max(22, 10 + 8 * len(str(count))), 18
            self._badge_at = (x - width - 4, y + 4, x - 4, y + 4 + height)
            self.badge.setFrame_display_(((self._badge_at[0], _cocoa_y(self._badge_at[3])), (width, height)), True)
            self.badge.orderFrontRegardless()
        else:
            self._badge_at = None
            self.badge.orderOut_(None)

    def hide(self):
        self.show([], None)

    def hit(self, x, y):
        """"badge", the key of the suggestion whose text (x, y) is on, or None."""
        b = self._badge_at
        if b and b[0] <= x < b[2] and b[1] <= y < b[3]:
            return "badge"
        for key, rects in self._words.items():
            if any(left <= x < right and top <= y < bottom for left, top, right, bottom in rects):
                return key
        return None
