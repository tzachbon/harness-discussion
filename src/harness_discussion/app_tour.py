"""The Tour mixin: the Get Started tour's lifecycle, the practice sandbox and its save guard, and the App
both entry points build. `Tour` is mixed in before `Practice` (T3) and before app_live.App."""
import sys
from types import SimpleNamespace

from . import app_live, app_practice, hotkeys, practice, tour, tour_copy, tour_view, writer


class Tour:
    TOUR_EVENTS = {"tour": "open_tour", "tour_replay": "replay_tour"}
    tour, _tour_index, _tour_dismissed, _tour_back_to_done = None, 0, False, False
    _sandbox = {}  # key -> saved value, while the tour has the setting on for practice; replaced per App in open_tour
    _sample_used = ()

    def _handle(self, kind, *args):
        if kind == "settings" and args:  # ("settings", True): only core's first-run welcome, new installs only
            self.change("writer", True)  # decided: every feature is on for new installs (live_check is already on)
            if self.tour_pending():
                return  # the tour opens from start() instead of the welcome Settings
        if kind == "done" and args[1:2] == (getattr(self, "_tour_voice_rid", None),) and not args[0]:
            self.tour_mark("voice")
        if kind in self.TOUR_EVENTS:
            return getattr(self, self.TOUR_EVENTS[kind])()
        super()._handle(kind, *args)
        if kind == "statuses":
            self._tour_refresh()

    def start(self):
        super().start()
        if self.tour_pending():
            self.events.put(("tour",))

    def tour_pending(self):
        """The tour will open, or is open, this run. The Mac guide waits for it."""
        return hasattr(self.host, "make_tour") and not self.cfg["tour_done"] and not self._tour_dismissed

    def open_tour(self, at=None):
        if self.tour and self.tour.alive:
            return self.tour.focus()
        self._sandbox, self._sample_used = {}, set()
        self.tour = self.host.make_tour(self)
        self.refresh_statuses()
        if self.writer_ready:
            self.refresh_writer()
        self.tour_go(tour.resume_at(self.cfg["tour_steps"]) if at is None else at)

    def replay_tour(self):
        self._tour_dismissed = False
        self.open_tour(at=0)

    def tour_step(self):
        return tour.STEPS[self._tour_index] if self.tour and self._tour_index < len(tour.STEPS) else None

    def tour_go(self, index):
        self._tour_index = index
        step = self.tour_step()
        if step in ("rewrite", "review", "dictate", "live"):
            self._sandbox_on("writer")
        if step == "live":
            self._sandbox_on("live_check")
        self.tour.show(tour_view.view(index, self.tour_state()))

    def tour_next(self):
        step = self.tour_step()
        if step == "welcome" and not self._mac():
            self.tour_mark("welcome")
        elif step and self.cfg["tour_steps"].get(step) != tour.TRIED:
            self.tour_mark(step, tour.SKIPPED)  # Next before success counts as Skip
        back, self._tour_back_to_done = self._tour_back_to_done, False
        self.tour_go(len(tour.STEPS) if back else self._tour_index + 1)

    def tour_skip(self):
        self.tour_next()  # same rule: an untried step becomes skipped

    def tour_try(self, step):
        self._tour_back_to_done = True
        self.tour_go(tour.STEPS.index(step))

    def tour_skip_all(self):
        self.change("tour_done", True)
        self._tour_close()

    tour_finish = tour_skip_all

    def tour_closed(self):  # the user closed the window
        self._tour_close(window_gone=True)

    def _tour_close(self, window_gone=False):
        self._sandbox_end()
        self._tour_dismissed = True
        if not window_gone and self.tour:
            self.tour.close()
        self.tour = None
        watcher = getattr(self.host, "watcher", None)  # Mac: the guide shows now if access is still missing
        if watcher is not None and not watcher.state:
            watcher.show_guide()

    def tour_mark(self, step, how=None):
        # how=None means tried; the class attr `tour` (the window) shadows the module in the class body
        self.change("tour_steps", tour.mark(self.cfg["tour_steps"], step, how or tour.TRIED))
        self._tour_refresh()

    # sandbox: settings on for practice only
    def sandboxed(self, *keys):
        return any(k in self._sandbox for k in keys)

    def _sandbox_on(self, key):
        if not self.cfg[key] and key not in self._sandbox:
            self._sandbox[key] = self.cfg[key]
            super().change(key, True, save=False)

    def _sandbox_end(self):
        saved, self._sandbox = self._sandbox, {}
        for key, value in saved.items():
            super().change(key, value, save=False)

    def change(self, key, value, save=True):
        self._sandbox.pop(key, None)  # an explicit change (Keep, Settings) ends that key's practice value
        if key in hotkeys.SHORTCUTS and self.tour:
            self.events.put(("call", self._tour_refresh))  # after the recorder finishes, so a redraw can't cut it off
        if not (save and self._sandbox):
            return super().change(key, value, save)
        practice_values = {k: self.cfg[k] for k in self._sandbox}
        self.cfg.update(self._sandbox)  # the file gets the saved values, never a practice one
        try:
            super().change(key, value, save)
        finally:
            self.cfg.update(practice_values)

    def tour_shortcut(self, key, spec):
        """Change… in the tour saves the way Settings does. An open Settings window gets the value too."""
        self.change(key, spec)
        win = self.settings_win
        if win and win.alive:
            win.cfg[key] = spec  # its copy decides conflicts there: a stale one could save a duplicate shortcut
            if key in getattr(win, "shortcut_labels", {}):  # Mac
                win._show_shortcut(key)
            elif key in getattr(win, "shortcuts", {}):  # Windows
                win.shortcuts[key]["label"]["text"] = hotkeys.label(spec)

    def _try_update(self):
        if self._sandbox:  # it saves self.cfg before installing: wait until the tour closes
            return self.host.after(1000, self._try_update)
        super()._try_update()

    def _writer_info(self):
        super()._writer_info()
        self._tour_refresh()

    def _tour_refresh(self):
        t = self.tour
        if t is not None and t.alive:
            t.refresh(tour_view.view(self._tour_index, self.tour_state()))

    def tour_sample(self, step):
        """A canned result stood in: the step's view shows its sample variant."""
        self._sample_used = {*self._sample_used, step}

    def _mac(self):
        return sys.platform == "darwin"

    def tour_state(self):
        trusted, stale = self._ax_state()
        return SimpleNamespace(mac=self._mac(), trusted=trusted, stale=stale, cfg=self.cfg,
                               progress=self.cfg["tour_steps"], statuses=self.statuses,
                               writer_statuses=self.writer_statuses, stt_statuses=self.stt_statuses,
                               sandboxed=set(self._sandbox), voice_source=self.tour_voice_source(),
                               writer_source=self.tour_writer_source(), stt_source=self.tour_stt_source(),
                               has_practice_edit=self._last_edit is not None
                               and isinstance(self._last_edit[0], practice.PracticeCtx),
                               sample_used=self._sample_used)

    def _ax_state(self):
        """(trusted, stale) for the view; (True, False) anywhere but a Mac."""
        if not self._mac():
            return True, False
        from .mac import permission
        return (permission.trusted(),
                permission.is_stale(self.cfg.get("ax_granted_build"), permission.current_build_id()))

    def tour_voice_source(self):
        return next((name for name, on in self.cfg["sources"]
                     if on and self.statuses.get(name) == "ready"), None)

    def tour_writer_source(self):
        return next((s.name for s in writer.chain(self.writer_sources, self.cfg["writer_sources"])
                     if self.writer_statuses.get(s.name) == "ready"), None)

    def tour_stt_source(self):
        return next((name for name, on in self.cfg["stt_sources"]
                     if on and self.stt_statuses.get(name) == "ready"), None)

    def tour_action(self, name, arg=None):
        """The windows' buttons, on the main thread."""
        if name == "play":
            self.test_voice(tour_copy.VOICE)
        elif name == "review":
            self.open_review()
        elif name == "keep_writer":
            self.change("writer", True)
        elif name == "keep_live":
            if self.sandboxed("writer") or not self.cfg["writer"]:
                self.change("writer", True)  # the OS view has already confirmed; live_check comes with writer
            self.change("live_check", True)
        elif name == "shortcut":
            self.tour_shortcut(*arg)
        elif name == "start_over":
            self.tour_go(self._tour_index)
        elif name == "try":
            self.tour_try(arg)
        elif name == "fix_source":
            self.fix_source(*arg)
        elif name == "fix_writer":
            self.fix_writer(*arg)
        elif name == "settings":
            self.events.put(("settings",))
        elif name == "allow":
            watcher = getattr(self.host, "watcher", None)  # Mac: step 1's Allow access opens the guide
            if watcher is not None:
                watcher.show_guide()


class App(Tour, app_practice.Practice, app_live.App):
    """core.App with Check as you type and the Get Started tour."""