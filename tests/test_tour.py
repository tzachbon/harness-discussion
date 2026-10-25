"""The tour's progress rules, copy, sample texts, view model and settings keys."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness_discussion import hotkeys, live, settings, tour, tour_copy, tour_view
from harness_discussion import writer_settings as ws


MAC_CFG = dict(settings.DEFAULTS, hotkey="<cmd>+<alt>+r", rewrite_hotkey="<cmd>+<alt>+e",
               dictate_hotkey="<cmd>+<alt>+d")  # fixed specs, so label tests pass on both CIs


def state(**over):
    base = dict(mac=True, trusted=True, stale=False, cfg=dict(MAC_CFG),
                progress={}, statuses={}, writer_statuses={}, stt_statuses={}, sandboxed=set(),
                voice_source="Cursor", writer_source="Cursor", stt_source="Local",
                has_practice_edit=False, review_text=None, sample_used=set())
    base.update(over)
    return SimpleNamespace(**base)


class ProgressTest(unittest.TestCase):
    def test_resume(self):
        self.assertEqual(tour.resume_at({}), 0)
        self.assertEqual(tour.resume_at({"welcome": "tried", "voice": "skipped"}), 2)
        self.assertEqual(tour.resume_at({s: "tried" for s in tour.STEPS}), 8)

    def test_mark_keeps_a_tried_step(self):
        self.assertEqual(tour.mark({"read": "tried"}, "read", "skipped")["read"], "tried")
        self.assertEqual(tour.mark({}, "read", "skipped")["read"], "skipped")

    def test_clean_drops_unknown_steps_and_marks(self):
        self.assertEqual(tour.clean([]), {})
        self.assertEqual(tour.clean({"read": "tried", "x": "tried", "voice": 1}), {"read": "tried"})
        self.assertEqual(tour.tried_count({"read": "tried", "voice": "skipped"}), 1)


class SettingsKeysTest(unittest.TestCase):
    def load(self, saved):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps(saved), encoding="utf-8")
            return settings.load(str(path)), path

    def test_a_file_without_the_keys_gets_the_defaults(self):
        s, _ = self.load({"speed": 1.5})
        self.assertIs(s["tour_done"], False)
        self.assertEqual(s["tour_steps"], {})

    def test_a_non_bool_tour_done_becomes_false(self):
        s, _ = self.load({"tour_done": "yes"})
        self.assertIs(s["tour_done"], False)

    def test_marks_survive_a_save_and_load(self):
        s, path = self.load({})
        s["tour_steps"] = {"rewrite": "skipped"}
        settings.save(s, str(path))
        self.assertEqual(settings.load(str(path))["tour_steps"], {"rewrite": "skipped"})


class SamplesTest(unittest.TestCase):
    def test_the_sample_rewrite_fixes_every_known_mistake(self):
        self.assertEqual(tour_copy.sample_fix(tour_copy.REWRITE),
                         "We received the report yesterday, but nobody has read it yet, so let's meet tomorrow.")
        self.assertEqual(tour_copy.sample_fix("recieved the"), "received the")
        self.assertEqual(tour_copy.sample_fix(tour_copy.LIVE), "I received the files yesterday")

    def test_the_live_sample_is_a_checkable_line(self):
        self.assertTrue(live.checkable(tour_copy.LIVE))

    def test_the_rewrite_sample_is_the_fixture_line(self):
        self.assertIn(tour_copy.REWRITE, Path("spikes/fixtures/live/page.html").read_text(encoding="utf-8"))


def no_writer(s):
    return state(**{**s.__dict__, "writer_source": None})


class ViewTest(unittest.TestCase):
    def setUp(self):
        p = patch.object(hotkeys.sys, "platform", "darwin")
        p.start()
        self.addCleanup(p.stop)

    def test_windows_welcome_has_no_card_and_next_is_primary(self):
        v = tour_view.view(0, state(mac=False))
        self.assertEqual(v["rows"], [])
        self.assertTrue(v["ready"])

    def test_mac_welcome_without_access_asks_for_it(self):
        v = tour_view.view(0, state(trusted=False))
        self.assertFalse(v["ready"])
        self.assertEqual([r["actions"] for r in v["rows"]], [[("Allow access", ("allow", None))]])
        v = tour_view.view(0, state(trusted=False, stale=True))
        self.assertEqual(v["rows"][0]["sub"], "Harness Discussion was updated, so macOS needs permission again.")
        self.assertEqual(v["rows"][0]["actions"], [("Fix permission", ("allow", None))])
        self.assertEqual(tour_view.view(0, state())["rows"][0]["pill"], "Allowed")

    def test_the_voice_step_lists_every_source_with_its_status(self):
        cfg = dict(settings.DEFAULTS)
        cfg["sources"] = [["Cursor", True], ["macOS", True]]
        v = tour_view.view(1, state(cfg=cfg, statuses={}))
        self.assertEqual([r["sub"] for r in v["rows"]], ["Checking…", "Checking…"])
        v = tour_view.view(1, state(cfg=cfg, statuses={"Cursor": "signed_out", "macOS": "ready"}))
        self.assertEqual([r["sub"] for r in v["rows"]], ["Not signed in", "Ready"])
        self.assertEqual(v["rows"][0]["actions"], [("Sign in…", ("fix_source", ("Cursor",)))])
        self.assertEqual(v["rows"][1]["actions"], [])

    def test_a_voice_row_with_no_status_yet_reads_checking(self):
        v = tour_view.view(1, state(cfg=dict(settings.DEFAULTS, sources=[["Cursor", True]]), statuses={}))
        self.assertEqual(v["rows"][0]["sub"], "Checking…")

    def test_no_source_cards_offer_a_fix_for_every_source_that_is_not_ready(self):
        cfg = dict(settings.DEFAULTS)
        cfg["writer_sources"] = [["Cursor", True], ["Codex", True], ["Local", True]]
        cfg["stt_sources"] = [["Local", True], ["Cursor", True], ["Codex", True]]
        s = state(cfg=cfg, writer_statuses={}, stt_statuses={"Cursor": "ready"})
        for i in (3, 4, 6):
            v = tour_view.view(i, no_writer(s))
            row = v["rows"][-1]
            self.assertEqual(row["name"], "No writing source is ready")
            self.assertEqual(row["sub"], "Sign in for real results. Until then, you'll see a sample.")
            self.assertIn(("Sign in to Cursor", ("fix_writer", ("Cursor", False))), row["actions"])
            self.assertIn(("Sign in to Codex", ("fix_writer", ("Codex", False))), row["actions"])
            self.assertIn(("Download Local…", ("fix_writer", ("Local", False))), row["actions"])
        v = tour_view.view(5, state(**{**s.__dict__, "stt_source": None}))
        row = v["rows"][-1]
        self.assertEqual(row["name"], "No speech source is ready")
        self.assertEqual([("Download Local", ("fix_writer", ("Local", True))),
                          ("Sign in to Codex", ("fix_writer", ("Codex", True)))], row["actions"])

    def test_the_rewrite_keep_row_follows_the_sandbox(self):
        s = state(sandboxed={"writer"}, cfg=dict(settings.DEFAULTS, live_check=False))
        self.assertEqual(tour_view.view(3, s)["keep"],
                         {"label": "Keep Writing on in all apps", "action": "keep_writer", "note": None})
        s = state(sandboxed={"writer"})
        self.assertEqual(tour_view.view(3, s)["keep"]["note"], "Check as I type comes on with it.")
        s = state(sandboxed=set())
        self.assertEqual(tour_view.view(3, s)["keep"], {"text": "Writing is on. ⌥⌘E works in any app."})

    def test_the_live_keep_row_has_three_cases(self):
        self.assertEqual(tour_view.view(6, state(sandboxed={"live_check"}))["keep"],
                         {"label": "Check as I type in all apps", "action": "keep_live", "note": "asks first"})
        self.assertEqual(tour_view.view(6, state(sandboxed={"writer"}))["keep"]["action"], "keep_writer")
        self.assertEqual(tour_view.view(6, state(sandboxed=set()))["keep"]["text"],
                         "Check as I type is on in all apps: lines you type there go to Cursor after a pause. "
                         "Turn it off in Settings → Writing.")

    def test_destination_lines_name_the_first_ready_source(self):
        s = state()
        self.assertEqual(tour_view.view(1, s)["dest"], "Read by Cursor. Online voices get only this sample text.")
        self.assertEqual(tour_view.view(2, s)["dest"], "Read by Cursor. Online voices get only the text you select here.")
        self.assertEqual(tour_view.view(3, s)["dest"],
                         "Sent to Cursor, the first ready writing source. Only this sample text is sent, with your "
                         "About you note and today's date.")
        self.assertIsNone(tour_view.view(4, s)["dest"])
        self.assertEqual(tour_view.view(5, s)["dest"], "Your recording goes to Local. macOS asks for the microphone the first time.")
        self.assertEqual(tour_view.view(6, s)["dest"], "Only lines you type in this box are sent, with your About you "
                                                       "note and today's date, to Cursor.")
        s.cfg = dict(s.cfg, about_me_on=False)
        self.assertEqual(tour_view.view(6, s)["dest"], "Only lines you type in this box are sent, to Cursor.")
        self.assertEqual(tour_view.view(3, s)["dest"],
                         "Sent to Cursor, the first ready writing source. Only this sample text is sent.")

    def test_a_sample_result_changes_the_status_and_the_destination(self):
        s = state(progress={"dictate": "tried"}, sample_used={"dictate"}, stt_source=None)
        v = tour_view.view(5, s)
        self.assertEqual(v["status"], ("✓ Typed where your cursor was, using a sample transcript.", True))
        s = state(progress={"rewrite": "tried"}, sample_used={"rewrite"}, writer_source=None)
        v = tour_view.view(3, s)
        self.assertEqual(v["status"], ("✓ Rewritten in place, using a sample result.", True))
        self.assertTrue(v["sample"])
        s = state(progress={"live": "tried"}, sample_used={"live"}, writer_source=None)
        v = tour_view.view(6, s)
        self.assertEqual(v["status"], ("✓ Applied. Undo is on the toast.", True))

    def test_the_done_status_of_every_step(self):
        marks = {"welcome": "tried", "voice": "tried", "read": "tried", "rewrite": "tried",
                 "review": "tried", "dictate": "tried", "live": "tried"}
        self.assertEqual(tour_view.view(1, state(progress=marks))["status"], ("✓ Played with Cursor", True))
        v = tour_view.view(2, state(progress=marks))
        self.assertEqual(v["status"], ("✓ You played it from the bar. Next time: ⌥⌘R reads a selection without the bar.", True))
        self.assertEqual(tour_view.view(3, state(progress=marks))["status"],
                         ("✓ Rewritten in place. Undo puts your words back.", True))
        self.assertEqual(tour_view.view(4, state(progress=marks))["status"], ("✓ You opened Review.", True))
        self.assertEqual(tour_view.view(5, state(progress=marks))["status"], ("✓ Typed where your cursor was.", True))
        self.assertEqual(tour_view.view(6, state(progress=marks))["status"], ("✓ Applied. Undo is on the toast.", True))
        self.assertEqual(tour_view.view(5, state(progress={}))["status"], None)

    def test_shortcut_rows_per_step(self):
        self.assertEqual(tour_view.view(2, state())["shortcuts"], [("hotkey", None)])
        self.assertEqual(tour_view.view(3, state())["shortcuts"], [("rewrite_hotkey", None)])
        self.assertEqual(tour_view.view(5, state())["shortcuts"], [("dictate_hotkey", None)])
        self.assertEqual(tour_view.view(8, state())["shortcuts"],
                         [("hotkey", None), ("rewrite_hotkey", "press twice to pick a mode"), ("dictate_hotkey", None)])
        for i in (0, 1, 4, 6, 7):
            self.assertEqual(tour_view.view(i, state())["shortcuts"], [])

    def test_leads_follow_the_shortcuts(self):
        s = state()
        self.assertEqual(tour_view.view(3, s)["lead"], "Select the text and press ⌥⌘E, or ✎ on the bar.")
        s = state(cfg=dict(settings.DEFAULTS, rewrite_hotkey="<ctrl>+<alt>+e"))
        self.assertEqual(tour_view.view(3, s)["lead"], "Select the text and press ⌃⌥E, or ✎ on the bar.")
        s = state(cfg=dict(settings.DEFAULTS, rewrite_hotkey=""))
        self.assertEqual(tour_view.view(3, s)["lead"], "Select the text, then press ✎ on the bar.")
        s = state(cfg=dict(settings.DEFAULTS, dictate_hotkey=""))
        self.assertEqual(tour_view.view(5, s)["lead"],
                         "Press Change… to set a dictation shortcut, then hold it and say the line.")
        s = state(cfg=dict(settings.DEFAULTS, rewrite_hotkey=""))
        self.assertEqual(tour_view.view(4, state(cfg=dict(settings.DEFAULTS, rewrite_hotkey=""),
                                                 has_practice_edit=False))["lead"],
                         "Click Review on the toast. Struck-through words were removed, green words are new. "
                         "Click a change to keep your original there.")

    def test_read_names_the_bar_without_a_shortcut(self):
        s = state(cfg=dict(settings.DEFAULTS, hotkey=""))
        v = tour_view.view(2, state(progress={"read": "tried"}, cfg=dict(settings.DEFAULTS, hotkey="")))
        self.assertEqual(v["status"], ("✓ You played it from the bar.", True))

    def test_the_review_step_follows_the_last_practice_edit(self):
        s = state(has_practice_edit=True, review_text="Fixed text.")
        v = tour_view.view(4, s)
        self.assertEqual(v["box"], {"text": "Fixed text.", "select": None})  # Review's Replace edits this box
        self.assertEqual(v["rows"], [{"name": "Click Review on the toast, or", "sub": None, "pill": None,
                                      "ok": True, "actions": [("Open Review", ("review", None))]}])
        v = tour_view.view(4, state(has_practice_edit=False))
        self.assertEqual(v["box"], {"text": tour_copy.REWRITE, "select": "all"})
        self.assertEqual(v["rows"][0]["name"], "Press ⌥⌘E first, then Review.")

    def test_every_step_owns_its_box(self):
        s = state()
        self.assertIsNone(tour_view.view(0, s)["box"])
        self.assertIsNone(tour_view.view(1, s)["box"])
        self.assertEqual(tour_view.view(2, s)["box"], {"text": tour_copy.READ, "select": None})
        self.assertEqual(tour_view.view(3, s)["box"], {"text": tour_copy.REWRITE, "select": "all"})
        self.assertEqual(tour_view.view(5, s)["box"], {"text": "", "select": None})
        self.assertEqual(tour_view.view(6, s)["box"], {"text": tour_copy.LIVE, "select": "end"})
        self.assertEqual(tour_view.view(7, s)["box"], {"text": "", "select": "end"})  # About you: the saved note
        self.assertIsNone(tour_view.view(8, s)["box"])
        self.assertEqual(tour_view.view(5, s)["quote"], tour_copy.SAY)

    def test_the_dictation_step_needs_access_on_a_mac(self):
        v = tour_view.view(5, state(trusted=False))
        self.assertEqual(v["rows"][0], {"name": "Dictation needs Accessibility access", "sub": None, "pill": None,
                                        "ok": False, "actions": [("Allow access", ("allow", None))]})

    def test_dots_and_the_step_number(self):
        s = state(progress={"welcome": "tried", "voice": "skipped"})
        v = tour_view.view(2, s)
        self.assertEqual((v["index"], v["count"], v["step"]), (2, 8, "read"))
        self.assertEqual(v["dots"], ["tried", "skipped", "now", "", "", "", "", ""])

    def test_next_is_primary_only_for_a_tried_step(self):
        s = state()
        self.assertFalse(tour_view.view(2, s)["ready"])
        self.assertTrue(tour_view.view(2, state(progress={"read": "tried"}))["ready"])
        self.assertTrue(tour_view.view(0, state(mac=False))["ready"])

    def test_the_done_screen_lists_the_steps_and_the_skipped_one(self):
        progress = {s: "tried" for s in tour.STEPS if s != "live"}
        progress["live"] = "skipped"
        v = tour_view.view(8, state(progress=progress))
        self.assertEqual(v["title"], "You're set: 7 of 8 tried")
        labels = [(label, tried, step) for label, tried, step in v["done"]["rows"]]
        self.assertIn(("Check as I type", False, "live"), labels)
        self.assertIn(("Voice", True, "voice"), labels)
        self.assertEqual(labels[0][0], "Allow access")
        self.assertEqual(tour_view.view(8, state(mac=False, progress=progress))["done"]["rows"][0][0], "Welcome")
        self.assertIn("Summarize on the bar", v["done"]["more"])
        self.assertIsNone(v["status"])


class LiveConfirmTextTest(unittest.TestCase):
    def test_both_platforms_get_their_exact_message(self):
        self.assertEqual(ws.live_confirm_text(["Cursor", "Local"], "this PC"),
                         "When you pause typing, the line you are typing is sent to Cursor, Local in order, "
                         "in every app except password fields, terminals, and apps you turn it off for. "
                         "Cursor and Codex send it to their service. Local keeps it on this PC.")
        self.assertEqual(ws.live_confirm_text(["Cursor"], "this Mac"),
                         "When you pause typing, the line you are typing is sent to Cursor in order, in every "
                         "app except password fields, terminals, and apps you turn it off for. Cursor and Codex "
                         "send it to their service. Local keeps it on this Mac.")


if __name__ == "__main__":
    unittest.main()