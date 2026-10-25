"""Settings → General → About you on macOS: the note sent with every prompt, its switch, and its limit."""
import AppKit
import objc
from Foundation import NSObject

from .. import writer
from . import ui

class _TextChanged(NSObject):
    """A text field delegate that calls fn() on every edit."""

    def initWithFn_(self, fn):
        self = objc.super(_TextChanged, self).init()
        if self is not None:
            self.fn = fn
        return self

    def controlTextDidChange_(self, _note):
        self.fn()

class AboutSection:
    """Mixed into SettingsWindow, which owns app, cfg and _set. Returns the General page's About you views."""

    def _about_section(self):
        from .settings_ui import WIDTH, _card, _divider, _note, _spacer  # settings_ui imports this module
        about_on, _ = ui.option("Add About you to prompts", "Sends this and today's date with every rewrite and "
                                "summary.", self.cfg.get("about_me_on", True), lambda on: self._set("about_me_on", on), WIDTH)
        self.about_text = AppKit.NSTextField.alloc().init()
        self.about_text.setStringValue_(self.cfg.get("about_me", ""))
        self.about_text.setPlaceholderString_("Dana, product manager in Tel Aviv. I work Sunday to Thursday, 9 to 6.")
        self.about_text.setUsesSingleLineMode_(False)
        self.about_text.cell().setWraps_(True)
        self.about_text.cell().setSendsActionOnEndEditing_(True)
        ui.bind(self.about_text, lambda _s: self._about_changed())
        self._about_typing = _TextChanged.alloc().initWithFn_(self._show_about_count)  # AppKit holds delegates weakly
        self.about_text.setDelegate_(self._about_typing)
        self.about_text.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        self.about_text.heightAnchor().constraintEqualToConstant_(54).setActive_(True)
        self.about_count = ui.label("", 11, secondary=True)
        uncapped, self.about_uncapped = ui.option(
            f"Allow more than {writer.ABOUT_CHARS} characters", "Every rewrite and summary gets slower.",
            self.cfg.get("about_me_uncapped", False), self._about_uncapped_changed, WIDTH)
        count = ui.stack([_spacer(), self.about_count], vertical=False)
        count.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        self._show_about_count()
        return [ui.label("About you", 13, bold=True),
                _card([about_on, _divider(), self.about_text, count, _divider(), uncapped]),
                _note("• Who you are and what you do\n• Your working days and hours\n• How you like to write\n"
                      "The date and time zone are added for you.")]

    def _show_about_count(self):
        n, limit = len(str(self.about_text.stringValue()).strip()), writer.about_limit(self.cfg)
        self.about_count.setStringValue_(f"{n} / {limit}" + (" · the rest is not sent" if n > limit else ""))
        self.about_count.setTextColor_(AppKit.NSColor.systemRedColor() if n > limit
                                       else AppKit.NSColor.secondaryLabelColor())

    def _about_uncapped_changed(self, on):
        if on and not self.app.host.confirm("Turn off the limit?", writer.ABOUT_WARNING, ok="Turn Off Limit"):
            self.about_uncapped.setState_(0)
            return
        self._set("about_me_uncapped", on)
        self._about_changed()
        self._show_about_count()

    def _about_changed(self):
        value = str(self.about_text.stringValue()).strip()[:writer.about_limit(self.cfg)]
        if value != self.cfg.get("about_me", ""):
            self._set("about_me", value)
