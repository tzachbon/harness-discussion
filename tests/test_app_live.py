"""Check as you type, the shared flow with a fake field, marks, toast, and source: when it checks, what it sends,
what it draws, and Apply, Dismiss, Review, Turn off, and Undo."""
import hashlib
import logging
import queue
import threading
import unittest
from types import SimpleNamespace

from harness_discussion import app_live, app_summarize, app_writer, live
from harness_discussion.edit_logic import Context, Field

from tests.test_app_writer import FakeToast, Source


def digest(text):
    return hashlib.sha256(text.encode("utf-16-le", "surrogatepass")).digest()


class FakeField:
    """One editable document with its own caret, read and changed like a platform edit module would."""
    NEWLINE = "\n"

    def __init__(self, text="", app_name="notepad.exe"):
        self.text, self.app_name, self.secure, self.placeable = text, app_name, False, True
        self.handle, self.at = "element", None  # at: the caret, or None for the end
        self.selected = None  # (start, end) after select_range
        self.observed, self.pasted = 0, []

    rect = (0, 0, 400, 200)
    collapsed = 0

    def field(self):
        caret = live.units(self.text) if self.at is None else self.at
        return Field(1, 2, self.app_name, self.app_name.split(".")[0].capitalize(), self.text, (caret, caret),
                     digest(self.text), self.rect, self.handle)

    def collapse(self):
        self.selected = None
        self.collapsed += 1

    def observe(self):
        self.observed += 1
        return None if self.secure else self.field()

    def bounds(self, field, spans):
        if field.digest != digest(self.text):
            return None
        return [[(s * 8, 0, e * 8, 18)] if self.placeable and live.text_at(self.text, s, e) == x else None
                for s, e, x in spans]

    def select_range(self, field, start, end, expected):
        if (field.handle != self.handle or field.digest != digest(self.text)
                or live.text_at(self.text, start, end) != expected):
            return None
        self.selected = (start, end)
        return Context(expected, 1, 2, True, False, "element", (start, end - start))

    def replace(self, text, ctx):
        start, end = self.selected
        self.text = live.text_at(self.text, 0, start) + text + live.text_at(self.text, end, live.units(self.text))
        self.pasted.append(text)
        self.selected = None
        self.caret = start + live.units(text)
        return True

    def undo(self, ctx, original, inserted):
        at = self.text.rfind(inserted)
        if at < 0:
            return False
        self.text = self.text[:at] + original + self.text[at + len(inserted):]
        return True

    # Writer's capture-based API, unused by these tests
    def capture(self, clipboard_fallback):
        return None


class Toast(FakeToast):
    menu_open = False

    def contains(self, x, y):
        return False


def window():
    """A Review panel stand-in that records what it shows."""
    review = SimpleNamespace(shown=[], visible=False, contains=lambda x, y: False)
    review.show = lambda *a: review.shown.append(a)
    return review


class FakeMarks:
    def __init__(self):
        self.shown, self.hidden, self.word = None, 0, {}

    def show(self, strips, badge):
        self.shown = (strips, badge)
        self.word = {key: rect for key, rect in strips}

    def hide(self):
        self.shown, self.word = None, {}
        self.hidden += 1

    def hit(self, x, y):
        if self.shown and self.shown[1] and (x, y) == (self.shown[1][0], self.shown[1][1]):
            return "badge"
        for key, (left, top, right, bottom) in self.word.items():
            if left <= x < right and top <= y < bottom:
                return key
        return None


