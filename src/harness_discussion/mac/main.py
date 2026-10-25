"""macOS entry point: single instance, logging, start at login, the permission watcher."""
import fcntl
import logging
import logging.handlers
import os
import sys
import time

from .. import settings
from ..app_tour import App
from ..core import log

OLD_BUNDLE_ID = "com.tzachbon.codexspeak"  # Codex Speak, the app's earlier name


def _self_test():
    """Used by CI on the built app: every module imports and the system frameworks answer."""
    import ctypes

    import AVFoundation
    from ApplicationServices import AXIsProcessTrusted

    from .. import kokoro_tts
    from . import guide, host, pasteboard, permission, popup, selection, settings_ui, startup, trigger, voice  # noqa: F401
    kokoro_tts.self_test()
    AXIsProcessTrusted()
    carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
    carbon.IsSecureEventInputEnabled()
    AVFoundation.AVSpeechSynthesizer.alloc().init()
    host.MacHost()
    print("self-test ok")


def _quit_old_app():
    """Codex Speak would recreate its folder after migrate() moves it."""
    import AppKit
    from Foundation import NSDate, NSRunLoop
    apps = list(AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(OLD_BUNDLE_ID))
    for app in apps:
        app.terminate()
    deadline = time.monotonic() + 2
    while apps and time.monotonic() < deadline and not all(app.isTerminated() for app in apps):
        # isTerminated() only changes while the main run loop runs
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))


def main():
    if sys.argv[1:2] == ["--self-test"]:
        _self_test()
        return
    _quit_old_app()
    if settings.migrate():
        cfg = settings.load()
        cfg["ax_granted_build"] = None  # a new bundle id needs a first grant, not the "was updated" guide
        settings.save(cfg)
    os.makedirs(settings.DIR, exist_ok=True)
    lock = open(os.path.join(settings.DIR, "app.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # another copy runs. Opening the app through Finder reaches it as a reopen
    handler = logging.handlers.RotatingFileHandler(os.path.join(settings.DIR, "harness-discussion.log"),
                                                   maxBytes=256_000, backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, handlers=[handler],  # libraries log IPs at INFO
                        format="%(asctime)s %(levelname)s %(message)s")
    log.setLevel(logging.INFO)
    log.info("start")
    from . import permission, startup
    from .host import MacHost
    try:
        startup.refresh()
    except OSError:
        log.exception("could not update the login item")
    try:
        host = MacHost()
        app = App(host)
        host.watcher = permission.Watcher(app)
        host.watcher.tick()
        app.start()
        host.run(app.pump)
    except Exception:
        log.exception("crashed")
        raise
