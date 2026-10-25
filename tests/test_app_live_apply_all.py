"""Check as you type: the rewrite shortcut with nothing selected applies every underlined change."""
import dataclasses
import unittest

from harness_discussion import app_writer
from tests.test_app_live import _App


class ApplyAll(unittest.TestCase):
    def setUp(self):
        self.app = _App()
        self.field = self.app.field_
        self.mode = self.app._find_mode(None)
        self.field.text = "i thinks we should of gone.\nok then\nshe go home now ok"
        self.app.live_cache.put("i thinks we should of gone.", self.mode, "I think we should have gone.")
        self.app.live_cache.put("she go home now ok", self.mode, "She goes home now, ok.")

    def test_every_line_is_fixed_without_a_new_rewrite(self):
        self.app.rewrite_selection()
        self.assertEqual(self.field.text, "I think we should have gone.\nok then\nShe goes home now, ok.")
        self.assertEqual(self.field.pasted, ["I think we should have gone.", "She goes home now, ok."])
        self.assertEqual(self.app.source.texts, [])
        self.assertFalse(self.app.rewriting)
        self.assertEqual(self.app.toast_.shown[-1][0], f"Applied 4 changes. Use {app_writer.UNDO_KEYS} to undo.")
        self.app.tick(10)
        self.assertIsNone(self.app.marks_.shown)  # nothing left to underline, and no new check
        self.assertEqual(self.app.source.texts, [])

    def test_one_line_can_be_undone(self):
        self.field.text = "i thinks we should of gone."
        self.app.rewrite_selection()
        self.assertEqual(self.field.text, "I think we should have gone.")
        message, actions, _ = self.app.toast_.shown[-1]
        self.assertEqual(message, "Applied 2 changes")
        actions["Undo"]()
        self.assertEqual(self.field.text, "i thinks we should of gone.")

    def test_a_dismissed_change_stays(self):
        from harness_discussion import live
        found = live.suggestions(self.field.text, self.app.live_cache, set(), self.mode)
        first = next(s for s in found if s.old == "i thinks")
        self.app._live_dismiss(first)
        self.app.rewrite_selection()
        self.assertTrue(self.field.text.startswith("i thinks we should have gone.\n"))

    def test_a_selection_a_named_mode_or_no_underlines_rewrite_as_before(self):
        original, observe = self.field.text, self.field.observe
        self.field.observe = lambda: dataclasses.replace(observe(), caret=(0, 8))
        self.app.rewrite_selection()
        self.field.observe = observe
        self.app._rewrite_done(self.app._rewrite_id, None, "Fix errors", app_writer.writer.Error("x"), None, None)
        self.app.rewrite_selection("Fix errors")
        self.app._rewrite_done(self.app._rewrite_id, None, "Fix errors", app_writer.writer.Error("x"), None, None)
        self.app.live_cache.clear()
        self.app.rewrite_selection()
        self.assertEqual(self.field.text, original)
        self.assertEqual(self.field.pasted, [])

    def test_a_refused_paste_applies_nothing_and_says_so(self):
        self.field.replace = lambda text, ctx: False
        self.app.rewrite_selection()
        self.assertEqual(self.app.toast_.shown[-1][0], "The text changed. Nothing was applied.")


if __name__ == "__main__":
    unittest.main()