class _App(app_live.Live, app_summarize.Summaries, app_writer.Writer):
    def __init__(self, reply="I think so, ok"):
        self.events, self.copied, self.changes = queue.Queue(), [], []
        self.toast_, self.marks_, self.review_ = Toast(), FakeMarks(), window()
        self.field_ = FakeField()
        self.host = SimpleNamespace(
            edit=self.field_, make_toast=lambda: self.toast_, make_review=lambda: self.review_,
            make_marks=lambda: self.marks_, esc_watcher=lambda fn: SimpleNamespace(start=lambda: None, stop=lambda: None),
            pointer=lambda: (10, 10), after=lambda ms, fn: None, copy_text=self.copied.append,
            set_shortcut=lambda *a: None)
        self.cfg = {"writer": True, "live_check": True, "live_off_apps": [],
                    "writer_sources": [["Cursor", True]], "writer_models": {}, "modes": [],
                    "default_mode": "Fix errors", "clipboard_fallback": True, "stt_sources": [],
                    "dictate_polish": False, "dictate_mode": "Clean dictation"}
        self.popup = SimpleNamespace(visible=False, set_rewriting=lambda on: None, set_last_mode=lambda m: None,
                                     hide=lambda: None, contains=lambda x, y: False)
        self.settings_win = None
        self.summary_server = SimpleNamespace(stop=lambda: None)
        self._summary_gen = 0
        self._init_writer()
        self.source = Source("Cursor", reply)
        self.writer_sources = [self.source]
        self._live_stop.set()  # the tests drive polls themselves

    def change(self, key, value, save=True):
        self.cfg[key] = value
        self.changes.append((key, value))
        self._apply_live(key, value)

    def _arm_summary_stop(self):
        pass

    def pump(self):
        while not self.events.empty():
            kind, *args = self.events.get_nowait()
            getattr(self, self.LIVE_EVENTS[kind])(*args)

    def type(self, text, at=-1):
        """The field had one character less, then the user typed the last one at `at`."""
        self.field_.text = text[:-1]
        self._live_tick(now=at - 0.5)
        self.field_.text = text
        self.tick(at)

    def tick(self, at):
        self._live_tick(now=at)
        for _ in range(100):
            if self._live_checking is None:
                break
            threading_wait()
        self.pump()


def threading_wait():
    import time
    time.sleep(0.01)


