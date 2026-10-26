"""Small AppKit helpers: Python callables as button targets, labels, stacks, cards, images and windows."""
import functools
import io

import AppKit
import objc
from Foundation import NSData, NSObject

from .. import hotkeys, icons

_keep = []  # AppKit holds targets weakly


class _Action(NSObject):
    def initWithFn_(self, fn):
        self = objc.super(_Action, self).init()
        if self is not None:
            self.fn = fn
        return self

    def run_(self, sender):
        self.fn(sender)


def target(fn):
    """An object whose `run:` selector calls fn(sender)."""
    t = _Action.alloc().initWithFn_(fn)
    _keep.append(t)
    return t


def bind(control, fn):
    control.setTarget_(target(fn))
    control.setAction_("run:")
    return control


def label(text, size=13, bold=False, secondary=False, width=None):
    field = AppKit.NSTextField.wrappingLabelWithString_(text) if width else AppKit.NSTextField.labelWithString_(text)
    font = AppKit.NSFont.boldSystemFontOfSize_(size) if bold else AppKit.NSFont.systemFontOfSize_(size)
    field.setFont_(font)
    if secondary:
        field.setTextColor_(AppKit.NSColor.secondaryLabelColor())
    if width:
        field.setPreferredMaxLayoutWidth_(width)
        field.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
    return field


def button(title, fn, primary=False):
    b = bind(AppKit.NSButton.buttonWithTitle_target_action_(title, None, None), lambda _s: fn())
    if primary:
        b.setKeyEquivalent_("\r")
    return b


def checkbox(title, value, fn):
    b = bind(AppKit.NSButton.checkboxWithTitle_target_action_(title, None, None),
             lambda s: fn(bool(s.state())))
    b.setState_(1 if value else 0)
    return b


def stack(views, vertical=True, spacing=8, inset=0):
    s = AppKit.NSStackView.stackViewWithViews_(views)
    s.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationVertical if vertical
                      else AppKit.NSUserInterfaceLayoutOrientationHorizontal)
    s.setAlignment_(AppKit.NSLayoutAttributeLeading if vertical else AppKit.NSLayoutAttributeCenterY)
    s.setSpacing_(spacing)
    if inset:
        s.setEdgeInsets_((inset, inset, inset, inset))
    return s


def window(title, content):
    style = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable)
    w = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0, 0), (100, 100)), style, AppKit.NSBackingStoreBuffered, False)
    w.setTitle_(title)
    w.setReleasedWhenClosed_(False)
    w.setContentView_(content)
    w.setContentSize_(content.fittingSize())
    w.center()
    return w


def show(w):
    AppKit.NSApp.activateIgnoringOtherApps_(True)
    w.makeKeyAndOrderFront_(None)


def timer(seconds, fn, repeats=False):
    """Main-thread timer. Common modes keep it firing while a menu is open."""
    t = AppKit.NSTimer.timerWithTimeInterval_repeats_block_(seconds, repeats, lambda _t: fn())
    AppKit.NSRunLoop.mainRunLoop().addTimer_forMode_(t, AppKit.NSRunLoopCommonModes)
    return t


def symbol(name, description=""):
    return AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, description)


def pil_image(img, points):
    """A PIL image as an NSImage `points` wide, its pixels kept for Retina screens."""
    data = io.BytesIO()
    img.save(data, "PNG")
    raw = data.getvalue()
    image = AppKit.NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(raw, len(raw)))
    image.setSize_((points, points))
    return image


@functools.lru_cache(maxsize=None)
def source_icon(name, points=16):
    return pil_image(icons.source_image(name, points * 2), points)


def image_view(image, points):
    view = AppKit.NSImageView.imageViewWithImage_(image)
    for anchor in (view.widthAnchor(), view.heightAnchor()):
        anchor.constraintEqualToConstant_(points).setActive_(True)
    return view


def card(views, spacing=10, width=None):
    """A rounded panel that groups a section's rows, as System Settings does."""
    box = AppKit.NSBox.alloc().init()
    if width:
        box.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
    box.setBoxType_(AppKit.NSBoxCustom)
    box.setTitlePosition_(AppKit.NSNoTitle)
    box.setCornerRadius_(10)
    box.setBorderWidth_(0.5)
    box.setBorderColor_(AppKit.NSColor.separatorColor())
    box.setFillColor_(AppKit.NSColor.controlBackgroundColor())
    box.setContentViewMargins_((0, 0))
    inner = stack(views, spacing=spacing)
    inner.setTranslatesAutoresizingMaskIntoConstraints_(False)
    content = box.contentView()
    content.addSubview_(inner)
    for a, inset in (("leadingAnchor", 14), ("trailingAnchor", -14), ("topAnchor", 12), ("bottomAnchor", -12)):
        getattr(inner, a)().constraintEqualToAnchor_constant_(getattr(content, a)(), inset).setActive_(True)
    return box


