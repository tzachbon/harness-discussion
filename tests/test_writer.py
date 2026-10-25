"""Writer core: modes, prompt, the source chain, fallback, cancel, log hygiene, and the word diff. No network."""
import unittest

from harness_discussion import writer


class Fake:
    def __init__(self, name, status="ready", reply="ok", error=None):
        self.name, self._status, self.reply, self.error = name, status, reply, error
        self.calls = []

    def status(self):
        return self._status

    def rewrite(self, system, text, model, effort, cancelled):
        self.calls.append((system, text, model, effort))
        if self.error:
            raise self.error
        return self.reply


class ModesTest(unittest.TestCase):
    def test_custom_mode_replaces_builtin_by_name(self):
        modes = writer.all_modes([{"name": "Email", "instructions": "Be brief."}, {"name": "My PR voice", "instructions": "Plain."}])
        names = [m.name for m in modes]
        self.assertEqual(names.count("Email"), 1)
        self.assertEqual(writer.find_mode("Email", modes).instructions, "Be brief.")
        self.assertIn("My PR voice", names)

    def test_bad_custom_entries_are_ignored(self):
        modes = writer.all_modes([{"name": "  ", "instructions": "x"}, {"name": 3}, "nope"])
        self.assertEqual([m.name for m in modes], [m.name for m in writer.BUILTIN_MODES])

    def test_unknown_mode_falls_back_to_first(self):
        modes = writer.all_modes([])
        self.assertEqual(writer.find_mode("Gone", modes).name, "Fix errors")

    def test_full_prompt_has_mode_rules_and_data_framing(self):
        message = writer.find_mode("Message", writer.BUILTIN_MODES)
        p = writer.system_prompt(message)
        self.assertIn(message.instructions, p)
        self.assertIn("<text>", p)
        self.assertIn(writer.STYLE, p)
        self.assertNotIn(writer.STYLE, writer.system_prompt(writer.find_mode("Fix errors", writer.BUILTIN_MODES)))

    def test_short_prompts_stay_small(self):
        for mode in writer.BUILTIN_MODES:
            self.assertLessEqual(len(writer.system_prompt(mode, short=True).split()), 170, mode.name)

    def test_clean_dictation_prompts_cover_spoken_structure_without_changing_facts(self):
        mode = writer.find_mode("Clean dictation", writer.BUILTIN_MODES)
        for short in (False, True):
            prompt = writer.system_prompt(mode, short=short)
            self.assertIn("numbered list", prompt)
            self.assertIn("1, 2, 3", prompt)
            self.assertIn("factual numbers", prompt)
            self.assertIn("self-corrections", prompt)
            self.assertNotIn("names, numbers", prompt)
        self.assertIn("list markers and list formatting are exceptions", writer.system_prompt(mode))
        self.assertIn("task numbering overrides list markers", writer.system_prompt(mode, short=True))
        self.assertFalse(mode.styled)

    def test_override_keeps_style_but_uses_its_own_words_on_the_short_tier(self):
        long = "Sound like me. " + "word " * 300
        mode = writer.find_mode("Message", writer.all_modes([{"name": "Message", "instructions": long}]))
        self.assertIn(writer.STYLE, writer.system_prompt(mode))
        short = writer.system_prompt(mode, short=True)
        self.assertIn("Sound like me.", short)
        self.assertNotIn(writer.find_mode("Message", writer.BUILTIN_MODES).brief, short)
        self.assertLessEqual(len(short.split()), 210)

    def test_custom_mode_short_tier_uses_its_instructions(self):
        mode = writer.find_mode("Tweet", writer.all_modes([{"name": "Tweet", "instructions": "Under 280 characters."}]))
        self.assertIn("Under 280 characters.", writer.system_prompt(mode, short=True))
        notes = writer.all_modes([{"name": "Notes", "instructions": "Write a release note. Never name customers."}])
        self.assertIn("Never name customers.", writer.system_prompt(writer.find_mode("Notes", notes), short=True))
        self.assertNotIn(writer.STYLE, writer.system_prompt(mode))