class WhenItChecks(unittest.TestCase):
    def setUp(self):
        self.app = _App()
        self.field = self.app.field_

    def test_off_by_default_reads_nothing(self):
        self.app.cfg["live_check"] = False
        self.field.text = "i thinks so, ok"
        for at in range(10):
            self.app.tick(at)
        self.assertEqual(self.field.observed, 0)
        self.assertTrue(self.app.events.empty())

    def test_one_check_after_a_pause_and_none_for_the_same_line_again(self):
        self.app.type("hello\ni thinks so, ok", at=0)
        self.app.tick(1)
        self.assertEqual(self.app.source.texts, [])  # still typing
        self.app.tick(2)
        self.assertEqual(len(self.app.source.texts), 1)
        self.assertIn("i thinks so, ok", self.app.source.texts[0])
        self.assertNotIn("hello", self.app.source.texts[0])
        for at in range(3, 10):
            self.app.tick(at)
        self.assertEqual(len(self.app.source.texts), 1)

    def test_text_that_keeps_changing_is_not_sent(self):
        for at in range(10):
            self.field.text = "i thinks so" + " ok" * at
            self.app.tick(at * 0.5)
        self.assertEqual(self.app.source.texts, [])

    def test_never_for_secure_off_listed_or_disabled_writer(self):
        self.field.text = "i thinks so, ok"
        self.field.secure = True
        for at in range(5):
            self.app.tick(at)
        self.field.secure, self.app.cfg["live_off_apps"] = False, ["notepad.exe"]
        for at in range(5, 10):
            self.app.tick(at)
        self.app.cfg["live_off_apps"], self.app.cfg["writer"] = [], False
        for at in range(10, 15):
            self.app.tick(at)
        self.assertEqual(self.app.source.texts, [])

    def test_no_check_while_a_rewrite_runs_and_a_manual_rewrite_cancels_one(self):
        self.field.text = "i thinks so, ok"
        self.app.rewriting = True
        for at in range(5):
            self.app.tick(at)
        self.assertEqual(self.app.source.texts, [])
        self.app.rewriting = False
        for start in (self.app.rewrite_selection, self.app._dictate_down):
            cancel = self.app._live_cancel = threading.Event()
            self.app.cfg["writer"] = False  # the Writer's own start then returns at once
            start()
            self.assertTrue(cancel.is_set(), start.__name__)

    def test_logs_one_length_only_line_and_never_the_text(self):
        with self.assertLogs("harness_discussion", logging.INFO) as logs:
            self.app.type("i thinks so, ok", at=0)
            self.app.tick(2)
        self.assertEqual([r.getMessage() for r in logs.records], ["live check 15 chars", "live check done with Cursor"])

    def test_a_line_only_opened_or_moved_to_is_never_sent(self):
        self.field.text = "my private draft line\nanother private line here"
        for at in range(10):
            self.app.tick(at)
        self.field.handle = "another field"  # focus moves to a field typed in before
        for at in range(10, 15):
            self.app.tick(at)
        self.assertEqual(self.app.source.texts, [])

    def test_typing_in_one_line_does_not_send_another(self):
        self.app.type("first line was not typed\nsecond line is typed now", at=0)
        self.field.at = live.units("first line")  # an arrow key moves the caret up, typing nothing
        for at in range(1, 6):
            self.app.tick(at)
        self.assertEqual(self.app.source.texts, [])

    def test_the_caret_moving_off_the_typed_line_before_a_poll_sends_nothing(self):
        self.field.text = "first line was not typed\nsecond line is typed no"
        self.app.tick(0)
        self.field.text += "w"
        self.field.at = live.units("first line")  # typed on the second line, then moved up before the next poll
        for at in range(1, 6):
            self.app.tick(at)
        self.assertEqual(self.app.source.texts, [])

    def test_a_rewrite_starting_during_the_poll_stops_the_check(self):
        observe = self.field.observe

        def busy_by_then():
            self.app.rewriting = True  # the manual rewrite began while the field was being read
            return observe()
        self.app.type("i thinks so, ok", at=0)
        self.field.observe = busy_by_then
        self.app.tick(2)
        self.assertEqual(self.app.source.texts, [])

    def test_a_check_is_cancelled_when_its_line_is_gone(self):
        self.app.type("i thinks so, ok", at=0)
        self.app._live_checking = "i thinks so, ok"  # still running
        cancel = self.app._live_cancel
        self.field.text = "something else now"
        self.app._live_tick(now=3)
        self.assertTrue(cancel.is_set())

    def test_no_check_while_dictation_is_held(self):
        self.field.text = "i thinks so, ok"
        self.app._holding = True
        for at in range(5):
            self.app.tick(at)
        self.app._holding, self.app._recorder = False, object()
        for at in range(5, 10):
            self.app.tick(at)
        self.assertEqual(self.app.source.texts, [])


class WhatItDraws(unittest.TestCase):
    def setUp(self):
        self.app = _App()
        self.field = self.app.field_
        self.app.type("i thinks so, ok", at=0)
        self.app.tick(2)
        self.app.tick(3)

    def test_an_underline_per_change(self):
        strips, badge = self.app.marks_.shown
        self.assertEqual([key[3:] for key, _ in strips], [("i thinks", "I think")])
        self.assertIsNone(badge)

    def test_an_idle_field_posts_nothing_new(self):
        for at in range(4, 10):
            self.app._live_tick(now=at)
        self.assertTrue(self.app.events.empty())

    def test_unplaceable_suggestions_show_the_badge_at_the_field(self):
        self.field.placeable = False
        self.app.tick(4)
        strips, badge = self.app.marks_.shown
        self.assertEqual((strips, badge), ([], (400, 0, 1)))

    def test_without_the_field_rectangle_the_badge_goes_by_the_pointer(self):
        self.field.placeable, self.field.rect = False, None
        self.app.tick(4)
        self.assertEqual(self.app.marks_.shown[1], (10, 10, 1))

    def test_turning_it_off_hides_the_marks(self):
        self.app.change("live_check", False)
        self.assertIsNone(self.app.marks_.shown)
        self.app.tick(5)
        self.assertIsNone(self.app.marks_.shown)

    def test_editing_the_line_drops_its_underlines(self):
        self.field.text = "i thinks so, ok then"
        self.app.tick(4)
        self.assertIsNone(self.app.marks_.shown)


