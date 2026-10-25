"""The pieces of the floating bar: its buttons, chips, divider, menus, and screen math."""
import AppKit
import objc

from . import ui

REWRITE_PAD = 12


class _Button(AppKit.NSButton):
    def acceptsFirstMouse_(self, event):
        return True  # one click works while another app stays active

    def alignmentRectInsets(self):
        # AppKit pads image buttons 2 points above and below their constrained size, which a layer background shows
        return (0, 0, 0, 0) if not self.isBordered() else objc.super(_Button, self).alignmentRectInsets()


class _Hover(AppKit.NSView):
    """Content view that reports mouse enter and exit, so the idle timer pauses while hovered."""

    def initWithOwner_(self, owner):
        self = objc.super(_Hover, self).init()
        if self is not None:
            self.owner = owner
        return self

    def updateTrackingAreas(self):
        objc.super(_Hover, self).updateTrackingAreas()
        for area in list(self.trackingAreas()):
            self.removeTrackingArea_(area)
        options = (AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveAlways
                   | AppKit.NSTrackingInVisibleRect)
        self.addTrackingArea_(AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), options, self, None))

    def mouseEntered_(self, event):
        self.owner.cancel_hide()

    def mouseExited_(self, event):
        self.owner.schedule_hide()


def _icon_button(name, description, fn, tint=None, size=26, fill=None):
    """A system symbol button, a circle of `fill` when one is given."""
    b = _Button.alloc().init()
    b.setBezelStyle_(AppKit.NSBezelStyleRegularSquare)
    b.setBordered_(False)
    b.setImage_(ui.symbol(name, description))
    b.setImagePosition_(AppKit.NSImageOnly)
    b.setImageScaling_(AppKit.NSImageScaleProportionallyDown)
    b.setContentTintColor_(tint or AppKit.NSColor.colorWithWhite_alpha_(1, 0.6))
    b.setToolTip_(description)
    b.setAccessibilityLabel_(description)
    b.widthAnchor().constraintEqualToConstant_(size).setActive_(True)
    b.heightAnchor().constraintEqualToConstant_(size).setActive_(True)
    if fill:
        b.setWantsLayer_(True)
        b.layer().setCornerRadius_(size / 2)
        b.layer().setBackgroundColor_(ui.cg(fill))
    return ui.bind(b, lambda s: fn(s))


def _play_button(fn):
    """Play/Pause: a filled circle in the system accent color, the one control that stands out."""
    return _icon_button("play.fill", "Play", lambda _s: fn(), tint=AppKit.NSColor.whiteColor(), size=30,
                        fill=AppKit.NSColor.controlAccentColor())


def _text_button(fn, title, titles, icon=lambda _t: None):
    """A rounded chip that opens a menu, as wide as its widest title so a pick never moves the bar."""
    b = _Button.alloc().init()
    b.setBezelStyle_(AppKit.NSBezelStyleRegularSquare)
    b.setBordered_(False)
    b.setWantsLayer_(True)
    b.layer().setCornerRadius_(13)
    b.layer().setBackgroundColor_(ui.cg(AppKit.NSColor.colorWithWhite_alpha_(1, 0.1)))
    b.heightAnchor().constraintEqualToConstant_(26).setActive_(True)
    widest = 0
    for option in titles:
        _set_title(b, option, icon(option))
        widest = max(widest, b.cell().cellSize().width)
    b.widthAnchor().constraintEqualToConstant_(widest + 24).setActive_(True)
    _set_title(b, title, icon(title) if title else None)
    return ui.bind(b, lambda s: fn(s))


