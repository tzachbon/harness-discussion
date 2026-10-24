"""The writer's toast: a HUD pill like the bar, with a message and a few actions. It hides itself after about
6 s, and waits while the pointer is over it. It never takes focus, so the user's app keeps typing."""
import AppKit

from . import ui
from .popup import _Button, _Hover, _cocoa_y, _screen_at, build_menu

HIDE_S = 6.0


def _action_button(title, fn):
    b = _Button.alloc().init()
    b.setBezelStyle_(AppKit.NSBezelStyleRegularSquare)
    b.setBordered_(False)
    b.setWantsLayer_(True)
    b.layer().setCornerRadius_(8)
    b.layer().setBackgroundColor_(ui.cg(AppKit.NSColor.colorWithWhite_alpha_(1, 0.12)))
    b.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_attributes_(
        f"  {title}  ", {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(12, AppKit.NSFontWeightMedium),
                         AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor()}))
    b.heightAnchor().constraintEqualToConstant_(24).setActive_(True)
    return ui.bind(b, lambda s: fn(s))


class Toast:
    def __init__(self):
        self._timer, self.menu_open, self.actions, self._busy = None, False, [], False
        self.on_hide = lambda: None
        self.label = ui.label("", 12)
        self.label.setTextColor_(AppKit.NSColor.whiteColor())
        self.meter = ui.label("▯" * 8, 12, width=64)
        self.meter.setTextColor_(AppKit.NSColor.systemGreenColor())
        self.meter.setAccessibilityLabel_("Microphone input level")
        self.meter.setHidden_(True)
        self.spinner = AppKit.NSProgressIndicator.alloc().init()
        self.spinner.setStyle_(AppKit.NSProgressIndicatorStyleSpinning)
        self.spinner.setControlSize_(AppKit.NSControlSizeSmall)
        self.row = ui.stack([self.spinner, self.meter, self.label], vertical=False, spacing=8)
        self.row.setEdgeInsets_((8, 14, 8, 10))
        effect = self.effect = AppKit.NSVisualEffectView.alloc().init()
        effect.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow)
        effect.setState_(AppKit.NSVisualEffectStateActive)
        effect.setWantsLayer_(True)
        effect.layer().setMasksToBounds_(True)
        effect.layer().setBorderWidth_(0.5)
        effect.layer().setBorderColor_(ui.cg(AppKit.NSColor.colorWithWhite_alpha_(1, 0.15)))
        hover = _Hover.alloc().initWithOwner_(self)
        for parent, child in ((hover, effect), (effect, self.row)):
            child.setTranslatesAutoresizingMaskIntoConstraints_(False)
            parent.addSubview_(child)
            for a in ("leadingAnchor", "trailingAnchor", "topAnchor", "bottomAnchor"):
                getattr(child, a)().constraintEqualToAnchor_(getattr(parent, a)()).setActive_(True)
        style = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
        p = self.win = ui.Panel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (10, 10)), style, AppKit.NSBackingStoreBuffered, False)
        p.setContentView_(hover)
        p.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameVibrantDark))
        p.setLevel_(AppKit.NSStatusWindowLevel)
        p.setFloatingPanel_(True)
        p.setHidesOnDeactivate_(False)
        p.setOpaque_(False)
        p.setBackgroundColor_(AppKit.NSColor.clearColor())
        p.setHasShadow_(True)
        p.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                                 | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
                                 | AppKit.NSWindowCollectionBehaviorTransient)

    @property
    def visible(self):
        return bool(self.win.isVisible())

    @property
    def message(self):
        return str(self.label.stringValue())

    def show(self, message, x, y, actions=(), busy=False, above=None):
        """actions: (title, fn) for a button, or (title, [(title, fn, checked)]) for a menu button.
        x, y: the top-left point to show it at. A busy toast spins and stays until replaced.
        above: the top of the bar it sits under, to go above the bar instead when there is no room under it."""
        for view in self.actions:
            self.row.removeArrangedSubview_(view)
            view.removeFromSuperview()
        self.actions = []
        for title, action in actions:
            if isinstance(action, list):
                entries = action
                button = _action_button(f"{title} ▾", lambda s, entries=entries: self._menu(s, entries))
            else:
                button = _action_button(title, lambda _s, fn=action: (self.hide(), fn()))
            self.row.addArrangedSubview_(button)
            self.actions.append(button)
        self.label.setStringValue_(message)
        self.meter.setHidden_(True)
        self.spinner.setHidden_(not busy)
        self.spinner.startAnimation_(None) if busy else self.spinner.stopAnimation_(None)
        p = self.win
        p.setContentSize_(p.contentView().fittingSize())
        w, h = p.frame().size.width, p.frame().size.height
        self.effect.layer().setCornerRadius_(h / 2)  # a full pill at any height, as the bar is
        area = _screen_at(x, y if above is None else above).visibleFrame()  # the bar's screen, not past its edge
        area_top = _cocoa_y(area.origin.y + area.size.height)
        area_bottom = area_top + area.size.height
        if above is not None and y + h > area_bottom:
            y = above - h - 6
        top = max(area_top, min(y, area_bottom - h))
        left = max(area.origin.x, min(x, area.origin.x + area.size.width - w))
        p.setFrameOrigin_((left, _cocoa_y(top + h)))
        p.orderFrontRegardless()
        self._busy = busy
        self.schedule_hide()

    def set_input_level(self, level):
        count = round(max(0.0, min(1.0, level)) * 8)
        self.meter.setStringValue_("▮" * count + "▯" * (8 - count))
        self.meter.setHidden_(False)
        self.spinner.stopAnimation_(None)
        self.spinner.setHidden_(True)
        self.win.setContentSize_(self.win.contentView().fittingSize())

    def _menu(self, button, entries):
        self.menu_open = True
        self.cancel_hide()
        try:
            build_menu([(t, (lambda fn=fn: (self.hide(), fn())), c) for t, fn, c in entries]
                       ).popUpMenuPositioningItem_atLocation_inView_(None, (0, button.bounds().size.height + 4), button)
        finally:
            self.menu_open = False
            self.schedule_hide()

    def schedule_hide(self):
        self.cancel_hide()
        if self.visible and not self.menu_open and not self._busy:
            self._timer = ui.timer(HIDE_S, self.hide)

    def cancel_hide(self):
        if self._timer:
            self._timer.invalidate()
            self._timer = None

    def hide(self):
        self.cancel_hide()
        self.win.orderOut_(None)
        self.on_hide()
