"""app_live.App is core.App with Check as you type, run through its real event loop with a fake host."""
import unittest
from types import SimpleNamespace

from harness_discussion import app_live
from tests.test_app_live import FakeField, FakeMarks, Toast, threading_wait, window
from tests.test_app_writer import Source


class RealApp(unittest.TestCase):
    """app_live.App is core.App with Check as you type: the platform entry points start this class."""

    def make(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from harness_discussion import settings
        from tests.test_core import Engine, FakeHost
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "settings.json"
        cfg = {**settings.DEFAULTS, "sources": [["macOS", True]], "writer": True, "live_check": True}
        path.write_text(json.dumps(cfg), encoding="utf-8")
        for p in (patch.object(settings, "PATH", str(path)), patch.object(settings, "load", return_value=cfg),
                  patch.object(settings, "save")):
            p.start()
            self.addCleanup(p.stop)
        host = FakeHost([Engine("macOS")])
        self.marks, self.field = FakeMarks(), FakeField("i thinks so, ok")
        host.edit, host.make_marks = self.field, lambda: self.marks
        host.make_toast, host.make_review = Toast, window
        host.esc_watcher = lambda fn: SimpleNamespace(start=lambda: None, stop=lambda: None)
        host.set_shortcut = lambda *a, **k: None
        make_popup = host.make_popup

        def popup(speed, **callbacks):
            p = make_popup(speed, **callbacks)
            for name in ("set_last_mode", "set_rewriting", "configure_writer", "set_writer"):
                setattr(p, name, lambda *a, **k: None)
            return p
        host.make_popup = popup
        app = app_live.App(host)
        app._live_stop.set()
        return app

    def test_the_app_runs_check_as_you_type_through_its_events_and_clicks(self):
        app = self.make()
        self.assertTrue(app.live_ready)
        app.writer_sources = [Source("Cursor", "I think so, ok")]
        app.cfg["writer_sources"] = [["Cursor", True]]
        text, self.field.text = self.field.text, self.field.text[:-1]
        app._live_tick(now=-1)
        self.field.text = text
        app._live_tick(now=0)
        app._live_tick(now=2)
        for _ in range(100):
            if app._live_checking is None:
                break
            threading_wait()
        app._live_tick(now=3)
        app.pump()
        (key, (left, top, _, _)), = app.marks.shown[0]
        app.events.put(("press", left + 1, top + 1))
        app.pump()
        self.assertEqual(app.toast.shown[-1][0], "i thinks → I think")
        app.change("live_check", False, save=False)
        self.assertIsNone(self.marks.shown)
        app.events.put(("quit",))
        app.pump()
        self.assertTrue(app._live_stop.is_set())


if __name__ == "__main__":
    unittest.main()