class TidyTest(unittest.TestCase):
    def test_strips_a_preamble_line(self):
        self.assertEqual(writer.tidy("Here is the rewritten text:\nShip it.", "ship it"), "Ship it.")

    def test_keeps_a_preamble_the_draft_had(self):
        draft = "Here are the steps:\n1. Build\n2. Ship"
        self.assertEqual(writer.tidy(draft, draft), draft)
        self.assertEqual(writer.tidy("Here are the blockers:\n1. Approval", "here are the blockers:\n1. aproval"),
                         "Here are the blockers:\n1. Approval")

    def test_unwraps_quotes_the_draft_did_not_have(self):
        self.assertEqual(writer.tidy('"Ship it."', "ship it"), "Ship it.")
        self.assertEqual(writer.tidy('"Ship it."', '"ship it"'), '"Ship it."')

    def test_strips_echoed_tags(self):
        self.assertEqual(writer.tidy("<text>\nShip it.\n</text>", "ship it"), "Ship it.")

    def test_plain_punctuation_in_prose_only(self):
        self.assertEqual(writer.tidy("We ship — today. “Done” it’s…", "x"), "We ship, today. \"Done\" it's...")
        self.assertEqual(writer.tidy("Run `a — b` now", "x"), "Run `a — b` now")
        self.assertEqual(writer.tidy("```\na — b\n```", "x"), "```\na — b\n```")
        self.assertEqual(writer.tidy("— first\n— second", "x"), "- first\n- second")
        self.assertEqual(writer.tidy("pages 3–5", "x"), "pages 3–5")
        self.assertEqual(writer.tidy('~~~py\nm = "Wait…"\n~~~', "x"), '~~~py\nm = "Wait…"\n~~~')
        self.assertEqual(writer.tidy('Run:\n\n    m = "Wait…"\n\nDone…', "x"), 'Run:\n\n    m = "Wait…"\n\nDone...')

    def test_idempotent(self):
        once = writer.tidy("Sure, here you go:\n“A” — b", "a b")
        self.assertEqual(writer.tidy(once, "a b"), once)


