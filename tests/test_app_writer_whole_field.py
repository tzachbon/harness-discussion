"""The rewrite shortcut with nothing selected rewrites the whole field."""
import unittest

from harness_discussion import live
from harness_discussion.edit_logic import Context, Field
from tests.test_app_writer import FakeEdit, Source, _App


class FieldEdit(FakeEdit):
    """FakeEdit that also reads the focused field, as the platforms with Check as you type do."""

    def __init__(self, text, caret=None):
        super().__init__(Context("", 1, 2, True, False))
        self.text, self.caret, self.selected = text, caret or (len(text), len(text)), None

    def observe(self):
        return Field(1, 2, "notepad.exe", "Notepad", self.text, self.caret, b"digest", None, "element")

    def select_range(self, field, start, end, expected):
        if live.text_at(self.text, start, end) != expected:
            return None
        self.selected, self.caret = (start, end), (start, end)
        self.ctx = Context(expected, 1, 2, True, False)
        return self.ctx


class WholeField(unittest.TestCase):
    def test_with_nothing_selected_the_whole_field_is_rewritten_and_the_toasts_say_so(self):
        app = _App()
        app.host.edit = app.edit = FieldEdit("hi there")
        app.rewrite_selection()
        self.assertEqual(app.edit.selected, (0, 8))
        self.assertEqual(app.toast_.shown[-1][0], "Rewriting the whole field · Fix errors")
        app.drain()
        self.assertEqual(app.edit.pasted, ["Hi there."])
        self.assertEqual(app.toast_.shown[-1][0], "Rewrote the whole field · Fix errors · Cursor")

    def test_a_field_over_the_limit_asks_for_a_selection_and_sends_nothing(self):
        app = _App()
        app.host.edit = app.edit = FieldEdit("ab " * 1334)  # 4,002 characters
        app.rewrite_selection()
        self.assertEqual(app.toast_.shown[-1][0], "Select text to rewrite. This field has more than 4,000 characters.")
        self.assertFalse(app.rewriting)
        self.assertIsNone(app.edit.selected)
        self.assertEqual(app.writer_sources[0].texts, [])

    def test_cancel_or_a_failure_puts_the_caret_back_unless_it_moved(self):
        for how in ("cancel", "fail", "moved"):
            with self.subTest(how=how):
                app = _App([Source("Cursor", RuntimeError("x") if how == "fail" else "Hi there.")])
                app.host.edit = app.edit = FieldEdit("hi there", caret=(3, 3))
                app.rewrite_selection()
                self.assertEqual(app.edit.selected, (0, 8))
                if how == "moved":
                    app.edit.caret = (5, 5)
                    app.cancel_rewrite()
                    self.assertEqual(app.edit.selected, (0, 8))
                    continue
                app.cancel_rewrite() if how == "cancel" else app.drain()
                self.assertEqual(app.edit.selected, (3, 3))
                self.assertEqual(app.edit.pasted, [])

    def test_a_mode_picked_after_a_double_press_also_rewrites_the_whole_field(self):
        app = _App()
        app.host.edit = app.edit = FieldEdit("hi there")
        app.rewrite_selection("Fix errors")
        self.assertEqual(app.edit.selected, (0, 8))
        self.assertEqual(app.toast_.shown[-1][0], "Rewriting the whole field · Fix errors")

    def test_the_bar_rewrites_what_it_captured_never_the_whole_field(self):
        app = _App()
        app.host.edit = app.edit = FieldEdit("hi there")
        app.edit.ctx = Context("hi there", 1, 2, True, False)
        app.rewrite_from_bar("hi there", "Fix errors")
        self.assertIsNone(app.edit.selected)
        self.assertEqual(app.toast_.shown[-1][0], "Rewriting · Fix errors")

    def test_a_selection_in_the_field_is_rewritten_as_before(self):
        app = _App()
        app.host.edit = app.edit = FieldEdit("hi there, ok", caret=(0, 8))
        app.edit.ctx = Context("hi there", 1, 2, True, False)
        app.rewrite_selection()
        self.assertIsNone(app.edit.selected)
        self.assertEqual(app.toast_.shown[-1][0], "Rewriting · Fix errors")
        app.drain()
        self.assertEqual(app.toast_.shown[-1][0], "Fix errors · Cursor")


if __name__ == "__main__":
    unittest.main()
