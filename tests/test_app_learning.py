import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness_discussion import app_learning, learning
from harness_discussion.edit_logic import Context, Field
from harness_discussion.practice import PracticeEdit


class App(app_learning.Learning):
    def __init__(self):
        self.cfg = {"writer": True, "dictation_learn": True, "dictation_words": []}
        self._writer_closed, self.events, self.messages = False, queue.Queue(), []
        self.ctx = Context("", 1, 2, True, False, "field", (0, 0), b"before")
        self.field = Field(1, 2, "fixture", "Fixture", "", (0, 0), b"before")
        self.edit = SimpleNamespace(NEWLINE="\n", observe=lambda ctx: self.field)

    def change(self, key, value):
        self.cfg[key] = self._learning_setting(key, value)

    def _toast(self, text, actions=()):
        self.messages.append((text, dict(actions)))

    def arm(self):
        before = self._learning_before(self.ctx)
        with patch.object(app_learning.time, "monotonic", return_value=0):
            self._learning_arm(self.ctx, before, "Ask Zach today")
        self.field = Field(1, 2, "fixture", "Fixture", "Ask Zach today", (14, 14), b"after")
        self._learning_tick(0.5)
        return self._learning


class LearningApp(unittest.TestCase):
    def test_deployed_practice_wrapper_forwards_bound_observation(self):
        app = App()
        real = app.edit
        tour = SimpleNamespace(practice_box=lambda: None, sandboxed=lambda *keys: False)
        app.edit = PracticeEdit(real, None, None, tour)
        self.assertIs(app._learning_before(app.ctx), app.field)
        tour.sandboxed = lambda *keys: True
        self.assertIsNone(app._learning_before(app.ctx))
        tour.sandboxed = lambda *keys: False
        tour.practice_box = lambda: object()
        self.assertIsNone(app._learning_before(app.ctx))

    def test_expired_window_never_reads_the_field(self):
        app = App()
        watch = app.arm()
        app.edit.observe = Mock()
        app._learning_tick(31)
        app.edit.observe.assert_not_called()
        self.assertTrue(watch.closed)

    def test_exact_insertion_then_settled_change_emits_and_saves_words(self):
        app = App()
        watch = app.arm()
        self.assertTrue(watch.confirmed)
        app.field = Field(1, 2, "fixture", "Fixture", "Ask Tzach today", (9, 9), b"edited")
        app._learning_tick(1)
        self.assertTrue(app.events.empty())
        app._learning_tick(2.6)
        kind, incoming, words = app.events.get_nowait()
        self.assertEqual((kind, incoming, words), ("learning_words", watch, ["Tzach"]))
        with patch.object(app_learning.settings, "save") as save:
            app._learning_words(incoming, words)
        self.assertEqual(save.call_args.args[0]["dictation_words"], ["Tzach"])
        self.assertEqual(app.cfg["dictation_words"], ["Tzach"])
        app.messages[-1][1]["Undo"]()
        self.assertEqual(app.cfg["dictation_words"], [])
        self.assertTrue(watch.closed)

    def test_manual_clear_edit_and_disable_reject_queued_batches(self):
        for key, value in [("dictation_words", []), ("dictation_words", ["Yonatan"]),
                           ("dictation_learn", False), ("writer", False)]:
            with self.subTest(key=key, value=value):
                app = App()
                watch = app.arm()
                app.events.put(("learning_words", watch, ["Tzach"]))
                app.change(key, value)
                _, watch, words = app.events.get_nowait()
                with patch.object(app_learning.settings, "save") as save:
                    app._learning_words(watch, words)
                save.assert_not_called()
                self.assertNotIn("Tzach", app.cfg["dictation_words"])

    def test_observation_failure_and_changed_baseline_do_not_arm(self):
        app = App()
        app.field = None
        self.assertIsNone(app._learning_before(app.ctx))
        app._learning_arm(app.ctx, None, "words")
        self.assertIsNone(app._learning)
        app = App()
        app.field = Field(1, 2, "fixture", "Fixture", "changed", (0, 0), b"changed")
        self.assertIsNone(app._learning_before(app.ctx))

    def test_persistence_failure_preserves_words_and_reports_no_success(self):
        app = App()
        watch = app.arm()
        with patch.object(app_learning.settings, "save", side_effect=OSError):
            app._learning_words(watch, ["Tzach"])
        self.assertEqual(app.cfg["dictation_words"], [])
        self.assertEqual(app.messages[-1], ("Could not save learned words.", {}))
        self.assertIsNone(app._learning)

    def test_undo_restores_words_evicted_at_capacity(self):
        app = App()
        app.cfg["dictation_words"] = learning.normalize(["Word" + chr(65 + i // 26) + chr(65 + i % 26)
                                                        for i in range(200)])
        original = app.cfg["dictation_words"][:]
        watch = app.arm()
        with patch.object(app_learning.settings, "save"):
            app._learning_words(watch, ["Tzach"])
        self.assertEqual(len(app.cfg["dictation_words"]), 200)
        app.messages[-1][1]["Undo"]()
        self.assertEqual(app.cfg["dictation_words"], original)


if __name__ == "__main__":
    unittest.main()
