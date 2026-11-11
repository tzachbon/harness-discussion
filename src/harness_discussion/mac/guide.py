"""The window that walks the user through granting Accessibility. Shown only while it is missing."""
import AppKit

from . import permission, ui

WIDTH = 400


class GuideWindow:
    def __init__(self, stale, after):
        self.after, self.stale = after, stale
        if stale:
            title = "Harness Discussion was updated, so macOS needs permission again"
            steps = ["1. Click Fix permission. This clears the old entry.",
                     "2. Turn on Harness Discussion in the list that opens.",
                     "3. Come back here. This window closes by itself when it works."]
            action = ui.button("Fix permission", self._fix, primary=True)
        else:
            title = "Let Harness Discussion read selected text"
            steps = ["1. Click Open System Settings below.",
                     "2. Turn on Harness Discussion in the list. If it isn't there, click + and pick "
                     "Harness Discussion from Applications.",
                     "3. Come back here. This window closes by itself when it works."]
            action = ui.button("Open System Settings", lambda: permission.open_settings(), primary=True)
        why = ("macOS asks you to allow this once. Harness Discussion uses it for its keyboard shortcuts, to read "
               "the text you select, and to put a rewrite in its place. Nothing is read until you press play "
               "or a shortcut.")
        self.help = ui.label("", 12, secondary=True, width=WIDTH)
        self.help.setHidden_(True)
        self.spinner = AppKit.NSProgressIndicator.alloc().init()
        self.spinner.setStyle_(AppKit.NSProgressIndicatorStyleSpinning)
        self.spinner.setControlSize_(AppKit.NSControlSizeSmall)
        self.spinner.startAnimation_(None)
        self.status = ui.label("Waiting for permission…", 12, secondary=True)
        content = ui.stack([
            ui.label(title, 15, bold=True, width=WIDTH),
            ui.label(why, 12, secondary=True, width=WIDTH),
            ui.stack([ui.label(s, 13, width=WIDTH) for s in steps], spacing=6),
            self.help,
            ui.stack([action, self.spinner, self.status], vertical=False, spacing=10),
        ], spacing=14, inset=22)
        self.win = ui.window("Harness Discussion needs permission", content)
        ui.show(self.win)

    @property
    def alive(self):
        return self.win.isVisible()

    def focus(self):
        ui.show(self.win)

    def _fix(self):
        if permission.reset_grant():
            permission.open_settings()
        else:
            self.help.setStringValue_("Couldn't clear the old entry. In the list, select Harness Discussion, click −, "
                                      "then click + and add Harness Discussion from Applications again.")
            self.help.setHidden_(False)
            permission.open_settings(prompt=False)
        self.win.setContentSize_(self.win.contentView().fittingSize())

    def granted(self):
        self.spinner.stopAnimation_(None)
        self.spinner.setHidden_(True)
        self.status.setStringValue_("Permission granted")
        self.status.setTextColor_(AppKit.NSColor.systemGreenColor())
        self.after(1000, self.win.close)


class ScreenGuideWindow:
    def __init__(self):
        import Quartz
        from .. import meeting_copy as copy
        self.spinner = AppKit.NSProgressIndicator.alloc().init()
        self.spinner.setStyle_(AppKit.NSProgressIndicatorStyleSpinning)
        self.spinner.startAnimation_(None)
        self.status = ui.label(copy.WAITING_PERMISSION, 12, secondary=True)
        def open_settings():
            AppKit.NSWorkspace.sharedWorkspace().openURL_(AppKit.NSURL.URLWithString_(
                "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"))
        content = ui.stack([
            ui.label(copy.GUIDE_HEADING, 15, bold=True, width=WIDTH),
            ui.label(copy.GUIDE_BODY, 12, secondary=True, width=WIDTH),
            ui.stack([ui.label(f"{i}. {step}", 13, width=WIDTH) for i, step in enumerate(copy.GUIDE_STEPS, 1)], spacing=6),
            ui.stack([ui.button(copy.OPEN_SYSTEM_SETTINGS, open_settings, primary=True), self.spinner, self.status],
                     vertical=False, spacing=10),
        ], spacing=14, inset=22)
        self.win = ui.window(copy.GUIDE_TITLE, content)
        def check():
            if not self.alive:
                self.timer.invalidate()
            elif Quartz.CGPreflightScreenCaptureAccess():
                self.timer.invalidate()
                self.win.close()
        self.timer = ui.timer(1, check, repeats=True)
        ui.show(self.win)

    @property
    def alive(self):
        return self.win.isVisible()

    def focus(self):
        ui.show(self.win)
