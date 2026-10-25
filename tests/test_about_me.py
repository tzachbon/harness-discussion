"""About you: the short personal note and today's date that every rewrite and summary prompt carries."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from harness_discussion import settings, summarize, tour, writer

from tests.test_app_tour_flow import FlowTest
from tests.test_practice import FakeBox

NOW = datetime(2026, 10, 25, 15, 20, tzinfo=timezone(timedelta(hours=2)))

class AboutPromptTest(unittest.TestCase):
    def test_note_and_date_reach_both_prompts(self):
        about = writer.about({"about_me": "Dana, PM in Tel Aviv. Works Sun-Thu.", "about_me_on": True}, NOW)
        self.assertIn("Dana, PM in Tel Aviv. Works Sun-Thu.", about)
        self.assertIn("Sunday, 25 October 2026, 15:20", about)
        self.assertIn("UTC+0200", about)
        mode = writer.BUILTIN_MODES[0]
        self.assertTrue(writer.system_prompt(mode, about=about).endswith(about))
        self.assertTrue(writer.system_prompt(mode, True, about).startswith(about))  # nothing after the local example
        self.assertEqual(writer.system_prompt(mode), writer.system_prompt(mode, about=""))

    def test_no_note_still_sends_the_date_and_off_sends_nothing(self):
        self.assertEqual(writer.about({"about_me": " ", "about_me_on": True}, NOW),
                         "Today is Sunday, 25 October 2026, 15:20 in the writer's time zone (UTC+0200).")
        self.assertEqual(writer.about({"about_me": "Dana", "about_me_on": False}, NOW), "")
        self.assertEqual(writer.about({"about_me": "Dana"}, NOW), "")  # a partial cfg, as in tests and old callers

    def test_rewrite_sends_it_to_the_source(self):
        class Source:
            name, systems = "Cursor", []
            def status(self):
                return "ready"
            def rewrite(self, system, text, model, effort, cancelled):
                self.systems.append(system)
                return "Fixed."
        source = Source()
        writer.rewrite("fixd", writer.BUILTIN_MODES[0], [source], about="ABOUT")
        self.assertTrue(source.systems[0].endswith("\n\nABOUT"))

    def test_settings_cut_and_validate(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                json.dump({"about_me": "x" * 1000, "about_me_on": "yes"}, f)
            s = settings.load(path)
        self.assertEqual(len(s["about_me"]), writer.ABOUT_CHARS)
        self.assertIs(s["about_me_on"], True)

class AboutLimitTest(unittest.TestCase):
    def test_the_limit_cuts_the_note_and_turning_it_off_keeps_more(self):
        note = "x" * 1000
        capped = writer.about({"about_me": note, "about_me_on": True}, NOW)
        self.assertIn("x" * writer.ABOUT_CHARS + "\n</about>", capped)
        uncapped = writer.about({"about_me": note, "about_me_on": True, "about_me_uncapped": True}, NOW)
        self.assertIn(note + "\n</about>", uncapped)
        self.assertEqual(writer.about_limit({"about_me_uncapped": True}), writer.ABOUT_MAX)

    def test_settings_keep_a_long_note_only_when_uncapped(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                json.dump({"about_me": "x" * 1000, "about_me_uncapped": True}, f)
            self.assertEqual(len(settings.load(path)["about_me"]), 1000)
            with open(path, "w") as f:
                json.dump({"about_me": "x" * 99999, "about_me_uncapped": True}, f)
            self.assertEqual(len(settings.load(path)["about_me"]), writer.ABOUT_MAX)

class AboutContextBudgetTest(unittest.TestCase):
    """A long About you note shares the local model's 8192-token context with the text."""

    def test_summary_text_shrinks_by_the_note(self):
        server, sent = summarize.Server(), []
        server.complete = lambda system, user, *a: sent.append(user) or "Short."
        note = "n" * 5000
        server.summarize("t" * summarize.MAX_CHARS, "English", about=note)
        self.assertEqual(sent[0], f"<text>\n{'t' * (summarize.MAX_CHARS - len(note))}\n</text>")

    def test_local_rewrite_refuses_text_a_long_note_leaves_no_room_for(self):
        sent = []
        local = summarize.LocalWriter(type("S", (), {"complete": lambda self, *a, **k: sent.append(a) or "ok"})())
        text = "t" * (summarize.MAX_CHARS // 2)
        capped = writer.system_prompt(writer.BUILTIN_MODES[0], True, writer.about(
            {"about_me": "n" * writer.ABOUT_CHARS, "about_me_on": True}))
        self.assertEqual(local.rewrite(capped, text, None, None, lambda: False), "ok")  # a capped note costs nothing
        with self.assertRaises(summarize.Error):
            local.rewrite(capped + "n" * 5000, text, None, None, lambda: False)

class AboutLiveCacheTest(unittest.TestCase):
    def test_changing_about_you_drops_cached_checks_and_cancels_one_in_flight(self):
        import threading
        from harness_discussion import app_live
        for key in ("about_me", "about_me_on", "about_me_uncapped"):
            app = type("A", (), {"marks": None})()
            app._live_cancel, app.live_cache = threading.Event(), type("C", (), {"cleared": 0})()
            app.live_cache.clear = lambda c=app.live_cache: setattr(c, "cleared", c.cleared + 1)
            app_live.Live._apply_live(app, key, "x")
            self.assertTrue(app._live_cancel.is_set(), key)
            self.assertEqual(app.live_cache.cleared, 1, key)

class AboutTourTest(FlowTest):
    def test_leaving_the_step_saves_the_box_and_marks_it_tried(self):
        self.app.open()
        self.app.tour_go(tour.STEPS.index("about"))
        self.assertEqual(self.app.tour.shown[-1]["box"], {"text": "", "select": "end"})
        self.app.tour.box = FakeBox("  Dana, PM. Works Sun-Thu.  ")
        self.app.tour_next()
        self.assertEqual(self.last_saved()["about_me"], "Dana, PM. Works Sun-Thu.")
        self.assertEqual(self.app.cfg["tour_steps"]["about"], tour.TRIED)

    def test_closing_on_the_step_saves_it_too(self):
        self.app.open()
        self.app.tour_go(tour.STEPS.index("about"))
        self.app.tour.box = FakeBox("Dana")
        self.app.tour_closed()
        self.assertEqual(self.app.cfg["about_me"], "Dana")

if __name__ == "__main__":
    unittest.main()
