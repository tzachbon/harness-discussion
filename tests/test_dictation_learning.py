"""Dictation delivery, learning and the next prompt through the actual app entry points."""
import hashlib
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness_discussion import app_learning, app_live, learning, live
from harness_discussion.edit_logic import Context, Field
from tests.test_app_writer import _App


def digest(text):
    return hashlib.sha256(text.encode("utf-16-le")).digest()


class DictationLearning(unittest.TestCase):
    def test_successful_delivery_then_correction_reaches_next_dictation_context(self):
        app = _App()
        self.addCleanup(app._close_writer)
        ctx = Context("", 1, 2, True, False, "field", (0, 0), digest(""))
        document = {"text": "", "caret": (0, 0)}
        def observe(bound):
            self.assertIs(bound, ctx)
            return Field(1, 2, "fixture", "Fixture", document["text"], document["caret"], digest(document["text"]))
        def paste(text, bound, **kwargs):
            self.assertIs(bound, ctx)
            document.update(text=text, caret=(live.units(text),) * 2)
            return True
        app.edit.ctx = ctx
        app.edit.observe, app.edit.type_at_caret = observe, paste
        app.cfg["dictation_words"], app.cfg["dictation_learn"] = [], True
        app._dictate_ctx = ctx
        app.deliver(app._rewrite_id, "Clean dictation", ("Ask Zach today", "Codex", None), (0, 0, None),
                    (time.monotonic(), 0.1, None, "Codex"))
        watch = app._learning
        self.assertIsNotNone(watch)
        app._learning_tick(watch.started + 0.1)
        self.assertTrue(watch.confirmed)
        document.update(text="Ask Tzach today", caret=(9, 9))
        app._learning_tick(watch.started + 1)
        app._learning_tick(watch.started + 2.6)
        with patch.object(app_learning.settings, "save"):
            app.drain("learning_words")
        self.assertEqual(app.cfg["dictation_words"], ["Tzach"])
        vocabulary, tidy = app._vocabulary_context()
        self.assertEqual(vocabulary, "Tzach")
        self.assertIn("Vocabulary spellings", tidy)
        self.assertIn("Tzach", tidy)

    def test_public_setting_change_invalidates_a_queued_learning_result(self):
        for key, value in [("dictation_words", []), ("dictation_words", ["Yonatan"]),
                           ("dictation_learn", False)]:
            app = app_live.App.__new__(app_live.App)
            app.cfg = {"writer": True, "dictation_learn": True, "dictation_words": ["Tzach"]}
            app._read_options, app.settings_win, app.writer_ready = None, None, False
            before = Field(1, 2, "fixture", "Fixture", "", (0, 0), digest(""))
            watch = learning.Watch(object(), before, "Ask Zach today", 0)
            app._learning = watch
            app.change(key, value, save=False)
            with patch.object(app_learning.settings, "save") as save:
                app._learning_words(watch, ["Oldword"])
            save.assert_not_called()
            self.assertNotIn("Oldword", app.cfg["dictation_words"])


if __name__ == "__main__":
    unittest.main()