def _set_title(button, title, image=None):
    """`[icon] title ▾` with a dimmed chevron, the icon inline so it centers with the title."""
    font = AppKit.NSFont.systemFontOfSize_weight_(12, AppKit.NSFontWeightMedium)
    text = AppKit.NSMutableAttributedString.alloc().init()
    if image:
        icon = AppKit.NSTextAttachment.alloc().init()
        icon.setImage_(image)
        size = image.size()
        icon.setBounds_(((0, (font.capHeight() - size.height) / 2), (size.width, size.height)))
        text.appendAttributedString_(AppKit.NSAttributedString.attributedStringWithAttachment_(icon))
        title = " " + title
    text.appendAttributedString_(AppKit.NSAttributedString.alloc().initWithString_attributes_(
        title + " ", {AppKit.NSFontAttributeName: font,
                       AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor()}))
    text.appendAttributedString_(AppKit.NSAttributedString.alloc().initWithString_attributes_(
        "▾", {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(9),
              AppKit.NSForegroundColorAttributeName: AppKit.NSColor.colorWithWhite_alpha_(1, 0.55),
              AppKit.NSBaselineOffsetAttributeName: 1}))
    button.setAttributedTitle_(text)


def _rewrite_button(fn):
    """✎ and the mode name: the primary control of the writing layout."""
    b = _Button.alloc().init()
    b.setBezelStyle_(AppKit.NSBezelStyleRegularSquare)
    b.setBordered_(False)
    b.setWantsLayer_(True)
    b.layer().setCornerRadius_(15)
    b.layer().setBackgroundColor_(ui.cg(AppKit.NSColor.controlAccentColor()))
    b.heightAnchor().constraintEqualToConstant_(30).setActive_(True)  # as tall as ▶, so both follow the bar's curve
    b.widthAnchor().constraintEqualToConstant_(0).setActive_(True)  # sized to each title by _set_rewrite_title
    return ui.bind(b, lambda _s: fn())


def _set_rewrite_title(button, title):
    """The drawn title, not its advance box, centered with the same padding on both sides."""
    font = AppKit.NSFont.systemFontOfSize_weight_(12, AppKit.NSFontWeightSemibold)
    text = AppKit.NSMutableAttributedString.alloc().initWithString_attributes_(
        f"✎ {title}", {AppKit.NSFontAttributeName: font,
                       AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor()})
    ink = text.boundingRectWithSize_options_((1e4, 1e4), AppKit.NSStringDrawingUsesDeviceMetrics)
    left, right = ink.origin.x, text.size().width - ink.origin.x - ink.size.width
    # a kern on the last glyph trims its trailing blank to match the leading one
    text.addAttribute_value_range_(AppKit.NSKernAttributeName, left - right, (text.length() - 1, 1))
    button.setAttributedTitle_(text)
    width = next(c for c in button.constraints() if c.firstAttribute() == AppKit.NSLayoutAttributeWidth)
    width.setConstant_(text.size().width + 2 * REWRITE_PAD)


def _divider():
    line = AppKit.NSBox.alloc().init()
    line.setBoxType_(AppKit.NSBoxSeparator)
    line.heightAnchor().constraintEqualToConstant_(18).setActive_(True)
    return line


def _cocoa_y(top):
    """Top-left y of the main screen to AppKit's bottom-left y."""
    return AppKit.NSScreen.screens()[0].frame().size.height - top


def _screen_at(x, y):
    point = (x, _cocoa_y(y))
    for screen in AppKit.NSScreen.screens():
        if AppKit.NSPointInRect(point, screen.frame()):
            return screen
    return AppKit.NSScreen.mainScreen()


def build_menu(entries):
    """entries: (title, fn, checked), (title, [entries]) for a submenu, or None for a separator."""
    menu = AppKit.NSMenu.alloc().init()
    menu.setAutoenablesItems_(False)
    for entry in entries:
        if entry is None:
            menu.addItem_(AppKit.NSMenuItem.separatorItem())
        elif isinstance(entry[1], list):
            item = menu.addItemWithTitle_action_keyEquivalent_(entry[0], None, "")
            menu.setSubmenu_forItem_(build_menu(entry[1]), item)
        else:
            title, fn, checked = entry
            item = menu.addItemWithTitle_action_keyEquivalent_(title, "run:" if fn else None, "")
            if fn:
                item.setTarget_(ui.target(lambda _s, fn=fn: fn()))
            item.setEnabled_(fn is not None)
            item.setState_(1 if checked else 0)
    return menu
