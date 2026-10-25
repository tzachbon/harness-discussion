"""The Mac tour: its guide hook, window, practice field, shortcut rows and menu item."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

if sys.platform != "darwin":
    raise unittest.SkipTest("macOS only")

from harness_discussion import hotkeys, settings, tour, tour_view  # noqa: E402
from harness_discussion.mac import permission  # noqa: E402

CFG = dict(settings.DEFAULTS, hotkey="<cmd>+<alt>+r", rewrite_hotkey="<cmd>+<alt>+e", dictate_hotkey="<cmd>+<alt>+d")


def watcher(tour_pending):
    """A Watcher over a stub app whose tour is or is not pending."""
    app = SimpleNamespace(tour_pending=tour_pending, cfg={},
                          host=SimpleNamespace(after=lambda ms, fn: None,
                                               set_permission_missing=lambda missing: None))
    return permission.Watcher(app)


class GuideHookTest(unittest.TestCase):
    def test_the_guide_waits_while_the_tour_is_pending(self):
        w = watcher(lambda: True)
        with patch.object(permission, "trusted", return_value=False), \
             patch("harness_discussion.mac.guide.GuideWindow") as guide:
            w.tick()
        guide.assert_not_called()

    def test_the_guide_shows_when_no_tour_is_pending(self):
        w = watcher(lambda: False)
        with patch.object(permission, "trusted", return_value=False), \
             patch("harness_discussion.mac.guide.GuideWindow") as guide:
            w.tick()
        guide.assert_called_once()
        self.assertIs(w.guide, guide.return_value)


class _App:
    """The parts of app_tour.App the window calls."""

    def __init__(self):
        self.cfg = dict(CFG)
        self.actions, self.applied, self.selected, self.closed = [], [], [], 0
        self.host = SimpleNamespace(confirm=lambda *a, **k: False)

    def tour_action(self, name, arg=None):
        self.actions.append((name, arg))

    def tour_closed(self):
        self.closed += 1

    def apply(self, key, value):
        self.applied.append((key, value))

    def _selected(self, x, y):
        self.selected.append((x, y))

    def tour_next(self):
        pass

    tour_skip = tour_skip_all = tour_finish = tour_next


def state(app, **over):
    base = dict(mac=True, trusted=True, stale=False, cfg=app.cfg, progress={}, statuses={}, writer_statuses={},
                stt_statuses={}, sandboxed=set(), voice_source="Cursor", writer_source="Cursor", stt_source="Local",
                has_practice_edit=False, sample_used=set())
    base.update(over)
    return SimpleNamespace(**base)


def view(app, step, **over):
    return tour_view.view(tour.STEPS.index(step) if step else len(tour.STEPS), state(app, **over))


class _TourCase(unittest.TestCase):
    def setUp(self):
        import AppKit
        from harness_discussion.mac import tour as mac_tour
        from harness_discussion.mac import ui
        AppKit.NSApplication.sharedApplication()
        p = patch.object(ui, "show", lambda w: w.orderFront_(None))  # visible without activating the test run
        p.start()
        self.addCleanup(p.stop)
        self.app = _App()
        self.t = mac_tour.TourWindow(self.app)
        self.addCleanup(self.t.close)

    def click(self, control):
        control.sendAction_to_(control.action(), control.target())


class TourWindowTest(_TourCase):
    def test_the_window_is_dark_with_a_transparent_title_bar(self):
        import AppKit
        win = self.t.win
        self.assertTrue(win.titlebarAppearsTransparent())
        self.assertTrue(win.styleMask() & AppKit.NSWindowStyleMaskFullSizeContentView)
        self.assertEqual(win.appearance().name(), AppKit.NSAppearanceNameDarkAqua)

    def test_the_done_screen_lists_every_step(self):
        self.t.show(view(self.app, None, progress={"welcome": "tried", "voice": "skipped"}))
        self.assertEqual(len(self.t.done_rows), 7)
        self.assertIsNone(self.t.box)

    def test_every_step_draws_and_refreshes(self):
        for step, over in ((None, {}), ("welcome", {"trusted": False, "stale": True}),
                           ("voice", {"statuses": {"Codex": "signed_out"}, "progress": {"voice": "tried"}}),
                           ("read", {}), ("rewrite", {"writer_source": None, "sandboxed": {"writer"}}),
                           ("review", {"has_practice_edit": True}), ("dictate", {"trusted": False, "stt_source": None}),
                           ("live", {"sandboxed": {"live_check"}})):
            with self.subTest(step=step):
                v = view(self.app, step, **over)
                self.t.show(v)
                self.t.refresh(v)
                self.assertEqual(self.t.box is not None, v["box"] is not None)
                self.assertEqual(v["box"] is not None, step in tour.PRACTICE_STEPS and step != "review")

    def test_keep_live_asks_first(self):
        self.t.show(view(self.app, "live", sandboxed={"writer", "live_check"}))
        switch = self.t.keep_switch
        self.app.host.confirm = lambda *a, **k: False
        switch.setState_(1)
        self.click(switch)
        self.assertEqual(switch.state(), 0)
        self.assertEqual(self.app.actions, [])
        self.app.host.confirm = lambda *a, **k: True
        switch.setState_(1)
        self.click(switch)
        self.assertEqual(self.app.actions, [("keep_live", None)])

    def test_a_user_close_calls_back_once_and_close_does_not(self):
        self.t.show(view(self.app, "read"))
        self.t.win.performClose_(None)
        self.assertEqual(self.app.closed, 1)
        self.assertFalse(self.t.alive)
        self.t.show(view(self.app, "read"))
        self.t.close()
        self.assertEqual(self.app.closed, 1)

    def test_hit_tells_the_box_from_the_window(self):
        from harness_discussion.mac import trigger
        self.t.show(view(self.app, "read"))
        left, top, right, bottom = self.t.box.screen_rect()
        self.assertEqual(self.t.hit((left + right) / 2, (top + bottom) / 2), "box")
        f = self.t.win.frame()
        x, y = trigger.top_left(SimpleNamespace(x=f.origin.x + 5, y=f.origin.y + 5))
        self.assertEqual(self.t.hit(x, y), "window")
        self.assertIsNone(self.t.hit(f.origin.x - 50, y))


class PracticeFieldTest(_TourCase):
    def setUp(self):
        super().setUp()
        self.t.show(view(self.app, "read"))
        self.box = self.t.box

    def test_set_replaces_and_moves_the_caret(self):
        self.box.load("Hello", None)
        self.box._set(0, 2, "Hi")
        self.assertEqual(self.box.text(), "Hillo")
        self.assertEqual(self.box.selection(), (2, 2))

    def test_offsets_are_utf16(self):
        self.box.load("😀 recieved", None)
        self.box._select(3, 11)
        self.assertEqual(self.box.selection(), (3, 11))
        self.assertEqual(self.box.selected_text(), "recieved")

    def test_bounds(self):
        self.box.load("😀 recieved", None)
        field = self.box.field()
        (rects,) = self.box.bounds(field, [(3, 11, "recieved")])
        self.assertEqual(len(rects), 1)
        left, top, right, bottom = rects[0]
        self.assertLess(left, right)
        self.assertLess(top, bottom)
        self.assertEqual(self.box.bounds(field, [(3, 11, "received")]), [None])
        self.box._set(0, 0, "x")
        self.assertIsNone(self.box.bounds(field, [(3, 11, "recieved")]))

    def test_a_drag_select_opens_the_bar(self):
        self.box.load("😀 recieved", None)
        self.box._select(3, 11)
        self.box._after_mouse()
        self.assertEqual(len(self.app.selected), 1)
        self.box._select(5, 5)
        self.box._after_mouse()
        self.assertEqual(len(self.app.selected), 1)

    def test_the_field_is_ours(self):
        from harness_discussion import practice
        self.assertTrue(practice.ours(self.box.field()))

    def test_load_selects_as_the_view_says(self):
        self.box.load("abc", "all")
        self.assertEqual(self.box.selection(), (0, 3))
        self.box.load("abc", "end")
        self.assertEqual(self.box.selection(), (3, 3))


class ShortcutRowTest(_TourCase):
    def setUp(self):
        super().setUp()
        self.t.show(view(self.app, "rewrite"))
        self.label = self.t.shortcut_labels["rewrite_hotkey"]

    def test_the_tour_reuses_the_settings_recorder(self):
        from harness_discussion.mac.reading_page import ReadingPage
        self.assertIsInstance(self.t, ReadingPage)
        self.assertEqual(self.label.stringValue(), hotkeys.label(self.app.cfg["rewrite_hotkey"]))

    def test_a_conflict_is_refused_and_a_free_chord_saves(self):
        self.t._set_hotkey(self.app.cfg["hotkey"], "rewrite_hotkey")
        self.assertEqual(self.label.stringValue(), "Used by Read shortcut")
        self.assertEqual(self.app.actions, [])
        self.t._set_hotkey("<ctrl>+<alt>+e", "rewrite_hotkey")
        self.assertEqual(self.app.actions, [("shortcut", ("rewrite_hotkey", "<ctrl>+<alt>+e"))])

    def test_show_ends_a_recording(self):
        saved = self.app.cfg["rewrite_hotkey"]
        self.t._record_hotkey("rewrite_hotkey")
        self.assertEqual(self.app.applied, [("rewrite_hotkey", "")])
        self.t.show(view(self.app, "review"))
        self.assertIsNone(self.t._recorder)
        self.assertEqual(self.app.applied[-1], ("rewrite_hotkey", saved))

    def test_refresh_keeps_a_conflict_and_follows_outside_changes(self):
        self.t._set_hotkey(self.app.cfg["hotkey"], "rewrite_hotkey")
        self.t.refresh(view(self.app, "rewrite"))
        self.assertEqual(self.label.stringValue(), "Used by Read shortcut")
        self.app.cfg["rewrite_hotkey"] = "<ctrl>+<alt>+e"
        self.t.refresh(view(self.app, "rewrite"))
        self.assertEqual(self.label.stringValue(), hotkeys.label("<ctrl>+<alt>+e"))

    def test_a_user_close_after_change_and_esc_still_calls_back(self):
        self.t._record_hotkey("rewrite_hotkey")
        self.t._stop_recording()  # what Esc does
        self.t.win.performClose_(None)
        self.assertEqual(self.app.closed, 1)


class MenuTest(unittest.TestCase):
    def test_take_the_tour_follows_settings(self):
        import AppKit
        from harness_discussion.mac import host
        h = host.MacHost()
        self.addCleanup(lambda: AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(h.item))
        titles = [i.title() for i in h.menu.itemArray()]
        self.assertEqual(titles[titles.index("Settings…") + 1], "Take the Tour")
        self.assertTrue(callable(h.make_tour))