class ChainTest(unittest.TestCase):
    def test_order_and_disabled_sources(self):
        a, b, c = Fake("A"), Fake("B"), Fake("C")
        self.assertEqual([s.name for s in writer.chain([a, b, c], [["C", True], ["A", False], ["B", True]])], ["C", "B"])

    def test_unknown_names_are_dropped(self):
        self.assertEqual(writer.chain([Fake("A")], [["Zed", True], ["A", True]])[0].name, "A")

    def test_skips_sources_that_are_not_ready(self):
        down, up = Fake("Cursor", status="missing"), Fake("Local", reply="done")
        out, name = writer.rewrite("hi", writer.BUILTIN_MODES[0], [down, up])
        self.assertEqual((out, name), ("done", "Local"))
        self.assertEqual(down.calls, [])

    def test_falls_through_on_failure_and_names_the_source(self):
        bad = Fake("Codex", error=RuntimeError("boom"))
        good = Fake("Local", reply="  fixed  ")
        out, name = writer.rewrite("hi", writer.BUILTIN_MODES[0], [bad, good])
        self.assertEqual((out, name), ("fixed", "Local"))

    def test_empty_reply_counts_as_failure(self):
        empty, good = Fake("Codex", reply="   "), Fake("Local", reply="ok")
        self.assertEqual(writer.rewrite("hi", writer.BUILTIN_MODES[0], [empty, good])[1], "Local")

    def test_reply_that_is_only_an_echo_counts_as_failure(self):
        echo, good = Fake("Codex", reply="<text></text>"), Fake("Local", reply="ok")
        self.assertEqual(writer.rewrite("hi", writer.BUILTIN_MODES[0], [echo, good])[1], "Local")

    def test_wraps_text_tidies_reply_and_picks_tier_per_source(self):
        cloud, local = Fake("Codex", error=RuntimeError("x")), Fake("Local", reply="Sure:\n“Hi” — there")
        local.short_prompt = True
        mode = writer.BUILTIN_MODES[0]
        out, _ = writer.rewrite("hi there", mode, [cloud, local])
        self.assertEqual(out, "\"Hi\", there")
        self.assertEqual(cloud.calls[0][:2], (writer.system_prompt(mode), writer.wrap("hi there")))
        self.assertEqual(local.calls[0][0], writer.system_prompt(mode, short=True))

    def test_passes_chosen_model_and_effort(self):
        s = Fake("Codex", reply="x")
        writer.rewrite("hi", writer.BUILTIN_MODES[0], [s], models={"Codex": {"model": "gpt-5.5", "effort": "low"}})
        self.assertEqual(s.calls[0][2:], ("gpt-5.5", "low"))

    def test_cancel_stops_before_next_source(self):
        first, second = Fake("A", reply="x"), Fake("B", reply="y")
        with self.assertRaises(writer.Cancelled):
            writer.rewrite("hi", writer.BUILTIN_MODES[0], [first, second], cancelled=lambda: True)
        self.assertEqual(second.calls, [])

    def test_cancel_inside_source_is_not_retried(self):
        s, other = Fake("A", error=writer.Cancelled("stop")), Fake("B", reply="y")
        with self.assertRaises(writer.Cancelled):
            writer.rewrite("hi", writer.BUILTIN_MODES[0], [s, other])
        self.assertEqual(other.calls, [])

    def test_all_fail_raises_safe_error(self):
        with self.assertRaises(writer.Error) as ctx:
            writer.rewrite("secret text", writer.BUILTIN_MODES[0], [Fake("A", status="missing")])
        self.assertNotIn("secret", str(ctx.exception))

    def test_log_never_contains_the_text(self):
        secret = "my private draft sentence"
        s = Fake("Codex", error=ValueError(secret))
        with self.assertLogs("harness_discussion", level="INFO") as logs:
            writer.rewrite(secret, writer.BUILTIN_MODES[0], [s, Fake("Local", reply="ok")])
        joined = "\n".join(logs.output)
        self.assertIn("Codex", joined)
        self.assertIn("ValueError", joined)
        self.assertNotIn(secret, joined)


class DiffTest(unittest.TestCase):
    def test_equal(self):
        self.assertEqual(writer.diff("a b", "a b"), [("=", "a b")])

    def test_replace_keeps_spacing(self):
        self.assertEqual(writer.diff("we should of shipped", "we should have shipped"),
                         [("=", "we should "), ("-", "of"), ("+", "have"), ("=", " shipped")])

    def test_insert_and_delete(self):
        self.assertEqual(writer.diff("hi there", "hi big there"), [("=", "hi "), ("+", "big "), ("=", "there")])
        self.assertEqual(writer.diff("hi big there", "hi there"), [("=", "hi "), ("-", "big "), ("=", "there")])

    def test_rejoining_pieces_gives_the_new_text(self):
        old, new = "a quick brown fox", "the slow brown dog"
        out = writer.diff(old, new)
        self.assertEqual("".join(t for op, t in out if op in "=+"), new)
        self.assertEqual("".join(t for op, t in out if op in "=-"), old)


class HunksTest(unittest.TestCase):
    def test_each_change_reverts_on_its_own(self):
        old, new = "i thinks we should of shiped it", "I think we should have shipped it"
        pairs = writer.hunks(old, new)
        changes = [i for i, (a, b) in enumerate(pairs) if a != b]
        self.assertEqual(writer.merge(pairs, set()), new)
        self.assertEqual(writer.merge(pairs, set(changes)), old)
        self.assertEqual(writer.merge(pairs, {changes[0]}), "i think we should have shipped it")

    def test_pure_insert_and_delete(self):
        pairs = writer.hunks("a b", "a x b")
        self.assertEqual(writer.merge(pairs, {i for i, (a, b) in enumerate(pairs) if a != b}), "a b")
        pairs = writer.hunks("a x b", "a b")
        self.assertEqual(writer.merge(pairs, set()), "a b")


if __name__ == "__main__":
    unittest.main()
