"""Accessibility permission: detection, the guide window, and recovery after an update.

macOS ties the grant to the app's code signature. Ad-hoc signed builds get a new signature on
every update, so System Settings can show Harness Discussion as allowed while macOS no longer trusts it.
"""
import logging
import subprocess

import AppKit
from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions
from Foundation import NSBundle, NSURL

from .. import hotkeys
from .startup import LABEL

log = logging.getLogger(__name__)
SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
PROMPT = "AXTrustedCheckOptionPrompt"


def trusted() -> bool:
    return bool(AXIsProcessTrusted())


def current_build_id():
    """Version plus commit, stamped into Info.plist at build time. None for source runs."""
    info = NSBundle.mainBundle().infoDictionary() or {}
    build = info.get("HarnessDiscussionBuild")
    return str(build) if build else None


def is_stale(granted_build, build):
    """A grant saved by another build of the app, which macOS no longer honors."""
    return bool(build and granted_build and granted_build != build)


def open_settings(prompt=True):
    if prompt:
        AXIsProcessTrustedWithOptions({PROMPT: True})  # adds Harness Discussion to the list
    AppKit.NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(SETTINGS_URL))


def reset_grant() -> bool:
    """Clears this app's stale Accessibility entry. Fixed arguments, no shell."""
    try:
        return subprocess.run(["/usr/bin/tccutil", "reset", "Accessibility", LABEL],
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class Watcher:
    """Polls once a second for the app's lifetime, so a grant or a revoke shows up at once."""

    def __init__(self, app):
        self.app, self.state, self.guide = app, None, None

    def tick(self):
        now = trusted()
        if now != self.state:
            self.state = now
            self._changed(now)
        if now:
            self._heal()
        self.app.host.after(1000, self.tick)

    def _heal(self):
        """A hold shortcut's event tap can fail at launch or right after a grant, and never retries itself."""
        for name, key in (("rewrite", "rewrite_hotkey"), ("dictate", "dictate_hotkey")):
            shortcut = self.app.host.shortcuts.get(name)
            if shortcut is not None and not shortcut.active:
                log.info("%s shortcut inactive, binding again", name)
                self.app.apply(key, self.app.cfg[key])

    def _changed(self, now):
        host, build = self.app.host, current_build_id()
        if now:
            if build and self.app.cfg.get("ax_granted_build") != build:
                self.app.change("ax_granted_build", build)
            host.set_permission_missing(False)
            for key in hotkeys.SHORTCUTS:  # key monitors only receive events once trusted
                self.app.apply(key, self.app.cfg[key])
            if self.guide:
                self.guide.granted()
                self.guide = None
            return
        stale = is_stale(self.app.cfg.get("ax_granted_build"), build)
        log.info("accessibility missing (stale: %s)", stale)
        host.set_permission_missing(True)
        self.show_guide(stale)

    def show_guide(self, stale=None):
        if stale is None:
            stale = is_stale(self.app.cfg.get("ax_granted_build"), current_build_id())
        from .guide import GuideWindow
        if self.guide and self.guide.alive:
            self.guide.focus()
        else:
            self.guide = GuideWindow(stale, self.app.host.after)
