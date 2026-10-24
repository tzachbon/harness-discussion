"""Review panel B: the original on the left, the rewrite with its changes marked on the right.

Clicking a change puts that part back to the original, and clicking it again restores the rewrite.
Replace puts the result into the app, and Copy puts it on the clipboard. It never takes focus.
"""
import AppKit

from .. import writer
from . import ui
from .popup import _Button, _cocoa_y

HUNK = "harnessHunk"
WIDTH, HEIGHT = 720, 320


class _DiffView(AppKit.NSTextView):
    def acceptsFirstMouse_(self, event):
        return True

    def mouseDown_(self, event):
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        index = self.characterIndexForInsertionAtPoint_(point)
        storage = self.textStorage()
        for i in (index, index - 1):
            if 0 <= i < storage.length():
                value = storage.attribute_atIndex_effectiveRange_(HUNK, i, None)[0]
                if value is not None:
                    self.owner.toggle(int(value))
                    return


def _text_view(cls=AppKit.NSTextView):
    scroll = AppKit.NSScrollView.alloc().initWithFrame_(((0, 0), (WIDTH / 2 - 24, HEIGHT - 80)))
    scroll.setHasVerticalScroller_(True)
    scroll.setBorderType_(AppKit.NSNoBorder)
    view = cls.alloc().initWithFrame_(scroll.contentView().bounds())
    view.setEditable_(False)
    view.setSelectable_(False)
    view.setDrawsBackground_(False)
    view.setTextContainerInset_((6, 6))
    view.setAutoresizingMask_(AppKit.NSViewWidthSizable)
    scroll.setDocumentView_(view)
    for anchor, size in ((scroll.widthAnchor(), WIDTH / 2 - 24), (scroll.heightAnchor(), HEIGHT - 80)):
        anchor.constraintGreaterThanOrEqualToConstant_(size).setActive_(True)
    return scroll, view


def _button(title, fn, primary=False):
    b = _Button.alloc().init()
    b.setBezelStyle_(AppKit.NSBezelStyleRounded)
    b.setTitle_(title)
    if primary:
        b.setKeyEquivalent_("\r")
    return ui.bind(b, lambda _s: fn())


class Review:
    def __init__(self):
        self.pairs, self.reverted, self.on_replace, self.on_copy = [], set(), None, None
        left_scroll, self.left = _text_view()
        right_scroll, self.right = _text_view(_DiffView)
        self.right.owner = self
        self.replace_btn = _button("Replace", lambda: self._done(self.on_replace), primary=True)
        self.copy_btn = _button("Copy", lambda: self._done(self.on_copy))
        hint = ui.label("Click a change to keep the original there.", 11, secondary=True)
        panes = ui.stack([left_scroll, right_scroll], vertical=False, spacing=12)
        buttons = ui.stack([hint, AppKit.NSView.alloc().init(), _button("Close", self.hide), self.copy_btn,
                            self.replace_btn], vertical=False, spacing=8)
        content = ui.stack([panes, buttons], spacing=12, inset=14)
        style = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable | AppKit.NSWindowStyleMaskResizable
                 | AppKit.NSWindowStyleMaskNonactivatingPanel | AppKit.NSWindowStyleMaskUtilityWindow)
        p = self.win = ui.Panel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (WIDTH, HEIGHT)), style, AppKit.NSBackingStoreBuffered, False)
        p.setTitle_("Review rewrite")
        p.setContentView_(content)
        p.setLevel_(AppKit.NSStatusWindowLevel)
        p.setFloatingPanel_(True)
        p.setHidesOnDeactivate_(False)
        p.setReleasedWhenClosed_(False)
        p.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                                 | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)

    @property
    def visible(self):
        return bool(self.win.isVisible())

    def contains(self, x, y):
        f = self.win.frame()
        top = _cocoa_y(f.origin.y + f.size.height)
        return self.visible and f.origin.x <= x < f.origin.x + f.size.width and top <= y < top + f.size.height

    @property
    def result(self):
        return writer.merge(self.pairs, self.reverted)

    def show(self, original, rewritten, on_replace, on_copy):
        """on_replace(text) is None when the original place is gone: then only Copy works."""
        self.pairs, self.reverted = writer.hunks(original, rewritten), set()
        self.on_replace, self.on_copy = on_replace, on_copy
        self.replace_btn.setEnabled_(on_replace is not None)
        self.left.textStorage().setAttributedString_(self._plain(original))
        self._render()
        self.win.center()
        self.win.orderFrontRegardless()

    def toggle(self, index):
        self.reverted ^= {index}
        self._render()

    def _plain(self, text, **extra):
        attrs = {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(13),
                 AppKit.NSForegroundColorAttributeName: AppKit.NSColor.labelColor(), **extra}
        return AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs)

    def _render(self):
        out = AppKit.NSMutableAttributedString.alloc().init()
        for i, (old, new) in enumerate(self.pairs):
            if old == new:
                out.appendAttributedString_(self._plain(old))
            elif i in self.reverted:
                out.appendAttributedString_(self._plain(old or "∅", **{
                    HUNK: i, AppKit.NSUnderlineStyleAttributeName: AppKit.NSUnderlineStyleSingle
                    | AppKit.NSUnderlinePatternDot}))
            else:
                if old:
                    out.appendAttributedString_(self._plain(old, **{
                        HUNK: i, AppKit.NSStrikethroughStyleAttributeName: AppKit.NSUnderlineStyleSingle,
                        AppKit.NSForegroundColorAttributeName: AppKit.NSColor.systemRedColor()}))
                if new:
                    out.appendAttributedString_(self._plain(new, **{
                        HUNK: i, AppKit.NSBackgroundColorAttributeName:
                        AppKit.NSColor.systemGreenColor().colorWithAlphaComponent_(0.25)}))
        self.right.textStorage().setAttributedString_(out)

    def _done(self, fn):
        if fn:
            text = self.result
            self.hide()
            fn(text)

    def hide(self):
        self.win.orderOut_(None)
