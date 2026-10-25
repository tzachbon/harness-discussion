"""Check as you type on macOS with the AX calls patched: no app is read or driven."""
import sys
import unittest
from unittest import mock

if sys.platform != "darwin":
    raise unittest.SkipTest("macOS only")

from types import SimpleNamespace

from Foundation import NSString

from harness_discussion import live
from harness_discussion.edit_logic import Context, Field
from harness_discussion.mac import edit

FIELD, OTHER = NSString.stringWithString_("field"), NSString.stringWithString_("other")


class FakeAX:
    """A text area: `text` with lines broken at "\\n" and "\\r\\n", each line 20 points high, 8 points a unit."""

    def __init__(self, text, visible=True):
        self.text, self.visible, self.selected = text, visible, None

    def lines(self):
        out, at = [], 0
        for start, line in live.lines(self.text):
            out.append((start, line))
        return out

    def param(self, element, name, value):
        if name == "AXLineForIndex":
            return max(i for i, (start, _) in enumerate(self.lines()) if start <= value)
        if name == "AXRangeForLine":
            start, line = self.lines()[value]
            nxt = self.lines()[value + 1][0] if value + 1 < len(self.lines()) else live.units(self.text)
            return ("range", start, nxt - start)
        start, length = value[1], value[2]
        if name == "AXStringForRange":
            return live.text_at(self.text, start, start + length)
        if name == "AXBoundsForRange":
            line = max(i for i, (s, _) in enumerate(self.lines()) if s <= start)
            column = start - self.lines()[line][0]
            width = length * 8 if self.visible else 0
            return ("rect", SimpleNamespace(origin=SimpleNamespace(x=column * 8, y=line * 20),
                                            size=SimpleNamespace(width=width, height=18 if self.visible else 0)))
        return None

    def get(self, value, kind, _):
        if value[0] == "range":
            return True, (value[1], value[2])
        return True, value[1]


def patched(ax):
    return [mock.patch.object(edit, "_same_field", side_effect=lambda f: FIELD if ax.text == f.text else None),
            mock.patch.object(edit, "_param", side_effect=ax.param),
            mock.patch.object(edit, "_cf_range", side_effect=lambda s, n: ("range", s, n)),
            mock.patch.object(edit, "AXValueGetValue", side_effect=ax.get)]


def settable(value=True):
    return mock.patch.object(edit, "AXUIElementIsAttributeSettable", return_value=(edit.kAXErrorSuccess, value))


def field_for(text):
    return Field(7, 2, "com.apple.TextEdit", "TextEdit", text, (0, 0), edit._digest(text), None, FIELD)


class Place(unittest.TestCase):
    def run_with(self, ax, fn):
        patches = patched(ax)
        for p in patches:
            p.start()
        try:
            return fn()
        finally:
            for p in patches:
                p.stop()

    def test_one_rectangle_per_line_with_raw_line_breaks(self):
        text = "ok\r\ni thinks so"
        ax = FakeAX(text)
        start = live.units("ok\r\ni ")
        rects = self.run_with(ax, lambda: edit.bounds(field_for(text), [(start, start + 6, "thinks")]))
        self.assertEqual(rects, [[(16, 20, 64, 38)]])

    def test_a_span_across_two_lines_has_two_rectangles(self):
        text = "one two\nthree"
        ax = FakeAX(text)
        rects = self.run_with(ax, lambda: edit.bounds(field_for(text), [(4, 13, "two\nthree")]))
        self.assertEqual(len(rects[0]), 2)

    def test_hidden_mismatched_and_changed(self):
        text = "i thinks so"
        ax = FakeAX(text, visible=False)
        self.assertEqual(self.run_with(ax, lambda: edit.bounds(field_for(text), [(2, 8, "thinks")])), [[]])
        self.assertEqual(self.run_with(ax, lambda: edit.bounds(field_for(text), [(2, 8, "think!")])), [None])
        ax.text = "i thinks so!"
        self.assertIsNone(self.run_with(ax, lambda: edit.bounds(field_for(text), [(2, 8, "thinks")])))