class Actions(unittest.TestCase):
    def setUp(self):
        self.app = _App(reply="I think we should have gone.")
        self.field = self.app.field_
        self.app.type("i thinks we should of gone.", at=0)
        self.app.tick(2)
        self.app.tick(3)

    def click(self, old):
        for key, (left, top, right, bottom) in self.app.marks_.word.items():
            if key[3] == old:
                self.assertTrue(self.app._live_press(left + 1, top + 1))
                return self.app.toast_.shown[-1]
        self.fail(f"no underline for {old!r}")

    def test_a_click_elsewhere_is_not_ours(self):
        self.assertFalse(self.app._live_press(1000, 1000))

    def test_a_click_on_our_card_or_review_over_an_underline_is_theirs(self):
        (left, top, _, _), = [r for k, r in self.app.marks_.word.items() if k[3] == "of"]
        for window in (self.app.toast_, self.app.review_, self.app.popup):
            window.contains = lambda x, y: True
            self.assertFalse(self.app._live_press(left + 1, top + 1))
            window.contains = lambda x, y: False
        self.app.toast_.menu_open = True
        self.assertFalse(self.app._live_press(left + 1, top + 1))

    def test_apply_changes_only_that_span_and_keeps_the_rest_without_a_new_check(self):
        message, actions, _ = self.click("i thinks")
        self.assertEqual(message, "i thinks → I think")
        self.assertEqual(set(actions), {"Apply", "Dismiss", "Review", "More"})
        actions["Apply"]()
        self.assertEqual(self.field.text, "I think we should of gone.")
        self.assertEqual(self.app.toast_.shown[-1][0], "Applied")
        for at in range(4, 8):
            self.app.tick(at)
        self.assertEqual(len(self.app.source.texts), 1)
        self.assertEqual([k[3] for k in self.app.marks_.word], ["of"])

    def test_apply_works_in_a_field_focused_after_an_identical_one(self):
        self.field.handle = "another field"  # same text and place, as in two copies of one draft
        self.app.tick(4)
        self.click("i thinks")[1]["Apply"]()
        self.assertEqual(self.field.text, "I think we should of gone.")

    def test_undo_restores_the_original(self):
        self.click("i thinks")[1]["Apply"]()
        self.app.toast_.action("Undo")()
        self.assertEqual(self.field.text, "i thinks we should of gone.")

    def test_apply_after_the_text_changed_offers_copy_and_pastes_nothing(self):
        actions = self.click("i thinks")[1]
        self.field.text = "i thinks we should of gone!"
        actions["Apply"]()
        self.assertEqual(self.field.pasted, [])
        self.assertEqual(self.app.toast_.shown[-1][0], "The text changed. Copy the suggestion?")
        self.app.toast_.action("Copy")()
        self.assertEqual(self.app.copied, ["I think"])

    def test_a_refused_paste_collapses_the_selection_and_offers_copy(self):
        actions = self.click("i thinks")[1]
        self.field.replace = lambda text, ctx: False
        actions["Apply"]()
        self.assertEqual((self.field.selected, self.field.collapsed), (None, 1))
        self.assertEqual(self.app.toast_.shown[-1][0], "The text changed. Copy the suggestion?")

    def test_review_replace_can_be_undone(self):
        self.click("i thinks")[1]["Review"]()
        replace = self.app.review_.shown[-1][2]
        replace("I think we should of gone.")
        self.assertEqual(self.app.toast_.shown[-1][0], "Applied")
        self.app.toast_.action("Undo")()
        self.assertEqual(self.field.text, "i thinks we should of gone.")

    def test_dismiss_survives_later_polls(self):
        self.click("i thinks")[1]["Dismiss"]()
        self.app.tick(4)
        self.assertEqual([k[3] for k in self.app.marks_.word], ["of"])

    def test_turn_off_in_this_app(self):
        menu = self.click("i thinks")[1]["More"]
        title, turn_off, _ = menu[0]
        self.assertEqual(title, "Turn off in Notepad")
        turn_off()
        self.assertEqual(self.app.cfg["live_off_apps"], ["notepad.exe"])
        self.assertIsNone(self.app.marks_.shown)
        self.app.type("she go home now ok", at=4)
        for at in range(5, 10):
            self.app.tick(at)
        self.assertEqual(len(self.app.source.texts), 1)

    def test_turn_off_updates_an_open_settings_window(self):
        refreshed = []
        self.app.settings_win = SimpleNamespace(alive=True, cfg={"live_off_apps": ["slack.exe"]},
                                                refresh_writing=lambda: refreshed.append(1))
        self.app.cfg["live_off_apps"] = ["slack.exe"]
        self.click("i thinks")[1]["More"][0][1]()
        self.assertEqual(self.app.settings_win.cfg["live_off_apps"], ["slack.exe", "notepad.exe"])
        self.assertEqual(refreshed, [1])

    def test_review_replaces_the_whole_line_through_the_guard(self):
        self.click("i thinks")[1]["Review"]()
        original, rewrite, replace, copy = self.app.review_.shown[-1]
        self.assertEqual((original, rewrite), ("i thinks we should of gone.", "I think we should have gone."))
        replace("I think we should of gone.")
        self.assertEqual(self.field.text, "I think we should of gone.")

    def test_review_opens_the_clicked_line_not_the_caret_line(self):
        self.app.live_cache.put("she go home now ok", self.app._find_mode(None), "She goes home now, ok.")
        self.field.text += "\nshe go home now ok"  # the caret is on this line
        self.app.tick(4)
        self.click("i thinks")[1]["Review"]()
        self.assertEqual(self.app.review_.shown[-1][0], "i thinks we should of gone.")

    def test_the_badge_opens_review(self):
        self.field.placeable = False
        self.app.tick(4)
        self.assertTrue(self.app._live_press(400, 0))
        self.assertEqual(self.app.review_.shown[-1][0], "i thinks we should of gone.")

    def test_the_badge_follows_the_caret_between_lines(self):
        self.app.live_cache.put("she go home now ok", self.app._find_mode(None), "She goes home now, ok.")
        self.field.text += "\nshe go home now ok"
        self.field.placeable = False
        self.app.tick(4)
        self.field.at = 0  # an arrow key moves the caret up, typing nothing
        self.app.tick(5)
        self.assertTrue(self.app._live_press(400, 0))
        self.assertEqual(self.app.review_.shown[-1][0], "i thinks we should of gone.")

    def test_quit_stops_checking_before_the_sources_close(self):
        order = []
        self.app._live_stop = SimpleNamespace(set=lambda: order.append("live"), is_set=lambda: True)
        self.app.writer_sources = [SimpleNamespace(name="Cursor", close=lambda: order.append("source"))]
        self.app._close_writer()
        self.assertEqual(order[:2], ["live", "source"])

    def test_changing_the_mode_or_sources_clears_the_cache(self):
        for key, value in (("writer_sources", [["Cursor", True]]), ("default_mode", "Fix errors")):
            self.app.live_cache.put("i thinks we should of gone.", self.app._find_mode(None), "x")
            self.app.change(key, value)
            self.assertFalse(self.app.live_cache.has("i thinks we should of gone.", self.app._find_mode(None)))


if __name__ == "__main__":
    unittest.main()
