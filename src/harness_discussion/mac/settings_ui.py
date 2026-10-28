"""The Settings window on macOS. Changes apply at once."""
import sys

import AppKit
from Foundation import NSURL

from .. import icons, settings, updates
from ..sources import fix as source_fix
from . import permission, startup, ui
from .about_page import AboutSection
from .captions_page import CaptionsPage
from .reading_page import ReadingPage
from .stt_page import SttPage
from .summarize_page import SummarizePage
from .writing_page import WritingPage

WIDTH = 460  # inside a card
SIDEBAR = 200
TOP = 44  # clears the title bar, which the sidebar runs under
RELEASES = "https://github.com/tzachbon/harness-discussion/releases/tag/v{}"
STATUS_COLOR = {"ready": "systemGreenColor", "missing": "systemRedColor", None: "tertiaryLabelColor"}
# (title, SF Symbol, tile color) per sidebar page, in sidebar order
PAGES = (("Voices", "waveform", "systemPurpleColor"), ("Captions", "captions.bubble.fill", "systemTealColor"),
         ("Reading", "text.cursor", "systemBlueColor"), ("Summarize", "sparkles", "systemPinkColor"),
         ("Speech to Text", "mic.fill", "systemOrangeColor"), ("General", "gearshape.fill", "systemGrayColor"))
WRITER_PAGES = (("Writing", "pencil", "systemIndigoColor"), ("Dictation", "mic.circle.fill", "systemRedColor"))


def _card(views):
    return ui.card(views, width=WIDTH + 28)


def _note(text):
    """Help text under a card, lined up with the card's text."""
    note = ui.stack([ui.label(text, 11, secondary=True, width=WIDTH)])
    note.setEdgeInsets_((0, 14, 0, 14))
    return note


def _spacer():
    view = AppKit.NSView.alloc().init()
    view.setContentHuggingPriority_forOrientation_(1, AppKit.NSLayoutConstraintOrientationHorizontal)
    return view


def _divider():
    line = AppKit.NSBox.alloc().init()
    line.setBoxType_(AppKit.NSBoxSeparator)
    line.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
    return line


class _NavButton(AppKit.NSButton):
    """A sidebar row; the selected one is filled with the accent color."""

    def setSelected_(self, on):
        self.layer().setBackgroundColor_((AppKit.NSColor.controlAccentColor() if on
                                          else AppKit.NSColor.clearColor()).CGColor())
        self.setContentTintColor_(AppKit.NSColor.whiteColor() if on else AppKit.NSColor.labelColor())


def _nav_button(title, symbol, color, fn):
    b = ui.bind(_NavButton.alloc().init(), lambda _s: fn())
    b.setBordered_(False)
    b.setWantsLayer_(True)
    b.layer().setCornerRadius_(6)
    b.setImage_(ui.inset(ui.tile(symbol, getattr(AppKit.NSColor, color)()), 6))
    b.setImagePosition_(AppKit.NSImageLeading)
    b.setAlignment_(AppKit.NSTextAlignmentLeft)
    b.setFont_(AppKit.NSFont.systemFontOfSize_(13))
    b.widthAnchor().constraintEqualToConstant_(SIDEBAR - 20).setActive_(True)
    b.heightAnchor().constraintEqualToConstant_(30).setActive_(True)
    return b


