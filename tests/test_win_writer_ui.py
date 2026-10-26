"""Writer toast and Review panel on real Tk, without clipboard, keys, or models."""
import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

import time
import tkinter as tk
from unittest.mock import patch

from harness_discussion.win import review as win_review, toast as win_toast

def labels(widget):
    """Every label under widget, in creation order."""
    out = []
    for child in widget.winfo_children():
        if isinstance(child, tk.Label):
            out.append(child)
        out += labels(child)
    return out

def click(root, widget, text):
    """Presses and releases the mouse on the visible label with this text, as a user would."""
    root.update()
    label = next(lbl for lbl in labels(widget) if lbl["text"] == text and lbl.winfo_viewable())
    label.winfo_toplevel().lift()
    root.update_idletasks()
    x, y = label.winfo_rootx() + 2, label.winfo_rooty() + 2
    label.event_generate("<ButtonPress-1>", rootx=x, rooty=y, x=2, y=2)
    label.event_generate("<ButtonRelease-1>", rootx=x, rooty=y, x=2, y=2)
    root.update()

def pump(root, seconds, until=lambda: False):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        if until():
            break
        time.sleep(0.01)

class Base(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

class ToastTest(Base):
    def test_completed_rewrite_can_be_dismissed_while_hovered_with_its_menu_open(self):
        from test_app_writer import _App
        app = _App()
        self.addCleanup(app._close_writer)
        toast = app.toast = win_toast.Toast(self.root)
        self.addCleanup(toast.destroy)
        hidden = []
        toast.on_hide = lambda: (hidden.append(True), app._watch_escape())
        app.rewrite_selection()
        app.drain()
        self.root.update()
        self.assertEqual([lbl["text"] for lbl in labels(toast.win)].count("×"), 1)
        self.assertTrue(app.esc.on)
        last_edit, pasted = app._last_edit, list(app.edit.pasted)
        toast.win.event_generate("<Enter>")
        click(self.root, toast.win, "Mode ▾")
        self.assertTrue(toast._menu_open)
        click(self.root, toast.win, "×")
        self.assertFalse(toast.visible)
        self.assertFalse(toast._menu_open)
        self.assertFalse(app.esc.on)
        self.assertEqual(hidden, [True])
        self.assertEqual(app._last_edit, last_edit)
        self.assertEqual(app.edit.pasted, pasted)

    def test_reentering_the_toast_cancels_the_pending_submenu_close(self):
        toast = win_toast.Toast(self.root)
        self.addCleanup(toast.destroy)
        toast.show("Choose mode", 200, 200, [("Mode", [("Email", lambda: None, True)])])
        click(self.root, toast.win, "Mode ▾")
        with patch.object(win_toast, "HIDE_MS", 50):
            menu = next(lbl for lbl in labels(self.root) if lbl["text"] == "✓ Email").winfo_toplevel()
            toast.win.event_generate("<Leave>")
            toast.win.event_generate("<Enter>")
            pump(self.root, 0.4)
            self.assertTrue(toast.visible)
            self.assertTrue(menu.winfo_exists())
            self.assertTrue(menu.winfo_viewable())
            toast.win.event_generate("<Leave>")
            pump(self.root, 0.4)
            self.assertFalse(toast.visible)

    def test_leaving_an_open_mode_menu_resumes_toast_auto_hide(self):
        toast = win_toast.Toast(self.root)
        self.addCleanup(toast.destroy)
        toast.show("Choose mode", 200, 200, [("Mode", [("Email", lambda: None, True)])])
        click(self.root, toast.win, "Mode ▾")
        with patch.object(win_toast, "HIDE_MS", 50):
            toast.win.event_generate("<Leave>")
            pump(self.root, 0.4)
            self.assertFalse(toast.visible)
            self.assertFalse(any(w.winfo_viewable() for w in self.root.winfo_children()))

    def test_entering_the_submenu_cancels_its_pending_close(self):
        toast = win_toast.Toast(self.root)
        self.addCleanup(toast.destroy)
        toast.show("Choose mode", 200, 200, [("Mode", [("Email", lambda: None, True)])])
        click(self.root, toast.win, "Mode ▾")
        with patch.object(win_toast, "HIDE_MS", 50):
            menu = next(lbl for lbl in labels(self.root) if lbl["text"] == "✓ Email").winfo_toplevel()
            toast.win.event_generate("<Leave>")
            menu.event_generate("<Enter>")
            pump(self.root, 0.4)
            self.assertTrue(toast.visible)
            self.assertTrue(menu.winfo_viewable())
            menu.event_generate("<Leave>")
            pump(self.root, 0.4)
            self.assertFalse(toast.visible)

    def test_reopening_a_submenu_does_not_leave_an_orphan_window(self):
        toast = win_toast.Toast(self.root)
        self.addCleanup(toast.destroy)
        toast.show("Choose mode", 200, 200, [("Mode", [("Email", lambda: None, False)])])
        self.root.update()
        for _ in range(2):
            click(self.root, toast.win, "Mode ▾")
            menus = [w for w in self.root.winfo_children()
                     if w is not toast.win and w.winfo_viewable()]
            self.assertEqual(len(menus), 1)
        toast.hide()
        self.root.update()
        self.assertFalse(any(w.winfo_viewable() for w in self.root.winfo_children()))

    def make(self):
        self.actions = []
        toast = win_toast.Toast(self.root, self.actions.append)
        self.addCleanup(toast.destroy)
        return toast

    def test_shows_message_and_reports_each_action(self):
        toast = self.make()
        self.assertFalse(toast.visible)
        toast.show("✓ Rewritten · Message · Cursor", x=200, y=200)
        self.root.update()
        self.assertTrue(toast.visible)
        texts = [lbl["text"] for lbl in labels(toast.win)]
        self.assertIn("✓ Rewritten · Message · Cursor", texts)
        for name in ("Undo", "Review", "Mode"):
            self.assertIn(name, texts)
        for name in ("Undo", "Review", "Mode"):
            click(self.root, toast.win, name)
        self.assertEqual(self.actions, ["undo", "review", "mode"])

    def test_shared_actions_and_submenu_hide_before_calling_back(self):
        calls, hidden = [], []
        toast = win_toast.Toast(self.root)
        toast.on_hide = lambda: hidden.append(not toast.visible)
        self.addCleanup(toast.destroy)

        toast.show("Ready", 200, 200,
                   [("Copy", lambda: calls.append(("copy", toast.visible))),
                    ("Mode", [("Careful", lambda: calls.append(("mode", toast.visible)), True)])], False)
        self.root.update()
        self.assertIn("Copy", [lbl["text"] for lbl in labels(toast.win)])
        click(self.root, toast.win, "Mode ▾")
        self.root.update()
        self.assertIn("✓ Careful", [lbl["text"] for lbl in labels(self.root) if lbl.winfo_viewable()])
        click(self.root, self.root, "✓ Careful")
        self.assertEqual(calls, [("mode", False)])
        self.assertEqual(hidden, [True])

        toast.show("Ready again", 200, 200, [("Copy", lambda: calls.append(("copy", toast.visible)))], False)
        click(self.root, toast.win, "Copy")
        self.assertEqual(calls, [("mode", False), ("copy", False)])
        self.assertEqual(hidden, [True, True])

    def test_error_toast_offers_copy_only(self):
        toast = self.make()
        toast.show("Text changed. Copy result?", actions=("copy",), x=200, y=200)
        self.root.update()
        shown = [lbl["text"] for lbl in labels(toast.win) if lbl.winfo_viewable()]
        self.assertEqual(shown[1:], ["Copy"])
        click(self.root, toast.win, "Copy")
        self.assertEqual(self.actions, ["copy"])

    def test_auto_hides_unless_hovered_and_each_show_restarts_the_timer(self):
        toast = self.make()
        with patch.object(win_toast, "HIDE_MS", 150):
            toast.show("one", x=200, y=200)
            pump(self.root, 0.1)
            toast.show("two", x=200, y=200)  # restarts the countdown
            pump(self.root, 0.1)
            self.assertTrue(toast.visible)
            pump(self.root, 0.15)
            self.assertFalse(toast.visible)

            toast.show("three", x=200, y=200)
            toast.win.event_generate("<Enter>")
            pump(self.root, 0.3)
            self.assertTrue(toast.visible, "hovering pauses the auto-hide")
            toast.win.event_generate("<Leave>")
            pump(self.root, 0.3)
            self.assertFalse(toast.visible)

    def test_hide_and_destroy_cancel_the_timer(self):
        toast = self.make()
        with patch.object(win_toast, "HIDE_MS", 50):
            toast.show("one", x=200, y=200)
            toast.hide()
            toast.destroy()
            pump(self.root, 0.15)  # a pending hide on a destroyed window would raise TclError here
        self.assertFalse(toast.visible)

    def test_updating_a_hovered_toast_keeps_auto_hide_paused(self):
        toast = self.make()
        with patch.object(win_toast, "HIDE_MS", 50):
            toast.show("one", x=200, y=200)
            self.root.update()
            toast.win.event_generate("<Enter>")
            toast.show("two", x=200, y=200)
            pump(self.root, 0.15)
            self.assertTrue(toast.visible)

    def test_busy_activity_and_hide_notifications(self):
        toast = win_toast.Toast(self.root)
        hidden = []
        toast.on_hide = lambda: hidden.append(toast.visible)
        self.addCleanup(toast.destroy)
        with patch.object(win_toast, "HIDE_MS", 50), patch.object(win_toast, "ACTIVITY_MS", 20):
            toast.show("Working", 200, 200, (), True)
            self.root.update()
            self.assertTrue(toast.spinner.winfo_viewable())
            first_frame = toast.spinner["text"]
            toast.win.event_generate("<Leave>")
            pump(self.root, 0.5, until=lambda: toast.spinner["text"] != first_frame)
            self.assertNotEqual(toast.spinner["text"], first_frame)
            pump(self.root, 0.12)
            self.assertTrue(toast.visible, "a busy toast stays visible after Leave")

            toast.show("Finished", 200, 200, (), False)
            pump(self.root, 0.12)
            self.assertFalse(toast.visible)
            self.assertEqual(hidden, [False])
            toast.hide()
            self.assertEqual(hidden, [False], "hiding an already hidden toast is not another transition")

            toast.show("Working again", 200, 200, (), True)
            toast.hide()
            self.assertEqual(hidden, [False, False])
            toast.show("Working once more", 200, 200, (), True)
            toast.destroy()
            self.assertEqual(hidden, [False, False, False])
            pump(self.root, 0.08)  # cancelled activity callbacks do not reach a destroyed window

OLD = "hey team, the deploy broke cause the env var wasnt set"
NEW = "Hi team, the deploy failed because the env var wasn't set"

class PlaceTest(unittest.TestCase):
    def test_under_the_bar_or_above_it_when_there_is_no_room(self):
        geometry = []
        w = type("W", (), {"update_idletasks": lambda s: None, "winfo_reqwidth": lambda s: 200,
                           "winfo_reqheight": lambda s: 32, "geometry": lambda s, g: geometry.append(g)})()
        area = type("Area", (), {"left": 0, "top": 0, "right": 1000, "bottom": 300})()
        lower = type("Area", (), {"left": 0, "top": 300, "right": 1000, "bottom": 600})()
        with patch.object(win_toast, "work_area", lambda x, y: area if y < 300 else lower):
            win_toast.place(w, 300, 248, above=200)  # fits under the bar
            win_toast.place(w, 300, 290, above=242)  # no room: above the bar
            win_toast.place(w, 300, 306, above=258)  # past the monitor's edge: still above the bar
            win_toast.place(w, 300, 100)  # no bar: by the pointer, as before
        self.assertEqual(geometry, ["+200+248", "+200+204", "+200+220", "+200+116"])

class ReviewTest(Base):
    def make(self):
        self.calls = []
        review = win_review.Review(self.root, on_replace=lambda t: self.calls.append(("replace", t)),
                                   on_copy=lambda t: self.calls.append(("copy", t)),
                                   on_rewrite=lambda t: self.calls.append(("rewrite", t)))
        self.addCleanup(review.destroy)
        return review

    def test_shows_both_sides_and_buttons_pass_the_result(self):
        review = self.make()
        self.assertFalse(review.visible)
        review.show(OLD, NEW)
        self.root.update()
        self.assertTrue(review.visible)
        self.assertEqual(review.left.get("1.0", "end-1c"), OLD)
        self.assertEqual(review.right.get("1.0", "end-1c"), NEW)
        for name in ("Rewrite again", "Copy", "Replace"):
            click(self.root, review.win, name)
        self.assertEqual(self.calls, [("rewrite", NEW), ("copy", NEW), ("replace", NEW)])

    def test_shared_show_callbacks_receive_current_result_and_keep_constructor_rewrite(self):
        calls = []
        review = win_review.Review(self.root, on_copy=lambda text: calls.append(("old copy", text)),
                                   on_rewrite=lambda text: calls.append(("rewrite", text)))
        self.addCleanup(review.destroy)
        review.show(OLD, NEW, lambda text: calls.append(("replace", text)),
                    lambda text: calls.append(("copy", text)))
        self.root.update()
        start = review.right.search("failed", "1.0")
        x, y, _w, _h = review.right.bbox(f"{start}+1c")
        review.right.event_generate("<Motion>", x=x, y=y)
        review.right.event_generate("<Button-1>", x=x, y=y)
        self.root.update()
        result = "Hi team, the deploy broke because the env var wasn't set"
        for name in ("Rewrite again", "Copy", "Replace"):
            click(self.root, review.win, name)
        self.assertEqual(calls, [("rewrite", result), ("copy", result), ("replace", result)])

    def test_clicking_a_change_restores_just_that_change(self):
        review = self.make()
        review.show(OLD, NEW)
        self.root.update()
        start = review.right.search("failed", "1.0")
        x, y, _w, _h = review.right.bbox(f"{start}+1c")
        review.right.event_generate("<Motion>", x=x, y=y)  # tag bindings follow the pointer's "current" mark
        review.right.event_generate("<Button-1>", x=x, y=y)
        self.root.update()
        restored = "Hi team, the deploy broke because the env var wasn't set"
        self.assertEqual(review.headings[1]["text"], "Rewrite · 3 changes")
        self.assertEqual(review.right.get("1.0", "end-1c"), restored)
        self.assertEqual(review.left.get("1.0", "end-1c"), OLD)
        click(self.root, review.win, "Copy")
        self.assertEqual(self.calls, [("copy", restored)])
        review.show(OLD, NEW)  # a new review starts with every change applied
        self.assertEqual(review.right.get("1.0", "end-1c"), NEW)

    def test_replace_is_disabled_when_the_context_is_gone(self):
        review = self.make()
        review.show(OLD, NEW, can_replace=False)
        click(self.root, review.win, "Replace")
        click(self.root, review.win, "Rewrite again")
        self.assertEqual(self.calls, [])
        review.set_can_replace(True)
        click(self.root, review.win, "Replace")
        self.assertEqual(self.calls, [("replace", NEW)])
        review.set_can_replace(False)
        click(self.root, review.win, "Replace")
        click(self.root, review.win, "Copy")
        self.assertEqual(self.calls, [("replace", NEW), ("copy", NEW)])
        review.hide()
        self.assertFalse(review.visible)

    def test_missing_replace_callback_means_copy_only_and_missing_callbacks_are_safe(self):
        calls = []
        review = win_review.Review(self.root, on_replace=lambda text: calls.append(("old replace", text)),
                                   on_copy=lambda text: calls.append(("old copy", text)),
                                   on_rewrite=lambda text: calls.append(("rewrite", text)))
        self.addCleanup(review.destroy)
        review.show(OLD, NEW, None, lambda text: calls.append(("copy", text)))
        for name in ("Replace", "Rewrite again", "Copy"):
            click(self.root, review.win, name)
        self.assertEqual(calls, [("copy", NEW)])

        review = win_review.Review(self.root)
        self.addCleanup(review.destroy)
        review.show(OLD, NEW, None, None)
        for name in ("Replace", "Rewrite again", "Copy"):
            click(self.root, review.win, name)
        self.assertEqual(calls, [("copy", NEW)])

if __name__ == "__main__":
    unittest.main()