class Observe(unittest.TestCase):
    def app(self, bundle, pid=7):
        app = SimpleNamespace(processIdentifier=lambda: pid, bundleIdentifier=lambda: bundle,
                              localizedName=lambda: "App")
        return mock.patch.object(edit, "_frontmost_app", return_value=app)

    def test_terminals_and_our_own_app_are_never_read(self):
        with mock.patch.object(edit.permission, "trusted", return_value=True), \
                mock.patch.object(edit, "_focused") as focused:
            for bundle in ("com.apple.Terminal", "com.googlecode.iterm2"):
                with self.app(bundle):
                    self.assertIsNone(edit.observe())
            with self.app("com.apple.TextEdit", pid=edit.os.getpid()):
                self.assertIsNone(edit.observe())
            focused.assert_not_called()

    def test_secure_and_long_fields_are_refused(self):
        attrs = {"AXRole": "AXTextArea", "AXNumberOfCharacters": 5, "AXValue": "hello"}
        with mock.patch.object(edit.permission, "trusted", return_value=True), self.app("com.apple.TextEdit"), \
                mock.patch.object(edit.selection, "_expose"), \
                mock.patch.object(edit, "_focused", return_value=FIELD), \
                mock.patch.object(edit, "_editable", return_value=True), settable(), \
                mock.patch.object(edit, "_attr", side_effect=lambda e, n: attrs.get(n)), \
                mock.patch.object(edit, "_range", return_value=(5, 0)), \
                mock.patch.object(edit, "_frontmost_pid", return_value=7), \
                mock.patch.object(edit, "_window_number", return_value=2), \
                mock.patch.object(edit, "_rect_of", return_value=(0, 0, 10, 10)):
            with mock.patch.object(edit.selection, "secure_input", return_value=False):
                field = edit.observe()
                self.assertEqual((field.text, field.caret, field.app_name), ("hello", (5, 5), "com.apple.TextEdit"))
                attrs["AXNumberOfCharacters"] = live.LIVE_MAX_DOC + 1
                self.assertIsNone(edit.observe())
            attrs["AXNumberOfCharacters"] = 5
            with mock.patch.object(edit.selection, "secure_input", return_value=True):
                self.assertIsNone(edit.observe())
            with mock.patch.object(edit.selection, "secure_input", return_value=False), \
                    settable(False):
                self.assertIsNone(edit.observe())  # a read-only text area

    def test_observe_and_bounds_never_use_the_pasteboard_or_keys(self):
        boom = mock.Mock(side_effect=AssertionError("clipboard or keys"))
        attrs = {"AXRole": "AXTextArea", "AXNumberOfCharacters": 11, "AXValue": "i thinks so"}
        ax = FakeAX("i thinks so")
        with mock.patch.object(edit.permission, "trusted", return_value=True), self.app("com.apple.TextEdit"), \
                mock.patch.object(edit.selection, "_expose"), mock.patch.object(edit, "_focused", return_value=FIELD), \
                mock.patch.object(edit, "_editable", return_value=True), settable(), \
                mock.patch.object(edit, "_attr", side_effect=lambda e, n: attrs.get(n)), \
                mock.patch.object(edit, "_range", return_value=(11, 0)), \
                mock.patch.object(edit, "_frontmost_pid", return_value=7), \
                mock.patch.object(edit, "_window_number", return_value=2), \
                mock.patch.object(edit, "_rect_of", return_value=(0, 0, 10, 10)), \
                mock.patch.object(edit.selection, "secure_input", return_value=False), \
                mock.patch.object(edit, "_key", boom), mock.patch.object(edit.pasteboard, "snapshot", boom), \
                mock.patch.object(edit.pasteboard, "copy_selection", boom):
            field = edit.observe()
            for p in patched(ax):
                p.start()
                self.addCleanup(p.stop)
            self.assertEqual(edit.bounds(field, [(2, 8, "thinks")]), [[(16, 0, 64, 18)]])
        boom.assert_not_called()


class SelectRange(unittest.TestCase):
    def test_returns_the_context_replace_accepts_or_collapses(self):
        text = "i thinks so"
        field = field_for(text)
        good = Context("thinks", 7, 2, True, False, FIELD, (2, 6))
        with mock.patch.object(edit, "_same_field", return_value=FIELD), \
                mock.patch.object(edit, "_cf_range", side_effect=lambda s, n: (s, n)), \
                mock.patch.object(edit, "AXUIElementSetAttributeValue", return_value=edit.kAXErrorSuccess) as set_, \
                mock.patch.object(edit, "collapse") as collapse:
            with mock.patch.object(edit, "capture", return_value=good):
                self.assertIs(edit.select_range(field, 2, 8, "thinks"), good)
            set_.assert_called_with(FIELD, "AXSelectedTextRange", (2, 6))
            with mock.patch.object(edit, "capture", return_value=Context("thinks", 7, 2, True, False, OTHER, (2, 6))):
                self.assertIsNone(edit.select_range(field, 2, 8, "thinks"))
            collapse.assert_called_once()
            self.assertIsNone(edit.select_range(field, 2, 8, "nope!!"))



class MarksTest(unittest.TestCase):
    def test_strips_ignore_the_mouse_and_hit_finds_the_word_or_badge(self):
        from harness_discussion.mac import marks
        m = marks.Marks()
        try:
            m.show([("a", (10, 20, 50, 38)), ("a", (0, 40, 30, 58))], (400, 100, 3))
            self.assertEqual(len(m._strips), 2)
            self.assertTrue(all(p.ignoresMouseEvents() for p in m._strips))
            self.assertFalse(m.badge.ignoresMouseEvents())
            self.assertEqual(m.hit(12, 25), "a")
            self.assertIsNone(m.hit(55, 25))
            left, top, right, bottom = m._badge_at
            self.assertEqual(m.hit(left + 1, top + 1), "badge")
            m.hide()
            self.assertIsNone(m.hit(12, 25))
            self.assertFalse(m._strips[0].isVisible())
        finally:
            m.hide()


class Confirm(unittest.TestCase):
    def test_the_first_button_can_be_named(self):
        import inspect
        from harness_discussion.mac import host
        self.assertEqual(inspect.signature(host.MacHost.confirm).parameters["ok"].default, "Download")


if __name__ == "__main__":
    unittest.main()
