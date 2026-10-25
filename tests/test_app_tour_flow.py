"""The Tour mixin: lifecycle, the sandbox, the save guard, first run, and the menus' events."""
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness_discussion import app_practice, app_tour, hotkeys, settings, tour, tour_copy, tour_view


class FakeTour:
    """The TourWindow stand-in: it records what the app shows, refreshes, closes and focuses."""

    def __init__(self, app):
        self.app, self.alive, self.box = app, True, None
        self.shown, self.refreshed, self.closed, self.focused = [], [], 0, 0

    def show(self, view):
        self.shown.append(view)

    def refresh(self, view):
        self.refreshed.append(view)

    def close(self):
        self.closed, self.alive = self.closed + 1, False

    def focus(self):
        self.focused += 1

    def frontmost(self):
        return True

    def hit(self, x, y):
        return None


class Base:
    """core.App's members the composition lacks; change saves through settings.save, _handle records."""

    writer_ready = False
    _last_edit = None  # Writer sets it in the real App; the tour's state reads it

    def change(self, key, value, save=True):
        self.cfg[key] = value
        self.changes.append((key, value))
        if save:
            settings.save(self.cfg)

    def _handle(self, kind, *args):
        self.handled.append((kind, args))

    def start(self):
        self.started.append(True)

    def refresh_statuses(self):
        pass

    def refresh_writer(self):
        pass

    def _try_update(self):
        self.updates.append(True)

    def _writer_info(self):
        pass

    def test_voice(self, text):
        self._read_id += 1


