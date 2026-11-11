"""App's update state: checking GitHub releases, downloading, and installing when idle."""
import shutil
import sys
import threading

from . import settings, updates


class Updates:
    def update_state(self):
        return self._update_text, self._update_busy or self._pending_update is not None

    def available_version(self):
        """macOS only: the newer release found by the last check, or None."""
        return self.update_asset and self.update_asset["version"]

    def install_update(self):
        """macOS: downloads the release the last check found and installs it as a manual update.
        False when this copy can't replace itself, so the caller opens the release page instead."""
        asset = self.update_asset
        if not asset or self._closing or self._update_busy or self._pending_update is not None:
            return True
        try:
            updates.mac_target()
        except ValueError as e:
            self._update_status(str(e))
            return False
        self._update_busy, self._update_manual = True, True
        self._update_status(f"Downloading v{asset['version']}…")

        def work():
            try:
                path, text = updates.download(asset), f"v{asset['version']} is ready to install."
            except Exception as e:
                path, text = None, f"Could not update: {e}"
            self.events.put(("update_result", path, text))
        threading.Thread(target=work, daemon=True).start()
        return True

    def _update_status(self, text):
        self._update_text = text
        if self.settings_win and self.settings_win.alive:
            self.settings_win.refresh_updates()

    def check_update(self, manual=True):
        if (not getattr(sys, "frozen", False) or self._closing or self._update_busy
                or self._pending_update is not None):
            return
        self._update_busy, self._update_manual = True, manual
        self._update_status("Checking for updates…")

        if sys.platform == "darwin":  # checks only report; Settings' Download installs
            def work():
                try:
                    asset = updates.check()
                    text = f"v{asset['version']} is available." if asset else "You are up to date."
                except updates.RateLimited:
                    asset, text = None, (
                        "Could not check for updates because GitHub is limiting requests from this network, "
                        "so try again later.")
                except Exception as e:
                    asset, text = None, f"Could not check for updates: {e}"
                self.events.put(("update_available", asset, text))
        else:
            def work():
                try:
                    asset = updates.check()
                    if asset:
                        self.events.put(("update_status", f"Downloading v{asset['version']}…"))
                        path = updates.download(asset)
                        text = f"v{asset['version']} is ready to install."
                    else:
                        path, text = None, "You are up to date."
                except updates.RateLimited:
                    path, text = None, (
                        "Could not update because GitHub is limiting requests from this network, "
                        "so try again later.")
                except Exception as e:
                    path, text = None, f"Could not update: {e}"
                self.events.put(("update_result", path, text))
        threading.Thread(target=work, daemon=True).start()

    def _auto_update(self):
        if self.cfg["auto_update"]:
            self.check_update(manual=False)
        self.host.after(24 * 60 * 60 * 1000, self._auto_update)

    def _discard_update(self):
        if self._pending_update:
            shutil.rmtree(self._pending_update.parent, ignore_errors=True)
            self._pending_update = None
            self._update_status("Automatic update cancelled.")

    def _try_update(self):
        if not self._pending_update or self._closing:
            return
        if not self._update_manual and not self.cfg["auto_update"]:
            self._discard_update()
            return
        server = self.stt
        writing = getattr(self, "rewriting", False) or getattr(self, "_recorder", None)
        if (self._read_options or self.chain.busy or self._draining_stt or writing
                or getattr(self, "meeting_busy", lambda: False)()
                or (server and not server.busy.acquire(blocking=False))):
            self._update_status("Update ready. Waiting for speech, transcription, writing, and meeting notes to finish…")
            self.host.after(1000, self._try_update)
            return
        try:
            settings.save(self.cfg)
            updates.install(self._pending_update)
        except Exception as e:
            if server:
                server.busy.release()
            self._discard_update()
            self._update_status(f"Could not install update: {e}")
            return
        self._handle("quit")
