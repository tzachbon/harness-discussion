"""The floating button bar next to a selection: a non-activating panel that never takes focus.

Three layouts: reading ▶ ✦ Voice Speed … ✕, writing (editable selection) adds ✎ Mode ⌄ | before it,
and playing ⏸ ⏮ ⏭ ■ Voice Speed … ✕. Everything else lives in the "…" menu.
"""
import AppKit

from .. import lang, settings
from . import ui
from .bar_parts import (  # noqa: F401
    _Button,
    _cocoa_y,
    _divider,
    _Hover,
    _icon_button,
    _play_button,
    _rewrite_button,
    _screen_at,
    _set_rewrite_title,
    _set_title,
    _text_button,
    build_menu,
)
from .caption import Caption
from .esc import EscWatcher

IDLE_HIDE_S = 4.0


class Popup:
    def __init__(self, speed, *, on_play, on_stop, on_pause, on_resume, on_lang, on_speed, on_settings,
                 on_source, sources, on_skip, on_summarize=lambda text, tag: None, on_rewrite=None,
                 modes=lambda: [], on_escape=None):
        """sources: returns the source names the menu offers; on_source(name) picks one. on_skip(-1 or 1).
        on_rewrite(text, mode name) is None where in-place editing isn't available; modes() lists mode names."""
        self.on_play, self.on_stop, self.on_pause, self.on_resume = on_play, on_stop, on_pause, on_resume
        self.on_summarize, self.on_rewrite, self.modes = on_summarize, on_rewrite, modes
        self.on_lang, self.on_speed, self.on_settings = on_lang, on_speed, on_settings
        self.on_source, self.sources, self.on_skip = on_source, sources, on_skip
        self.text, self.playing, self.paused, self.menu_open, self.loading = "", False, False, False, False
        self.editable, self.rewriting, self._downloading = False, False, False
        self._lang, self._speed, self._source, self._hide_timer, self._mode = None, speed, None, None, ""
        self._esc = EscWatcher(on_escape or self.close)

        self.rewrite_btn = _rewrite_button(lambda: self._rewrite(self._mode))
        self.mode_btn = _icon_button("chevron.down", "Mode", self._mode_menu)
        self.btn = _play_button(self._toggle)
        self.stop_btn = _icon_button("stop.fill", "Stop", lambda _s: self._stop(), tint=AppKit.NSColor.whiteColor())
        self.back_btn = _icon_button("backward.fill", "Previous sentence", lambda _s: on_skip(-1))
        self.next_btn = _icon_button("forward.fill", "Next sentence", lambda _s: on_skip(1))
        self.spinner = AppKit.NSProgressIndicator.alloc().init()
        self.spinner.setStyle_(AppKit.NSProgressIndicatorStyleSpinning)
        self.spinner.setControlSize_(AppKit.NSControlSizeSmall)
        for anchor in (self.spinner.widthAnchor(), self.spinner.heightAnchor()):
            anchor.constraintEqualToConstant_(30).setActive_(True)
        self.ai_btn = _icon_button("sparkles", "Summarize", lambda _s: self._summarize(),
                                   tint=AppKit.NSColor.whiteColor())
        self.ai_btn.setWantsLayer_(True)
        self.ai_btn.layer().setCornerRadius_(8)
        self.ai_progress = AppKit.NSProgressIndicator.alloc().init()  # a filling ring while the model downloads
        self.ai_progress.setStyle_(AppKit.NSProgressIndicatorStyleSpinning)
        self.ai_progress.setControlSize_(AppKit.NSControlSizeSmall)
        self.ai_progress.setIndeterminate_(False)
        self.ai_progress.setMaxValue_(1.0)
        for anchor in (self.ai_progress.widthAnchor(), self.ai_progress.heightAnchor()):
            anchor.constraintEqualToConstant_(26).setActive_(True)
        self.source_btn = _text_button(self._source_menu, "", settings.DEFAULT_SOURCES, ui.source_icon)
        self.speed_btn = _text_button(self._speed_menu, settings.speed_label(speed),
                                      [settings.speed_label(s) for s in settings.SPEED_PRESETS])
        self.more_btn = _icon_button("ellipsis", "More", self._more_menu)
        self.close_btn = _icon_button("xmark", "Close", lambda _s: self.close())
        self.divider = _divider()
        self.row = ui.stack([self.rewrite_btn, self.mode_btn, self.divider, self.btn, self.spinner, self.back_btn,
                             self.next_btn, self.stop_btn, self.ai_btn, self.ai_progress, self.source_btn,
                             self.speed_btn, self.more_btn, self.close_btn], vertical=False, spacing=8)
        self.row.setEdgeInsets_((6, 6, 6, 8))
        # a centered stack drops part of its vertical insets, and the 30-point ▶ needs 6 on every side to sit in the curve
        self.row.heightAnchor().constraintEqualToConstant_(42).setActive_(True)
        _set_rewrite_title(self.rewrite_btn, "Rewrite")

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
            for a, b in (("leadingAnchor", "leadingAnchor"), ("trailingAnchor", "trailingAnchor"),
                         ("topAnchor", "topAnchor"), ("bottomAnchor", "bottomAnchor")):
                getattr(child, a)().constraintEqualToAnchor_(getattr(parent, b)()).setActive_(True)

        style = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
        p = self.win = ui.Panel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (10, 10)), style, AppKit.NSBackingStoreBuffered, False)
        p.setContentView_(hover)
        self.caption = Caption()
        p.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameVibrantDark))
        p.setLevel_(AppKit.NSStatusWindowLevel)
        p.setFloatingPanel_(True)
        p.setBecomesKeyOnlyIfNeeded_(True)
        p.setHidesOnDeactivate_(False)
        p.setOpaque_(False)
        p.setBackgroundColor_(AppKit.NSColor.clearColor())
        p.setHasShadow_(True)
        p.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                                 | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
                                 | AppKit.NSWindowCollectionBehaviorTransient)
        self._layout()

    def show(self, text, x, y, editable=False):
        self.text, self.editable = text, editable and self.on_rewrite is not None
        self.set_playing(False)
        self._set_lang(None)
        self._layout(keep_left=False)
        p = self.win
        w, h = p.frame().size.width, p.frame().size.height
        area = _screen_at(x, y).visibleFrame()
        area_top = _cocoa_y(area.origin.y + area.size.height)  # top-left y of the usable area
        area_bottom = area_top + area.size.height
        top = y - h - 32 if y - h - 32 >= area_top else y + 16  # 32 clears a large selected line
        top = max(area_top, min(top, area_bottom - h))
        left = max(area.origin.x, min(x + 12, area.origin.x + area.size.width - w))
        p.setFrameOrigin_((left, _cocoa_y(top + h)))
        p.orderFrontRegardless()
        self._esc.start()
        self.schedule_hide()

    def close(self):
        """Esc: stop any read and hide."""
        self.set_playing(False)
        self.hide()

    def hide(self):
        self.cancel_hide()
        self._esc.stop()
        self.caption.hide()
        self.win.orderOut_(None)
        self.on_stop()  # drops the Codex session warmed up for this popup

    @property
    def visible(self):
        return bool(self.win.isVisible())

    def contains(self, x, y):
        f = self.win.frame()
        top = _cocoa_y(f.origin.y + f.size.height)
        return self.visible and f.origin.x <= x < f.origin.x + f.size.width and top <= y < top + f.size.height

    def anchor(self):
        """Top-left of the bar, then its height, so the toast can sit under or above it."""
        f = self.win.frame()
        return f.origin.x, _cocoa_y(f.origin.y + f.size.height), f.size.height

    def lang_tag(self):
        return self._lang

    def _layout(self, keep_left=True):
        """Shows the controls of the current layout. The left edge and top stay put when the width changes."""
        writing = self.editable and not self.playing
        playing = self.playing
        shown = {self.source_btn, self.speed_btn, self.more_btn, self.close_btn}
        shown |= {self.spinner} if self.loading else {self.btn}
        if playing:
            shown |= {self.back_btn, self.next_btn, self.stop_btn}
        else:
            shown |= {self.ai_progress} if self._downloading else {self.ai_btn}
        if writing:
            shown |= {self.rewrite_btn, self.mode_btn, self.divider}
        for view in self.row.arrangedSubviews():
            view.setHidden_(view not in shown)
        p = self.win
        f = p.frame()
        top = f.origin.y + f.size.height
        p.setContentSize_(p.contentView().fittingSize())
        self.effect.layer().setCornerRadius_(p.frame().size.height / 2)
        if keep_left:
            p.setFrameOrigin_((f.origin.x, top - p.frame().size.height))

    def set_editable(self, editable):
        self.editable = editable and self.on_rewrite is not None
        self._layout()

    def set_last_mode(self, name):
        self._mode = name
        _set_rewrite_title(self.rewrite_btn, "Rewriting…" if self.rewriting else (name or "Rewrite"))
        self.rewrite_btn.setToolTip_(f"Rewrite: {name}" if name else "Rewrite")
        if self.visible:
            self._layout()

    def set_rewriting(self, on):
        self.rewriting = on
        self.rewrite_btn.setEnabled_(not on)
        self.set_last_mode(self._mode)
        self.cancel_hide() if on else self.schedule_hide()

    def set_loading(self, loading):
        if loading == self.loading:
            return
        self.loading = loading
        self.spinner.startAnimation_(None) if loading else self.spinner.stopAnimation_(None)
        self._layout()

    def set_playing(self, playing, paused=False):
        self.playing, self.paused = playing, playing and paused
        if not playing:
            self.caption.hide()
            self.set_loading(False)
            self._highlight(False)
        self.caption.set_paused(self.paused)  # a read stopped while paused must not freeze the next one
        pausing = playing and not paused
        action = "Pause" if pausing else "Play"
        self.btn.setImage_(ui.symbol("pause.fill" if pausing else "play.fill", action))
        self.btn.setAccessibilityLabel_(action)
        self._layout()
        if not playing:
            self.schedule_hide()

    def set_summary(self):
        """A summary is on its way: the spinner until its first audio, and ✦ marked until it ends."""
        self.cancel_hide()
        self.set_playing(True)
        self.set_loading(True)
        self._highlight(True)

    def _highlight(self, on):
        self.ai_btn.layer().setBackgroundColor_(ui.cg(AppKit.NSColor.colorWithWhite_alpha_(1, 0.14 if on else 0)))

    def set_summarize_progress(self, fraction):
        """0..1 while the model downloads, None after."""
        self._downloading = fraction is not None
        self.ai_progress.setDoubleValue_(fraction or 0)
        self._layout()

    def _summarize(self):
        self.cancel_hide()
        self.on_summarize(self.text, self._lang)
        if not self.playing:  # nothing started, such as a cancelled download
            self.schedule_hide()

    def _rewrite(self, mode):
        if self.on_rewrite and not self.rewriting:
            self.cancel_hide()
            self.on_rewrite(self.text, mode)

    def set_caption(self, text):
        if self.playing:
            screen = self.win.screen() or AppKit.NSScreen.mainScreen()
            self.caption.show(text, self.win.frame(), screen.visibleFrame())

    def set_caption_paused(self, paused):
        self.caption.set_paused(paused)

    def configure_captions(self, font_size, background, background_opacity):
        self.caption.configure(font_size, background, background_opacity)

    def set_speed(self, speed):
        self._speed = speed
        _set_title(self.speed_btn, settings.speed_label(speed))

    def set_source(self, name):
        self._source = name
        _set_title(self.source_btn, name, ui.source_icon(name))

    def schedule_hide(self):
        self.cancel_hide()
        if not (self.playing or self.menu_open or self.rewriting) and self.visible:  # a hidden popup must not stop reads
            self._hide_timer = ui.timer(IDLE_HIDE_S, self.hide)

    def cancel_hide(self):
        if self._hide_timer:
            self._hide_timer.invalidate()
            self._hide_timer = None

    def play(self):
        """Starts reading the shown text, as ▶ does."""
        if not self.playing:
            self._toggle()

    def _toggle(self):
        if not self.playing:
            self.cancel_hide()
            self.set_playing(True)
            self.set_loading(True)
            self.on_play(self.text, self._lang)
        elif self.paused:
            self.on_resume()
            self.set_playing(True)
        else:
            self.on_pause()
            self.set_playing(True, paused=True)

    def _open_settings(self):
        if self.playing and not self.paused:
            self._toggle()
        self.on_settings()

    def _stop(self):
        self.on_stop()
        self.set_playing(False)

    def _open(self, button, menu):
        """Blocks while the menu is open; timers keep running."""
        self.menu_open = True
        self.cancel_hide()
        try:
            menu.popUpMenuPositioningItem_atLocation_inView_(None, (0, button.bounds().size.height + 4), button)
        finally:
            self.menu_open = False
            self.schedule_hide()

    def _voice_entries(self):
        """Ready sources to pick from, then each source that needs an install or sign-in."""
        choices = self.sources()
        voices = [(n, lambda n=n: self.on_source(n), n == self._source) for n, fix in choices if not fix]
        fixes = [(f"{n}: {fix}", lambda n=n: self.on_source(n), False) for n, fix in choices if fix]
        return voices + ([None] if voices and fixes else []) + fixes

    def _speed_entries(self):
        return [(settings.speed_label(s), lambda s=s: self._speed_changed(s), s == self._speed)
                for s in settings.SPEED_PRESETS]

    def menu_entries(self):
        """The "…" menu: Voice, Language, Speed, Summarize, Rewrite, Settings."""
        languages = [("Auto", lambda: self._lang_changed(None), self._lang is None)] + [
            (v[0], lambda t=t: self._lang_changed(t), t == self._lang) for t, v in lang.LANGS.items()]
        entries = [("Voice", self._voice_entries()), ("Language", languages),
                   ("Speed", self._speed_entries()), None, ("Summarize", self._summarize, False)]
        if self.editable:
            entries.append(("Rewrite", [(m, lambda m=m: self._rewrite(m), m == self._mode) for m in self.modes()]))
        return entries + [None, ("Settings…", self._open_settings, False)]

    def _more_menu(self, button):
        self._open(button, build_menu(self.menu_entries()))

    def _source_menu(self, button):
        self._open(button, build_menu(self._voice_entries()))

    def _speed_menu(self, button):
        self._open(button, build_menu(self._speed_entries()))

    def _mode_menu(self, button):
        self._open(button, build_menu([(m, lambda m=m: self._rewrite(m), m == self._mode) for m in self.modes()]))

    def _set_lang(self, tag):
        self._lang = tag

    def _lang_changed(self, tag):
        self._set_lang(tag)
        self.on_lang(tag)

    def _speed_changed(self, speed):
        self.set_speed(speed)
        self.on_speed(speed)