class SettingsWindow(CaptionsPage, SummarizePage, ReadingPage, SttPage, WritingPage, AboutSection):
    def __init__(self, app, show_guide, welcome=False):
        self.app, self.cfg = app, dict(app.cfg)
        self._save_timer = self._recorder = None

        # Voices
        self.speed = AppKit.NSSlider.sliderWithValue_minValue_maxValue_target_action_(
            self.cfg["speed"], settings.SPEEDS[0], settings.SPEEDS[1], None, None)
        ui.bind(self.speed, lambda s: self._speed_changed(s.doubleValue()))
        self.speed.widthAnchor().constraintEqualToConstant_(WIDTH - 190).setActive_(True)
        self.speed_label = ui.label(settings.speed_label(self.cfg["speed"]), 13, bold=True)
        self._engines = {e.name: e for e in app.chain.engines}
        self.fix_buttons, self.dots = {}, {}
        self.source_rows = {name: self._source_row(name) for name, _ in self.cfg["sources"]}
        self.sources = ui.stack([], spacing=10)
        self.welcome = ui.label(settings.WELCOME, 13, bold=True) if welcome else None
        speed = ui.stack([ui.image_view(ui.symbol("tortoise.fill", "Slower"), 16), self.speed,
                          ui.image_view(ui.symbol("hare.fill", "Faster"), 16), self.speed_label, _spacer(),
                          ui.button("Test voice", self._test)], vertical=False, spacing=8)
        speed.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        voices = [
            _card([*([self.welcome] if welcome else []), self.sources]),
            _note("Sources read top to bottom. If one can't read, the next one does. Codex uses your ChatGPT "
                  "sign-in and can read slower but not faster, so above 1× the next source reads. macOS voices "
                  "stay on this Mac; add more in System Settings > Accessibility > Spoken Content."),
            ui.label("Speed", 13, bold=True), _card([speed])]

        # Captions
        captions = self._captions_page()

        reading = self._reading_page()

        summary = self._summarize_page()

        stt = self._stt_page()

        # General
        login, self.startup = ui.option("Open at login", "Starts Harness Discussion in the menu bar when you log in.",
                                        startup.enabled(), self._startup_changed, WIDTH)
        auto, self.auto_update = ui.option("Check for updates automatically", "Checks at startup and once a day.",
                                           self.cfg["auto_update"], lambda on: self._set("auto_update", on), WIDTH)
        self.update_button = ui.button("Check for updates", app.check_update)
        self.download_button = ui.button("Download", self._download, primary=True)
        self.update_status = ui.label("", 11, secondary=True, width=WIDTH - 240)
        about = ui.stack([ui.image_view(ui.pil_image(icons.icon_image(80), 40), 40),
                          ui.stack([ui.label("Harness Discussion", 13, bold=True),
                                    ui.label(f"Version {updates.current_version()}", 11, secondary=True)],
                                   spacing=1), _spacer()], vertical=False, spacing=10)
        about.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        version = ui.stack([self.update_status, _spacer(), self.download_button, self.update_button], vertical=False)
        version.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        general = [_card([about, _divider(), version]), *self._about_section(), _card([login, _divider(), auto])]

        # The page area: a permission banner over the selected page
        warning = ui.image_view(ui.symbol("exclamationmark.triangle.fill", "Warning"), 18)
        warning.setContentTintColor_(AppKit.NSColor.systemOrangeColor())
        self.permission_row = _card([ui.stack([
            warning, ui.label("Shortcuts and selected text need Accessibility permission.", 12, width=WIDTH - 130),
            ui.button("Fix this", show_guide, primary=True)], vertical=False, spacing=10)])
        pages = [voices, captions, reading, summary, stt, general]
        self.page_list = PAGES
        if app.writer_ready:  # after Summarize
            pages[4:4] = [self._writing_page(), self._dictation_page()]
            self.page_list = PAGES[:4] + WRITER_PAGES + PAGES[4:]
        self.pages = [ui.stack([ui.label(title, 20, bold=True), *views], spacing=10)
                      for (title, _, _), views in zip(self.page_list, pages)]
        rest = AppKit.NSView.alloc().init()  # takes the spare height, so cards keep their own
        rest.setContentHuggingPriority_forOrientation_(1, AppKit.NSLayoutConstraintOrientationVertical)
        area = ui.stack([self.permission_row, *self.pages, rest], spacing=16)
        area.setEdgeInsets_((TOP, 24, 24, 24))

        self.nav = [_nav_button(title, symbol, color, lambda i=i: self.show_page(i))
                    for i, (title, symbol, color) in enumerate(self.page_list)]
        sidebar = AppKit.NSVisualEffectView.alloc().init()
        sidebar.setMaterial_(AppKit.NSVisualEffectMaterialSidebar)
        sidebar.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        nav = ui.stack(self.nav, spacing=2)
        nav.setTranslatesAutoresizingMaskIntoConstraints_(False)
        sidebar.addSubview_(nav)
        nav.topAnchor().constraintEqualToAnchor_constant_(sidebar.topAnchor(), TOP).setActive_(True)
        nav.leadingAnchor().constraintEqualToAnchor_constant_(sidebar.leadingAnchor(), 10).setActive_(True)
        sidebar.widthAnchor().constraintEqualToConstant_(SIDEBAR).setActive_(True)

        self.content = ui.stack([sidebar, area], vertical=False, spacing=0)
        self.content.setAlignment_(AppKit.NSLayoutAttributeTop)
        sidebar.heightAnchor().constraintEqualToAnchor_(self.content.heightAnchor()).setActive_(True)
        self._page = 0
        self.refresh_sources()
        self.refresh_summarize()
        if app.writer_ready:
            self.refresh_writing()
        tallest = 0
        for i in range(len(self.page_list)):  # one window height for every page, so switching never resizes it
            self.show_page(i)
            tallest = max(tallest, area.fittingSize().height)
        area.heightAnchor().constraintGreaterThanOrEqualToConstant_(tallest).setActive_(True)
        self.show_page(0)
        self.win = ui.window("Harness Discussion Settings", self.content)
        self.win.setStyleMask_(self.win.styleMask() | AppKit.NSWindowStyleMaskFullSizeContentView)
        self.win.setTitlebarAppearsTransparent_(True)
        self.win.setTitleVisibility_(AppKit.NSWindowTitleHidden)
        self.refresh_permission()
        self.focus()

    def show_page(self, index):
        self._page = index
        for i, (page, button) in enumerate(zip(self.pages, self.nav)):
            page.setHidden_(i != index)
            button.setSelected_(i == index)
        self._refresh_nav()

    def _refresh_nav(self):
        """An orange dot on Voices while an enabled source needs an install or sign-in."""
        attention = any(on and source_fix(name, self.app.statuses.get(name)) for name, on in self.cfg["sources"])
        for i, (button, (title, _, _)) in enumerate(zip(self.nav, self.page_list)):
            color = AppKit.NSColor.whiteColor() if i == self._page else AppKit.NSColor.labelColor()
            text = AppKit.NSMutableAttributedString.alloc().initWithString_attributes_(
                "  " + title, {AppKit.NSForegroundColorAttributeName: color,
                               AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(13)})
            if attention and i == 0:
                text.appendAttributedString_(AppKit.NSAttributedString.alloc().initWithString_attributes_(
                    "  ●", {AppKit.NSForegroundColorAttributeName: AppKit.NSColor.systemOrangeColor(),
                            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(9)}))
            button.setAttributedTitle_(text)

    @property
    def alive(self):
        return bool(self.win.isVisible())

    def focus(self):
        self._refresh_stt()
        self.refresh_updates()
        self.refresh_permission()
        if hasattr(self, "microphone"):  # only after Summarize
            self.refresh_microphones()
        ui.show(self.win)

    def _fit(self):
        self.win.setContentSize_(self.content.fittingSize())

    def refresh_permission(self):
        self.permission_row.setHidden_(permission.trusted())
        self._fit()

    def refresh_updates(self):
        text, busy = self.app.update_state()
        installed = getattr(sys, "frozen", False)
        self.update_button.setEnabled_(installed and not busy)
        self.auto_update.setEnabled_(installed)
        self.download_button.setHidden_(self.app.available_version() is None)
        self.download_button.setEnabled_(installed and not busy)
        self.update_status.setStringValue_(text if installed else "Updates are available in installed builds.")

    def set_speed(self, speed):
        """Mirrors a speed picked in the popup, without saving it again."""
        self.speed.setDoubleValue_(speed)
        self.cfg["speed"] = speed
        self.speed_label.setStringValue_(settings.speed_label(speed))

    def close(self):
        self._flush()
        self._stop_recording()
        self.win.close()

    def _source_row(self, name):
        """(enabled, status, voice picker or None, row, (test, up, down)) for one voice source."""
        check = ui.switch(False, lambda on: self._set_sources(
            [[n, on if n == name else was] for n, was in self.cfg["sources"]]))
        check.setAccessibilityLabel_(f"Use {name}")
        status = ui.label("", 11, secondary=True)
        dot = ui.label("●", 9)
        choices = getattr(self._engines.get(name), "voices", ())
        voice = AppKit.NSPopUpButton.alloc().init() if choices else None
        if voice:
            voice.addItemsWithTitles_([label for _, label in choices])
            voice.setControlSize_(AppKit.NSControlSizeSmall)
            ui.bind(voice, lambda s: self._set("voices", {**self.cfg["voices"],
                                                          name: choices[s.indexOfSelectedItem()][0]}))
        slot = voice or ui.label("Default voice" if name == "Codex" else "", 11, secondary=True)
        fix = ui.button("", lambda: self.app.fix_source(name))
        fix.setControlSize_(AppKit.NSControlSizeSmall)
        fix.setBezelColor_(AppKit.NSColor.controlAccentColor())
        self.fix_buttons[name] = fix
        buttons = (ui.symbol_button("play.circle", f"Test {name}", lambda: self._test_source(name), 16),
                   ui.symbol_button("chevron.up", f"Move {name} up", lambda: self._move(name, -1), 11),
                   ui.symbol_button("chevron.down", f"Move {name} down", lambda: self._move(name, 1), 11))
        text = ui.stack([ui.label(name, 13, bold=True), ui.stack([dot, status], vertical=False, spacing=4)],
                        spacing=1)
        row = ui.stack([ui.image_view(ui.source_icon(name, 28), 28), text, _spacer(), fix, slot, *buttons, check],
                       vertical=False, spacing=8)
        row.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        self.dots[name] = dot
        return check, status, voice, row, buttons

    def refresh_sources(self):
        """Shows the rows in the saved order, with the statuses core last checked and a fix for each unready one."""
        sources = self.cfg["sources"]
        for view in list(self.sources.arrangedSubviews()):
            self.sources.removeArrangedSubview_(view)
            view.removeFromSuperview()
        for i, (name, enabled) in enumerate(sources):
            check, status, voice, row, (_, up, down) = self.source_rows[name]
            state = self.app.statuses.get(name)
            check.setState_(1 if enabled else 0)
            status.setStringValue_(settings.status_text(state))
            self.dots[name].setTextColor_(getattr(AppKit.NSColor, STATUS_COLOR.get(state, "systemOrangeColor"))())
            found, fix = source_fix(name, state), self.fix_buttons[name]
            fix.setHidden_(not found)
            fix.setTitle_(found[0] if found else "")
            if voice:
                engine = self._engines[name]
                ids = [v for v, _ in engine.voices]
                chosen = self.cfg["voices"].get(name)
                chosen = chosen if chosen in ids else getattr(engine, "default_voice", ids[0])
                voice.selectItemAtIndex_(ids.index(chosen))
            up.setEnabled_(i > 0)
            down.setEnabled_(i < len(sources) - 1)
            self.sources.addArrangedSubview_(row)
        if hasattr(self, "nav"):
            self._refresh_nav()
        if hasattr(self, "win"):  # a status that wraps makes its row taller
            self._fit()

    def set_sources(self, sources):
        """Mirrors an order changed elsewhere, without saving it again."""
        self.cfg["sources"] = sources
        self.refresh_sources()

    def _set_sources(self, sources):
        self._set("sources", sources)
        self.refresh_sources()

    def _move(self, name, step):
        sources = [list(s) for s in self.cfg["sources"]]
        i = [n for n, _ in sources].index(name)
        if 0 <= i + step < len(sources):
            sources[i], sources[i + step] = sources[i + step], sources[i]
            self._set_sources(sources)

    def _test_source(self, name):
        self._flush()
        self.app.play(f"This is {name}.", None, only=name)

    def _set(self, key, value):
        self.cfg[key] = value
        self.app.change(key, value)

    def _speed_changed(self, value):
        value = round(value * 20) / 20  # 0.05 steps
        self.set_speed(value)
        self.app.change("speed", value, False)  # the next read uses it at once
        if self._save_timer:
            self._save_timer.invalidate()
        self._save_timer = ui.timer(0.4, self._flush)

    def _flush(self):
        self._about_changed()
        if self._save_timer:
            self._save_timer.invalidate()
            self._save_timer = None
            self._set("speed", self.cfg["speed"])

    def _startup_changed(self, on):
        try:
            startup.set_enabled(on)
        except OSError as e:
            self.startup.setState_(1 if startup.enabled() else 0)
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_(f"Could not change start at login: {e}")
            alert.runModal()

    def _download(self):
        version = self.app.available_version()
        if version and not self.app.install_update():
            AppKit.NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(RELEASES.format(version)))

    def _test(self):
        self._flush()
        self.app.test_voice(f"This is how Harness Discussion sounds at {settings.speed_label(self.cfg['speed'])} speed.")
