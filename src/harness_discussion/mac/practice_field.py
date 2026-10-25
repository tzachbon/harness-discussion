"""The tour's practice box on macOS: an NSTextView the Writer edits through practice_box.Box. NSTextView offsets
are already UTF-16."""
import AppKit
import objc

from .. import live
from ..practice_box import Box
from . import trigger

BOX_BG = (0x15, 0x15, 0x17)


def rgb(r, g, b, a=1.0):
    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255, g / 255, b / 255, a)


class PracticeTextView(AppKit.NSTextView):
    def mouseDown_(self, event):
        objc.super(PracticeTextView, self).mouseDown_(event)  # returns after the drag-tracking loop ends at mouse-up
        self.practice._after_mouse()


class PracticeField(Box):
    def __init__(self, app, width, height=96):
        self.app = app
        self.view = scroll = AppKit.NSScrollView.alloc().initWithFrame_(((0, 0), (width, height)))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(AppKit.NSNoBorder)
        scroll.setBackgroundColor_(rgb(*BOX_BG))
        scroll.setWantsLayer_(True)
        scroll.layer().setCornerRadius_(8)
        scroll.layer().setBorderWidth_(1)
        scroll.layer().setBorderColor_(rgb(255, 255, 255, 0.08).CGColor())
        for anchor, size in ((scroll.widthAnchor(), width), (scroll.heightAnchor(), height)):
            anchor.constraintEqualToConstant_(size).setActive_(True)
        size = scroll.contentSize()
        tv = self.text_view = PracticeTextView.alloc().initWithFrame_(((0, 0), size))
        tv.practice = self
        tv.setMinSize_((0, size.height))
        tv.setMaxSize_((1e7, 1e7))
        tv.setVerticallyResizable_(True)
        tv.setHorizontallyResizable_(False)
        tv.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        tv.textContainer().setWidthTracksTextView_(True)
        tv.setTextContainerInset_((10, 10))
        tv.setRichText_(False)
        tv.setAllowsUndo_(True)
        # typed text stays as typed: the samples' mistakes are the practice, and our underlines are the only ones
        for setter in ("setAutomaticQuoteSubstitutionEnabled_", "setAutomaticDashSubstitutionEnabled_",
                       "setAutomaticTextReplacementEnabled_", "setAutomaticSpellingCorrectionEnabled_",
                       "setContinuousSpellCheckingEnabled_", "setGrammarCheckingEnabled_"):
            getattr(tv, setter)(False)
        tv.setDrawsBackground_(True)
        tv.setBackgroundColor_(rgb(*BOX_BG))
        tv.setInsertionPointColor_(AppKit.NSColor.whiteColor())
        tv.setSelectedTextAttributes_({AppKit.NSBackgroundColorAttributeName: rgb(10, 132, 255, 0.35)})
        scroll.setDocumentView_(tv)

    def load(self, text, select):
        """The step's sample; select is "all", "end" or None, as the view dict says."""
        tv = self.text_view
        tv.setString_(text)
        tv.setFont_(AppKit.NSFont.systemFontOfSize_(15))
        tv.setTextColor_(AppKit.NSColor.whiteColor())
        if tv.undoManager():
            tv.undoManager().removeAllActions()  # ⌘Z must not bring back the previous step's text
        n = live.units(text)
        self._select(*{"all": (0, n), "end": (n, n)}.get(select, (0, 0)))

    def text(self):
        return str(self.text_view.string())

    def selection(self):
        r = self.text_view.selectedRange()
        return r.location, r.location + r.length

    def _set(self, start, end, new):
        tv, rng = self.text_view, (start, end - start)
        if tv.shouldChangeTextInRange_replacementString_(rng, new):
            text = AppKit.NSAttributedString.alloc().initWithString_attributes_(new, tv.typingAttributes())
            tv.textStorage().replaceCharactersInRange_withAttributedString_(rng, text)
            tv.didChangeText()
        tv.setSelectedRange_((start + live.units(new), 0))

    def _select(self, start, end):
        self.text_view.setSelectedRange_((start, end - start))

    def _rects(self, start, end):
        # shortcut: a span that wraps returns one bounding rect; samples are one line, split by line fragments if
        # users report misplaced underlines
        tv = self.text_view
        lm = tv.layoutManager()
        glyphs, _ = lm.glyphRangeForCharacterRange_actualCharacterRange_((start, end - start), None)
        r = lm.boundingRectForGlyphRange_inTextContainer_(glyphs, tv.textContainer())
        o = tv.textContainerOrigin()
        r = ((r.origin.x + o.x, r.origin.y + o.y), (r.size.width, r.size.height))
        if not AppKit.NSIntersectsRect(r, tv.visibleRect()):
            return []
        return [self._screen(tv.convertRect_toView_(r, None))]

    def screen_rect(self):
        return self._screen(self.view.convertRect_toView_(self.view.bounds(), None))

    def _screen(self, rect):
        """A rect in window coordinates as (left, top, right, bottom) on screen, top-left origin."""
        win = self.view.window()
        if win is None:
            return 0, 0, 0, 0
        r = win.convertRectToScreen_(rect)
        height = AppKit.NSScreen.screens()[0].frame().size.height
        return r.origin.x, height - r.origin.y - r.size.height, r.origin.x + r.size.width, height - r.origin.y

    def _after_mouse(self):
        s, e = self.selection()
        if s != e:
            self.app._selected(*trigger.top_left(AppKit.NSEvent.mouseLocation()))