class Keys:
    """A shortcut drawn as keycaps, one rounded chip per key, like an HTML <kbd> group. show(spec) draws the keys;
    say(text) shows plain text instead, such as "Press keys…". `view` goes in a stack; `text` is what it reads as."""

    def __init__(self, size=13, bold=True):
        self.size, self.bold, self.text = size, bold, ""
        self.view = stack([], vertical=False, spacing=4)
        self.view.setAccessibilityElement_(True)  # VoiceOver reads the shortcut once, not cap by cap
        self.view.setAccessibilityRole_(AppKit.NSAccessibilityStaticTextRole)

    def show(self, spec, after=""):
        names = hotkeys.keys(spec)
        if not names:
            return self.say("Off")
        after_label = [label(after, self.size, self.bold)] if after else []
        self._fill(hotkeys.label(spec) + after, [self._cap(name) for name in names] + after_label)

    def say(self, text):
        self._fill(text, [label(text, self.size, self.bold)])

    def _fill(self, text, views):
        for view in list(self.view.arrangedSubviews()):
            self.view.removeArrangedSubview_(view)
            view.removeFromSuperview()
        for view in views:
            self.view.addArrangedSubview_(view)
        self.text = text
        self.view.setAccessibilityLabel_(text)

    def _cap(self, name):
        box = AppKit.NSBox.alloc().init()
        box.setBoxType_(AppKit.NSBoxCustom)
        box.setTitlePosition_(AppKit.NSNoTitle)
        box.setCornerRadius_(4)
        box.setBorderWidth_(0)
        box.setFillColor_(AppKit.NSColor.quaternaryLabelColor())
        box.setContentViewMargins_((0, 0))
        text = label(name, self.size - 2, secondary=True)
        text.setTranslatesAutoresizingMaskIntoConstraints_(False)
        content = box.contentView()
        content.addSubview_(text)
        for a in ("centerXAnchor", "centerYAnchor"):
            getattr(text, a)().constraintEqualToAnchor_(getattr(content, a)()).setActive_(True)
        side = self.size + 7  # fixed sizes: a box has no intrinsic size, so a stack could stretch it
        box.heightAnchor().constraintEqualToConstant_(side).setActive_(True)
        box.widthAnchor().constraintEqualToConstant_(max(side, text.fittingSize().width + 10)).setActive_(True)
        return box

def switch(value, fn):
    s = bind(AppKit.NSSwitch.alloc().init(), lambda s: fn(bool(s.state())))
    s.setState_(1 if value else 0)
    s.setControlSize_(AppKit.NSControlSizeSmall)
    return s


def symbol_button(name, description, fn, size=13):
    """A borderless SF Symbol button for small row actions."""
    b = bind(AppKit.NSButton.buttonWithImage_target_action_(symbol(name, description), None, None), lambda _s: fn())
    b.setBordered_(False)
    b.setSymbolConfiguration_(AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        size, AppKit.NSFontWeightSemibold))
    b.setContentTintColor_(AppKit.NSColor.secondaryLabelColor())
    b.setAccessibilityLabel_(description)
    return b


def option(title, detail, value, fn, width):
    """A System Settings row: a title with a line of detail on the left, a switch on the right. (row, switch)."""
    toggle = switch(value, fn)
    toggle.setAccessibilityLabel_(title)
    text = stack([label(title, 13), label(detail, 11, secondary=True, width=width - 70)], spacing=2)
    filler = AppKit.NSView.alloc().init()
    filler.setContentHuggingPriority_forOrientation_(1, AppKit.NSLayoutConstraintOrientationHorizontal)
    row = stack([text, filler, toggle], vertical=False)
    row.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
    return row, toggle


def tile(name, color, points=22):
    """An SF Symbol in white on a colored rounded square, as System Settings marks its panes."""
    glyph = symbol(name).imageWithSymbolConfiguration_(
        AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(points * 0.5, AppKit.NSFontWeightSemibold)
        .configurationByApplyingConfiguration_(
            AppKit.NSImageSymbolConfiguration.configurationWithHierarchicalColor_(AppKit.NSColor.whiteColor())))

    def draw(rect):
        color.setFill()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, points * 0.24, points * 0.24).fill()
        w, h = glyph.size()
        glyph.drawInRect_((((rect.size.width - w) / 2, (rect.size.height - h) / 2), (w, h)))
        return True
    return AppKit.NSImage.imageWithSize_flipped_drawingHandler_((points, points), False, draw)


def inset(image, left):
    """The image with `left` points of clear space before it."""
    w, h = image.size()

    def draw(rect):
        image.drawInRect_(((left, 0), (w, h)))
        return True
    return AppKit.NSImage.imageWithSize_flipped_drawingHandler_((w + left, h), False, draw)


class Panel(AppKit.NSPanel):
    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


def cg(color):
    return color.CGColor()
