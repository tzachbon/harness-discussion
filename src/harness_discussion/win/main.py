"""Windows entry point: single instance, the installer's --startup and --local-only commands, logging, DPI."""
import ctypes
import logging
import logging.handlers
import os
import sys
import threading
import time
from ctypes import wintypes

from .. import settings
from ..app_tour import App
from ..core import log
from .host import TkHost

k32 = ctypes.windll.kernel32
for _f in (k32.CreateMutexW, k32.CreateEventW, k32.OpenEventW):
    _f.restype = wintypes.HANDLE
SHOW_SETTINGS = "harness-discussion-show-settings"  # a second launch sets this to open Settings


def _wait_show_settings(app, show_event):
    while k32.WaitForSingleObject(show_event, 0xFFFFFFFF) == 0:
        app.events.put(("settings",))


def main():
    settings.migrate()  # before anything reads or creates the new folders
    if sys.argv[1:2] == ["--startup"]:  # used by the installer: HarnessDiscussion.exe --startup on|off|refresh
        try:
            if sys.argv[2:3] == ["refresh"]:
                settings.refresh_startup()
            else:
                settings.set_startup(sys.argv[2:3] == ["on"])
        except Exception:
            sys.exit(1)  # a windowed exe would show a dialog that blocks setup; the user can retry in Settings
        return
    if sys.argv[1:2] == ["--local-only"]:  # the installer's privacy option: only voices that run on this PC
        try:
            settings.local_only()
        except Exception:
            sys.exit(1)  # Settings → Voices can still turn the online sources off
        return
    mutex = k32.CreateMutexW(None, False, "harness-discussion-single-instance")
    if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS: ask the running copy to open Settings
        for _ in range(50):  # the running copy may still be starting up
            ev = k32.OpenEventW(0x0002, False, SHOW_SETTINGS)  # EVENT_MODIFY_STATE
            if ev:
                k32.SetEvent(ev)
                k32.CloseHandle(ev)
                break
            time.sleep(0.1)
        return
    show_event = k32.CreateEventW(None, False, False, SHOW_SETTINGS)  # before the slow start-up
    os.makedirs(settings.DIR, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(os.path.join(settings.DIR, "harness-discussion.log"),
                                                   maxBytes=256_000, backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, handlers=[handler],  # libraries log IPs at INFO
                        format="%(asctime)s %(levelname)s %(message)s")
    log.setLevel(logging.INFO)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # tk and the mouse hook agree on pixels
    log.info("start, launch command %s", settings.launch_command())
    try:
        settings.refresh_startup()
    except OSError:
        log.exception("could not update the sign-in task")
    try:
        host = TkHost()
        app = App(host)
        threading.Thread(target=_wait_show_settings, args=(app, show_event), daemon=True).start()
        app.start()
        host.run(app.pump)
    except Exception:
        log.exception("crashed")
        raise
    k32.CloseHandle(mutex)
