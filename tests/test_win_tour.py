"""The Windows tour window: the practice box primitives, the dark frame, the tray item and the shortcut rows."""
import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

import ctypes
import gc
import os
import queue
import tkinter as tk
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from harness_discussion import hotkeys, settings, tour_copy, tour_view, writer_settings
from harness_discussion.win import host, practice_field, tour, tour_steps
from tests.test_win_writer_ui import click, labels


def state(cfg, **kw):
    s = dict(mac=False, trusted=True, stale=False, cfg=cfg, progress={}, statuses={}, writer_statuses={},
             stt_statuses={}, sandboxed=set(), voice_source=None, writer_source=None, stt_source=None,
             has_practice_edit=False, sample_used=set())
    return SimpleNamespace(**{**s, **kw})


READ, REWRITE, DICTATE, LIVE, DONE = 2, 3, 5, 6, 7


class TourBase(unittest.TestCase):
    def setUp(self):
        win_key = patch("harness_discussion.win.shortcut_row.win_down", return_value=False)
        win_key.start()
        self.addCleanup(win_key.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = Mock(cfg=deepcopy(settings.DEFAULTS))
        self.on_record = Mock()
        self.calls = Mock()  # one log, so the tests can check the order of actions, records and closes
        self.calls.attach_mock(self.app.tour_action, "tour_action")
        self.calls.attach_mock(self.app.tour_closed, "tour_closed")
        self.calls.attach_mock(self.on_record, "on_record")
        self.w = tour.TourWindow(self.root, self.app, on_record=self.on_record)

    def tearDown(self):
        if self.w.alive:
            self.w.close()
        self.root.destroy()
        self.w = self.root = None
        gc.collect()  # Tk cycles must release their interpreter on its owning thread.

    def view(self, index, **kw):
        return tour_view.view(index, state(self.app.cfg, **kw))

    def show(self, index, **kw):
        self.w.show(self.view(index, **kw))
        self.root.update()
        return self.w.box

    def key(self, widget, keycode, state=0, keysym=None):
        options = {"keysym": keysym} if keysym else {}  # first, so the virtual key below is the one Tk keeps
        widget.event_generate("<KeyPress>", **options, keycode=keycode, state=state, when="now")


class IndexUnits(TourBase):
    def test_tk_index_units_match_the_branch_practice_field_uses(self):
        text = tk.Text(self.root)
        text.insert("1.0", "😀 recieved")
        got = text.get("1.0 + 2 chars", "1.0 + 3 chars")
        self.assertIn(got, (" ", "r"))
        self.assertEqual(practice_field.TK_UTF16, got == " ", f"Tk {tk.TkVersion} returned {got!r}")
        text.destroy()


class PracticeBox(TourBase):
    def test_set_replaces_the_range_and_puts_the_caret_after_it(self):
        box = self.show(DICTATE)
        box._set(0, 0, "Hello")
        box._set(0, 2, "Hi")
        self.assertEqual(box.text(), "Hillo")
        self.assertEqual(box.selection(), (2, 2))

    def test_offsets_past_an_emoji_are_utf16_units(self):
        box = self.show(DICTATE)
        box._set(0, 0, "😀 recieved")
        box._select(3, 11)
        self.assertEqual(box.selection(), (3, 11))
        self.assertEqual(box.selected_text(), "recieved")
        box._set(3, 11, "received")
        self.assertEqual(box.text(), "😀 received")
        self.assertEqual(box.selection(), (11, 11))

    def test_an_empty_selection_is_the_caret(self):
        box = self.show(LIVE)
        end = len(tour_copy.LIVE)
        self.assertEqual(box.selection(), (end, end))
        self.assertEqual(box.selected_text(), "")

    def test_the_view_selects_all_or_nothing(self):
        box = self.show(REWRITE)
        self.assertEqual(box.selected_text(), tour_copy.REWRITE)
        box = self.show(READ)
        self.assertEqual((box.text(), box.selection()), (tour_copy.READ, (0, 0)))

    def test_bounds_places_a_span_and_refuses_changed_text(self):
        box = self.show(DICTATE)
        box._set(0, 0, "😀 recieved")
        self.root.update()
        field = box.field()
        spans = box.bounds(field, [(3, 11, "recieved")])
        self.assertEqual(len(spans), 1)
        (left, top, right, bottom), = spans[0]
        self.assertLess(left, right)
        self.assertLess(top, bottom)
        l, t, r, b = box.screen_rect()
        self.assertTrue(l <= left < right <= r and t <= top < bottom <= b)
        self.assertEqual(box.bounds(field, [(3, 11, "received")]), [None])
        box._set(0, 2, "")
        self.assertIsNone(box.bounds(field, [(3, 11, "recieved")]))

    def test_the_field_is_ours(self):
        from harness_discussion import practice
        field = self.show(READ).field()
        self.assertEqual((field.app, field.app_name, field.label), (os.getpid(), "Harness Discussion tour",
                                                                    "the practice box"))
        self.assertTrue(practice.ours(field))


class Frame(TourBase):
    def test_the_title_bar_is_dark(self):
        self.w.close()
        with patch.object(ctypes.windll, "dwmapi", create=True) as dwm:
            self.w = tour.TourWindow(self.root, self.app, on_record=self.on_record)
        attributes = [c.args for c in dwm.DwmSetWindowAttribute.call_args_list]
        self.assertTrue(any(a[1] == 20 and a[3] == 4 for a in attributes), attributes)

    def test_the_user_close_calls_back_and_close_does_not(self):
        self.show(READ)
        self.root.tk.call(self.w.win.protocol("WM_DELETE_WINDOW"))
        self.app.tour_closed.assert_called_once_with()
        self.assertFalse(self.w.alive)
        self.w = tour.TourWindow(self.root, self.app, on_record=self.on_record)
        self.show(READ)
        self.w.close()
        self.app.tour_closed.assert_called_once_with()
        self.assertFalse(self.w.alive)
        self.assertIsNone(self.w.box)

    def test_frontmost_is_our_process_owning_the_foreground(self):
        for identity, expected in (((os.getpid(), 5), True), ((os.getpid() + 1, 5), False), (None, False)):
            with self.subTest(identity=identity), patch.object(tour.edit, "_foreground", return_value=identity):
                self.assertIs(self.w.frontmost(), expected)

    def test_clicks_on_next_and_skip_are_the_window_not_the_box(self):
        box = self.show(REWRITE)
        self.root.update()
        for text in ("Next", "Skip", "Skip tour"):
            label = next(lbl for lbl in labels(self.w.win) if lbl["text"] == text)
            x, y = label.winfo_rootx() + label.winfo_width() // 2, label.winfo_rooty() + label.winfo_height() // 2
            self.assertEqual(self.w.hit(x, y), "window", text)
        left, top, right, bottom = box.screen_rect()
        self.assertEqual(self.w.hit((left + right) // 2, (top + bottom) // 2), "box")
        self.assertIsNone(self.w.hit(left - 5000, top - 5000))

    def test_next_skip_and_skip_tour_call_the_app(self):
        self.show(REWRITE)
        for text, method in (("Next", "tour_next"), ("Skip", "tour_skip"), ("Skip tour", "tour_skip_all")):
            click(self.root, self.w.win, text)
            getattr(self.app, method).assert_called_once_with()
        self.show(DONE)
        click(self.root, self.w.win, "Finish")
        click(self.root, self.w.win, "Open Settings")
        self.app.tour_finish.assert_called_once_with()
        self.app.tour_action.assert_called_once_with("settings")

    def test_focus_brings_an_open_tour_back(self):
        self.show(READ)
        self.w.win.iconify()
        self.root.update()
        self.w.focus()
        self.root.update()
        self.assertEqual(self.w.win.state(), "normal")

    def test_the_done_screen_lists_every_step_and_try_now(self):
        self.app.cfg["tour_steps"] = {"welcome": "tried", "voice": "skipped"}
        self.show(DONE, progress=self.app.cfg["tour_steps"])
        texts = [lbl["text"] for lbl in labels(self.w.win)]
        rows = ["Welcome", "Voice", "Read", "Rewrite", "Review", "Dictation", "Check as I type"]
        self.assertEqual([t for t in texts if t.removeprefix("✓ ").removesuffix(" – skipped") in rows],
                         ["✓ Welcome"] + [f"{r} – skipped" for r in rows[1:]])
        self.assertEqual(texts.count("Try now"), 6)
        self.assertIn(tour_copy.MORE, texts)
        self.assertEqual(set(self.w.shortcuts), set(hotkeys.SHORTCUTS))
        click(self.root, self.w.win, "Try now")
        self.app.tour_action.assert_called_once_with("try", "voice")

    def test_refresh_keeps_the_box_text_and_selection(self):
        box = self.show(REWRITE)
        box._set(0, 2, "")
        box._select(1, 3)
        self.w.refresh(self.view(REWRITE, progress={"rewrite": "tried"}))
        self.root.update()
        self.assertIs(self.w.box, box)
        self.assertEqual((box.text(), box.selection()), (tour_copy.REWRITE[2:], (1, 3)))
        self.assertIn("✓ Rewritten in place. Undo puts your words back.", [l["text"] for l in labels(self.w.win)])

    def test_start_over_asks_the_app(self):
        self.show(REWRITE)
        click(self.root, self.w.win, "Start over")
        self.app.tour_action.assert_called_once_with("start_over")


class KeepSwitch(TourBase):
    def test_keep_live_asks_first(self):
        self.app.cfg["writer_sources"] = [["Cursor", True], ["Codex", False], ["Local", True]]
        self.show(LIVE, sandboxed={"writer", "live_check"})
        with patch.object(tour_steps.messagebox, "askokcancel", return_value=False) as ask:
            self.w.keep_switch.click()
        ask.assert_called_once_with("Check as I type", writer_settings.live_confirm_text(["Cursor", "Local"],
                                                                                         "this PC"),
                                    icon="question", parent=self.w.win)
        self.assertFalse(self.w.keep_switch.on)
        self.app.tour_action.assert_not_called()
        with patch.object(tour_steps.messagebox, "askokcancel", return_value=True):
            self.w.keep_switch.click()
        self.assertTrue(self.w.keep_switch.on)
        self.app.tour_action.assert_called_once_with("keep_live")

    def test_keep_writer_saves_without_a_confirm(self):
        self.show(REWRITE, sandboxed={"writer"})
        with patch.object(tour_steps.messagebox, "askokcancel") as ask:
            self.w.keep_switch.click()
        ask.assert_not_called()
        self.app.tour_action.assert_called_once_with("keep_writer")


class TrayItem(unittest.TestCase):
    def setUp(self):
        with patch.object(host.tk, "Tk"), patch.object(host.pystray, "Icon"), patch.object(host, "icon_image"):
            self.host = host.TkHost()
        self.host.app = Mock(cfg=deepcopy(settings.DEFAULTS), events=queue.Queue())

    def test_take_the_tour_follows_settings_and_replays(self):
        items = list(self.host._menu().items)
        texts = [item.text for item in items]
        at = texts.index("Settings") + 1
        self.assertEqual(texts[at], "Take the Tour")
        items[at](None)
        self.assertEqual(self.host.app.events.get_nowait(), ("tour_replay",))

    def test_make_tour_pauses_the_shortcuts_while_change_records(self):
        with patch("harness_discussion.win.tour.TourWindow") as window:
            made = self.host.make_tour(self.host.app)
        window.assert_called_once_with(self.host.root, self.host.app, on_record=self.host._record_shortcuts)
        self.assertIs(made, window.return_value)


class ShortcutRows(TourBase):
    CTRL, SHIFT, ALT = 0x4, 0x1, 0x20000

    def test_a_rewrite_view_shows_the_saved_shortcut(self):
        self.show(REWRITE)
        self.assertEqual(set(self.w.shortcuts), {"rewrite_hotkey"})
        self.assertEqual(self.w.shortcuts["rewrite_hotkey"]["label"]["text"],
                         hotkeys.label(self.app.cfg["rewrite_hotkey"]))

    def test_a_recorded_chord_is_saved_through_the_tour(self):
        self.show(REWRITE)
        self.w._record_hotkey("rewrite_hotkey")
        self.assertEqual(self.w.shortcuts["rewrite_hotkey"]["label"]["text"], "Press keys…")
        self.w._hotkey_key(SimpleNamespace(keycode=0x59, state=self.CTRL | self.SHIFT, keysym="Y"))
        self.assertEqual(self.calls.mock_calls, [call.on_record(True),
                                                 call.tour_action("shortcut", ("rewrite_hotkey", "<ctrl>+<shift>+y")),
                                                 call.on_record(False)])

    def test_the_read_shortcut_chord_is_refused(self):
        self.app.cfg["hotkey"] = "<ctrl>+<alt>+r"
        self.show(REWRITE)
        self.w._record_hotkey("rewrite_hotkey")
        self.w._hotkey_key(SimpleNamespace(keycode=0x52, state=self.CTRL | self.ALT, keysym="r"))
        self.assertEqual(self.w.shortcuts["rewrite_hotkey"]["error"]["text"],
                         "Read shortcut already uses Ctrl+Alt+R.")
        self.app.tour_action.assert_not_called()
        self.w.refresh(self.view(REWRITE))
        self.assertEqual(self.w.shortcuts["rewrite_hotkey"]["error"]["text"],
                         "Read shortcut already uses Ctrl+Alt+R.")

    def test_refresh_shows_a_shortcut_changed_elsewhere(self):
        self.show(REWRITE)
        self.app.cfg["rewrite_hotkey"] = "<ctrl>+<shift>+e"
        self.w.refresh(self.view(REWRITE))
        self.assertEqual(self.w.shortcuts["rewrite_hotkey"]["label"]["text"], "Ctrl+Shift+E")

    def test_show_ends_a_recording_and_rebuilds_the_rows(self):
        self.show(REWRITE)
        self.w._record_hotkey("rewrite_hotkey")
        self.show(DICTATE)
        self.assertEqual(self.on_record.call_args_list, [call(True), call(False)])
        self.assertIsNone(self.w._recording)
        self.assertEqual(set(self.w.shortcuts), {"dictate_hotkey"})

    def test_a_user_close_while_recording_restores_the_shortcuts_first(self):
        self.show(REWRITE)
        self.w._record_hotkey("rewrite_hotkey")
        self.w._user_close()
        self.assertEqual(self.calls.mock_calls, [call.on_record(True), call.on_record(False), call.tour_closed()])

    def test_change_then_escape_keeps_the_shortcut(self):
        saved = self.app.cfg["rewrite_hotkey"]
        self.show(REWRITE)
        self.w._record_hotkey("rewrite_hotkey")
        self.key(self.w.win, 0x1B, keysym="Escape")
        self.assertIsNone(self.w._recording)
        self.assertEqual(self.w.shortcuts["rewrite_hotkey"]["label"]["text"], hotkeys.label(saved))
        self.app.tour_action.assert_not_called()
        self.w._user_close()
        self.assertEqual(self.calls.mock_calls, [call.on_record(True), call.on_record(False), call.tour_closed()])

    def test_keys_recorded_from_the_box_never_reach_it(self):
        self.w.show(dict(self.view(DICTATE), shortcuts=[("rewrite_hotkey", None)]))
        self.root.update()
        box = self.w.box
        self.w._record_hotkey("rewrite_hotkey")
        box.widget.focus_force()
        self.root.update()
        self.key(box.widget, 0x59, keysym="y")
        self.root.update()
        self.assertEqual(box.text(), "")
        self.app.tour_action.assert_called_once_with("shortcut", ("rewrite_hotkey", "y"))

    def test_return_records_while_recording_and_types_in_the_box_otherwise(self):
        box = self.show(DICTATE)
        self.w._record_hotkey("dictate_hotkey")
        with patch.object(self.w, "_hotkey_key", return_value="break") as recorder:
            self.root.update()
            self.key(self.w.win, 0x0D, keysym="Return")
        recorder.assert_called_once()
        self.app.tour_next.assert_not_called()
        self.w._stop_recording()
        box.widget.focus_force()
        self.root.update()
        self.key(box.widget, 0x0D, keysym="Return")
        self.root.update()
        self.assertIn("\n", box.text())
        self.app.tour_next.assert_not_called()

    def test_return_outside_the_box_presses_next(self):
        self.show(READ)
        self.w.win.focus_force()
        self.root.update()
        self.key(self.w.win, 0x0D, keysym="Return")
        self.app.tour_next.assert_called_once_with()