class _App(app_tour.Tour, app_practice.Practice, Base):
    def __init__(self, **cfg):
        self.events = queue.Queue()
        self.cfg = dict(settings.DEFAULTS, **cfg)
        self.tour, self.settings_win = None, None
        self.statuses, self.writer_statuses, self.stt_statuses = {}, {}, {}
        self.writer_sources = []
        self.changes, self.handled, self.started, self.updates, self.after_calls = [], [], [], [], []
        self._read_id = 0
        self.host = SimpleNamespace(make_tour=FakeTour, after=lambda ms, fn: self.after_calls.append((ms, fn)))

    def _ax_state(self):  # tests patch _mac() True on Windows too, where mac.permission can't import AppKit
        return True, False

    def open(self, **cfg):  # the ("tour",) event start() queues, handled as pump would
        for key, value in cfg.items():
            self.cfg[key] = value
        self._handle("tour")


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.app = _App()
        self.saved_dicts = []  # what settings.save would have written, snapshotted at save time
        patcher = patch.object(settings, "save",
                               side_effect=lambda s: self.saved_dicts.append(dict(s)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def last_saved(self):
        return self.saved_dicts[-1]


class FirstRunTest(FlowTest):
    @patch.object(_App, "_mac", return_value=True)
    def test_first_run_saves_writer_and_opens_the_tour_from_start(self, mac):
        self.app._handle("settings", True)
        self.assertTrue(self.app.cfg["writer"])
        self.assertTrue(self.app.cfg["live_check"])
        self.assertIsNone(self.app.settings_win)  # the base never opened the welcome Settings
        self.assertEqual(self.app.handled, [])
        self.app.start()
        self.assertEqual(self.app.events.get_nowait(), ("tour",))
        self.app._handle("tour")
        self.assertIsInstance(self.app.tour, FakeTour)
        self.assertEqual(self.app.tour.shown[0]["index"], 0)

    @patch.object(_App, "_mac", return_value=True)
    def test_a_host_without_make_tour_shows_the_welcome_settings(self, mac):
        del self.app.host.make_tour
        self.app._handle("settings", True)
        self.assertTrue(self.app.cfg["writer"])
        self.assertTrue(self.app.cfg["live_check"])
        self.assertEqual(self.app.handled, [("settings", (True,))])
        self.app.start()
        self.assertTrue(self.app.events.empty())

    @patch.object(_App, "_mac", return_value=True)
    def test_an_existing_user_gets_the_tour_without_touching_writer(self, mac):
        self.app.start()
        self.assertEqual(self.app.events.get_nowait(), ("tour",))
        self.assertFalse(self.app.cfg["writer"])
        self.assertTrue(self.app.events.empty())

    @patch.object(_App, "_mac", return_value=True)
    def test_a_done_tour_is_never_queued(self, mac):
        self.app.cfg["tour_done"] = True
        self.app.start()
        self.assertTrue(self.app.events.empty())


class SandboxTest(FlowTest):
    def setUp(self):
        super().setUp()
        self.app.open()

    def test_a_practice_step_turns_writer_on_in_memory_only(self):
        self.app.cfg["writer"] = False
        self.app.tour_go(3)
        self.assertTrue(self.app.cfg["writer"])
        self.assertTrue(self.app.sandboxed("writer"))
        self.app.change("speed", 1.5)
        saved = self.last_saved()
        self.assertEqual(saved["speed"], 1.5)
        self.assertFalse(saved["writer"])  # the file gets the saved value, never the practice one
        self.assertTrue(self.app.cfg["writer"])  # memory keeps the practice value
        self.app.tour_action("keep_writer")
        self.assertTrue(self.last_saved()["writer"])
        self.assertFalse(self.app.sandboxed("writer"))

    def test_the_review_step_keeps_the_rewritten_box_text(self):
        self.app.open()
        self.app.tour.box = SimpleNamespace(text=lambda: "Fixed text.")
        self.app.tour_go(3)
        self.app._last_edit = (app_practice.practice.PracticeCtx("x", 1, -1, True, False), "x", "Fixed text.", "Fix")
        self.app.tour_next()
        self.assertEqual(self.app.tour.shown[-1]["box"], {"text": "Fixed text.", "select": None})

    def test_closing_restores_the_remaining_practice_values(self):
        self.app.cfg.update(writer=False, live_check=False)
        self.app.tour_go(6)  # both keys sandboxed
        self.app.tour_action("keep_writer")
        self.app.tour_closed()
        self.assertFalse(self.app.cfg["live_check"])
        self.assertFalse(self.app.sandboxed("live_check"))
        self.assertIsNone(self.app.tour)

    def test_the_update_installer_waits_for_the_sandbox_to_end(self):
        self.app.cfg["writer"] = False
        self.app.tour_go(3)
        self.app._try_update()
        self.assertEqual(self.app.after_calls, [(1000, self.app._try_update)])
        self.assertEqual(self.app.updates, [])
        self.app.tour_closed()
        self.app._try_update()
        self.assertEqual(self.app.updates, [True])


class NavigationTest(FlowTest):
    def setUp(self):
        super().setUp()
        self.app.open()
        mac = patch.object(_App, "_mac", return_value=True)  # the welcome branch differs per OS
        mac.start()
        self.addCleanup(mac.stop)

    def test_skip_on_an_untried_step_marks_it_skipped(self):
        self.app.tour_go(1)
        self.app.tour_skip()
        self.assertEqual(self.app.cfg["tour_steps"]["voice"], "skipped")
        self.assertEqual(self.app.tour.shown[-1]["index"], 2)

    def test_next_on_a_tried_step_keeps_the_mark(self):
        self.app.tour_mark("voice")
        self.app.tour_skip()
        self.assertEqual(self.app.cfg["tour_steps"]["voice"], "tried")

    def test_a_closed_tour_resumes_where_it_left_off(self):
        self.app.tour_next()  # welcome is untried: skipped
        self.app.tour_closed()
        self.assertFalse(self.app.cfg["tour_done"])
        app = _App(tour_steps=dict(self.app.cfg["tour_steps"]))
        app.start()
        app._handle(app.events.get_nowait()[0])
        self.assertEqual(app.tour.shown[0]["index"], 1)  # resume_at skips the marked step

    def test_skip_all_and_finish_save_tour_done(self):
        self.app.tour_skip_all()
        self.assertTrue(self.last_saved()["tour_done"])
        app = _App()
        app.tour_finish()
        self.assertEqual(app.changes[-1], ("tour_done", True))

    def test_try_returns_to_the_done_screen(self):
        self.app.tour_try("dictate")
        self.app.tour_next()
        self.assertEqual(self.app.tour.shown[-1]["index"], len(tour.STEPS))

    def test_replay_opens_at_the_first_step_even_after_a_close(self):
        self.app.tour_closed()
        self.app._handle("tour_replay")
        self.assertEqual(self.app.tour.shown[0]["index"], 0)

    def test_replay_on_an_open_tour_focuses_it(self):
        self.app.tour_go(2)
        self.app._handle("tour_replay")
        self.assertEqual(self.app.tour.focused, 1)
        self.assertEqual(self.app.tour.shown[-1]["index"], 2)  # the step is unchanged


class VoiceAndGuideTest(FlowTest):
    def setUp(self):
        super().setUp()
        self.app.open()

    def test_a_successful_test_voice_marks_the_voice_step(self):
        self.app.tour_go(1)
        self.app.test_voice(tour_copy.VOICE)
        self.app._handle("done", None, self.app._tour_voice_rid)
        self.assertEqual(self.app.cfg["tour_steps"]["voice"], "tried")
        self.assertEqual(self.app.handled[-1], ("done", (None, self.app._tour_voice_rid)))
        app = _App()
        app.open()
        app.tour_go(1)
        app.test_voice(tour_copy.VOICE)
        app._handle("done", "boom", app._tour_voice_rid)
        self.assertEqual(app.cfg["tour_steps"], {})

    def test_a_voice_row_fix_reaches_the_source(self):
        self.app.fix_source = Mock()
        self.app.statuses = {"Cursor": "signed_out"}
        with patch.object(self.app, "_mac", return_value=False):
            view = tour_view.view(1, self.app.tour_state())
        _, (name, arg) = view["rows"][0]["actions"][0]  # the voice step's "Sign in…" on the Cursor row
        self.app.tour_action(name, arg)
        self.app.fix_source.assert_called_once_with("Cursor")

    def test_a_close_with_access_still_missing_shows_the_guide(self):
        self.app.host.watcher = SimpleNamespace(state=False, show_guide=Mock())
        self.app.tour_closed()
        self.app.host.watcher.show_guide.assert_called_once_with()


class KeepLiveTest(FlowTest):
    def test_the_live_step_sandboxes_and_keeps_both_keys(self):
        self.app.open(writer=False, live_check=False)
        self.app.tour_go(6)
        self.assertTrue(self.app.sandboxed("writer", "live_check"))
        self.app.tour_action("keep_live")
        self.assertEqual(self.app.changes[-2:], [("writer", True), ("live_check", True)])
        self.assertFalse(self.app.sandboxed("writer", "live_check"))

    def test_live_check_already_on_sandboxes_only_writer(self):
        self.app.open(writer=False, live_check=True)
        self.app.tour_go(6)
        self.assertTrue(self.app.sandboxed("writer"))
        self.assertFalse(self.app.sandboxed("live_check"))


class ShortcutTest(FlowTest):
    def setUp(self):
        super().setUp()
        self.app.open()
        self.app.cfg["writer"] = False
        self.app.tour_go(3)  # writer is sandboxed on the rewrite step

    def test_a_shortcut_change_saves_the_real_writer_and_queues_a_refresh(self):
        self.app.tour_action("shortcut", ("rewrite_hotkey", "<ctrl>+<alt>+e"))
        saved = self.last_saved()
        self.assertEqual(saved["rewrite_hotkey"], "<ctrl>+<alt>+e")
        self.assertFalse(saved["writer"])
        kind, fn = self.app.events.get_nowait()
        self.assertEqual(kind, "call")
        self.assertEqual(self.app.events.qsize(), 0)
        fn()
        self.assertIn(hotkeys.label("<ctrl>+<alt>+e"), self.app.tour.refreshed[-1]["lead"])

    def test_the_open_settings_window_shows_the_new_shortcut(self):
        win = SimpleNamespace(alive=True, cfg=dict(self.app.cfg), shortcut_labels={"rewrite_hotkey": None},
                              _show_shortcut=Mock())
        self.app.settings_win = win
        self.app.tour_action("shortcut", ("rewrite_hotkey", "<ctrl>+<alt>+e"))
        self.assertEqual(win.cfg["rewrite_hotkey"], "<ctrl>+<alt>+e")
        win._show_shortcut.assert_called_once_with("rewrite_hotkey")
        win2 = SimpleNamespace(alive=True, cfg=dict(self.app.cfg),
                               shortcuts={"rewrite_hotkey": {"label": {"text": ""}, "error": None}})
        self.app.settings_win = win2
        self.app.tour_action("shortcut", ("rewrite_hotkey", "y"))
        self.assertEqual(win2.shortcuts["rewrite_hotkey"]["label"]["text"], hotkeys.label("y"))

    def test_a_settings_change_queues_a_refresh_only_while_the_tour_is_open(self):
        self.app.change("hotkey", "<ctrl>+<alt>+r")
        self.assertEqual(self.app.events.get_nowait(), ("call", self.app._tour_refresh))
        self.app.tour_closed()
        self.app.change("hotkey", "<ctrl>+<alt>+r")
        self.assertTrue(self.app.events.empty())


if __name__ == "__main__":
    unittest.main()