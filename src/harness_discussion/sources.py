"""App's voice-source state: the cached statuses, what makes a source usable, and the floating bar's source button."""
import atexit
import logging
import subprocess
import threading
import webbrowser

from . import kokoro_tts

log = logging.getLogger("harness_discussion")
INSTALL = {"Codex": "https://github.com/openai/codex#quickstart", "Cursor": "https://cursor.com/download"}
RECHECK_S = (5, 15, 30, 60, 120)  # after a fix: installs and sign-ins finish outside the app


def _codex_login():
    """`codex login` opens the ChatGPT sign-in in the browser and exits once it is saved."""
    from .codex_rt import SPAWN, codex_exe
    p = subprocess.Popen([codex_exe(), "login"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **SPAWN)
    atexit.register(p.kill)  # quitting the app ends an unfinished sign-in

    def wait():
        try:
            p.wait(600)
        except subprocess.TimeoutExpired:
            p.kill()  # an abandoned sign-in must not keep its port forever
    threading.Thread(target=wait, daemon=True).start()


def fix(name, status):
    """(button title, action) that makes a source usable, or None when there is nothing to do."""
    if status == "missing" and name in INSTALL:
        return "Install…", lambda: webbrowser.open(INSTALL[name])
    if status in ("signed_out", "expired") and name == "Cursor":
        return "Sign in…", lambda: webbrowser.open("cursor://")  # Cursor refreshes or asks for its sign-in
    if status in ("signed_out", "expired") and name == "Codex":
        return "Sign in…", _codex_login
    if status == "not_downloaded" and name == "Kokoro":
        return "Download…", kokoro_tts.start_download
    return None


class Sources:
    """Mixed into core.App, which owns chain, cfg, popup, events and change()."""

    def refresh_statuses(self):
        """Settings shows the cached result: status() can take seconds, as macOS asks a login shell for codex."""
        if not self._checking.acquire(blocking=False):
            return  # the running check reports soon enough
        def work():
            statuses = {}
            try:
                for e in self.chain.engines:
                    try:
                        statuses[e.name] = e.status() if hasattr(e, "status") else "ready"
                    except Exception as err:
                        log.warning("%s status failed: %s", e.name, type(err).__name__)
                        statuses[e.name] = "missing"
                self.statuses = self.chain.statuses = statuses
            finally:
                self._checking.release()
            self.events.put(("statuses",))
        threading.Thread(target=work, daemon=True).start()

    _poll_pending = False

    def _poll_while_downloading(self):
        """Main thread: one re-check every 2 s until the model download ends, however many checks report it."""
        if "downloading" not in self.statuses.values() or self._poll_pending:
            return
        self._poll_pending = True

        def poll():
            self._poll_pending = False
            self.refresh_statuses()
        self.host.after(2000, poll)

    def _show_source(self):
        """Idle bar label: the source the next read starts with. While reading, engine events set it."""
        if not self.chain.busy:
            self.popup.set_source(self.chain.order()[0].name)

    def fix_source(self, name):
        """Runs the source's install or sign-in, then checks again while the user finishes it."""
        found = fix(name, self.statuses.get(name))
        if not found:
            return
        try:
            found[1]()
        except Exception as e:  # codex removed since the last check, or no browser
            self.host.notify(f"Could not open {name}: {e}")
            return
        for s in RECHECK_S:
            self.host.after(s * 1000, self.refresh_statuses)

    def _source_choices(self):
        """The bar's menu as (name, fix title or None): enabled sources not known to be unready, in the saved
        order, else what a read would use; then each enabled source that needs an install or sign-in."""
        on = [name for name, enabled in self.cfg["sources"] if enabled]
        fixes = [(name, found[0]) for name in on if (found := fix(name, self.statuses.get(name)))]
        broken = {name for name, _ in fixes}
        ready = [(name, None) for name in on if self.statuses.get(name, "ready") == "ready"]
        return (ready or [(e.name, None) for e in self.chain.order() if e.name not in broken]) + fixes

    def _popup_source(self, name):
        if fix(name, self.statuses.get(name)):
            return self.fix_source(name)
        sources = self.cfg["sources"]
        tag = self._read_options and self._read_options[0]
        start = self._sentence  # the sentence on screen, not the start of a long piece
        reading = bool(self._read_options)
        top = next((getattr(e, "max_speed", None) for e in self.chain.engines if e.name == name), None)
        if top and self.cfg["speed"] > top:  # else order() skips the pick and the bar snaps back (Codex is 1× only)
            self.change("speed", top)
        self.change("sources", [[name, True]] + [s for s in sources if s[0] != name])
        if reading:  # a read in progress starts over from that sentence on the picked source, ⏮ still reaching back
            paused = self.popup.paused
            self.play(self._read_text, tag, start=start)
            if paused:
                self.pause()
            self.popup.set_playing(True, paused=paused)
            self.popup.set_loading(not paused)
