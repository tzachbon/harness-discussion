"""Check as you type, the platform-free part: paragraphs, UTF-16 offsets, suggestions from cached rewrites, the cache."""
import unittest

from harness_discussion import live, writer

MODE = writer.BUILTIN_MODES[0]


def cached(*pairs, mode=MODE):
    cache = live.Cache()
    for line, rewrite in pairs:
        cache.put(line, mode, rewrite)
    return cache


class Units(unittest.TestCase):
    def test_counts_utf16_code_units(self):
        self.assertEqual(live.units("ab"), 2)
        self.assertEqual(live.units("👋"), 2)
        self.assertEqual(live.units("שלום"), 4)
        self.assertEqual(live.units("é"), 2)  # e + combining acute


class ParagraphAt(unittest.TestCase):
    def test_the_line_holding_the_caret(self):
        text = "first line\nsecond 👋 line\nthird"
        start = live.units("first line\n")
        self.assertEqual(live.paragraph_at(text, start + 3), (start, start + live.units("second 👋 line")))
        self.assertEqual(live.paragraph_at(text, 0), (0, live.units("first line")))
        self.assertEqual(live.paragraph_at(text, live.units(text)), (live.units(text) - 5, live.units(text)))

    def test_empty_line(self):
        self.assertEqual(live.paragraph_at("a\n\nb", 2), (2, 2))

    def test_text_at(self):
        text = "x 👋 y\nz"
        self.assertEqual(live.text_at(text, 2, 4), "👋")


class Checkable(unittest.TestCase):
    def test_needs_some_words_and_a_bounded_length(self):
        self.assertFalse(live.checkable("too short"))
        self.assertTrue(live.checkable("i thinks we should"))
        self.assertFalse(live.checkable("word " * (live.MAX_PARAGRAPH // 5 + 1)))
        self.assertFalse(live.checkable("   "))


class Suggestions(unittest.TestCase):
    def test_substitution_with_document_offsets(self):
        text = "Hello there.\nשלום 👋 i thinks so\nbye"
        cache = cached(("שלום 👋 i thinks so", "שלום 👋 I think so"))
        found = live.suggestions(text, cache, set(), MODE)
        self.assertEqual([(s.old, s.new) for s in found], [("i thinks", "I think")])
        start = live.units("Hello there.\nשלום 👋 ")
        self.assertEqual((found[0].start, found[0].end), (start, start + live.units("i thinks")))
        self.assertEqual(live.text_at(text, found[0].start, found[0].end), "i thinks")

    def test_insertion_widens_to_the_previous_word(self):
        cache = cached(("we should shipped it", "we should have shipped it"))
        found = live.suggestions("we should shipped it", cache, set(), MODE)
        self.assertEqual([(s.old, s.new) for s in found], [("should ", "should have ")])
        self.assertEqual(live.text_at("we should shipped it", found[0].start, found[0].end), "should ")

    def test_insertion_at_line_start_widens_to_the_next_word(self):
        cache = cached(("think we should go", "I think we should go"))
        found = live.suggestions("think we should go", cache, set(), MODE)
        self.assertEqual([(s.old, s.new) for s in found], [("think", "I think")])
        self.assertEqual(found[0].start, 0)

    def test_insertions_around_one_word_are_one_change(self):
        cache = cached(("think we go", "I think that we go"))
        found = live.suggestions("think we go", cache, set(), MODE)
        self.assertEqual([(s.old, s.new) for s in found], [("think", "I think that")])
        self.assertEqual(live.fixed("think we go", found), "I think that we go")

    def test_deletion(self):
        cache = cached(("it is is fine now", "it is fine now"))
        found = live.suggestions("it is is fine now", cache, set(), MODE)
        self.assertEqual([(s.old, s.new) for s in found], [("it is", "it")])
        s = found[0]
        self.assertEqual("it is is fine now"[:s.start] + s.new + "it is is fine now"[s.end:], "it is fine now")

    def test_each_suggestion_applies_cleanly_on_its_own(self):
        line = "i thinks we should of shiped it"
        cache = cached((line, "I think we should have shipped it"))
        for s in live.suggestions(line, cache, set(), MODE):
            self.assertEqual(live.text_at(line, s.start, s.end), s.old)

    def test_whitespace_around_the_line_is_kept_out(self):
        cache = cached(("i thinks so ok", "I think so ok"))
        found = live.suggestions("  i thinks so ok  ", cache, set(), MODE)
        self.assertEqual(found[0].start, 2)

    def test_unchanged_rewrite_has_no_suggestions(self):
        cache = cached(("All good here today", "All good here today"))
        self.assertEqual(live.suggestions("All good here today", cache, set(), MODE), [])

    def test_dismissed_keys_are_skipped(self):
        line = "i thinks we should of gone"
        cache = cached((line, "I think we should have gone"))
        found = live.suggestions(line, cache, set(), MODE)
        again = live.suggestions(line, cache, {found[0].key}, MODE)
        self.assertEqual([s.key for s in again], [s.key for s in found[1:]])

    def test_a_line_edited_after_caching_has_none(self):
        cache = cached(("i thinks so ok", "I think so ok"))
        self.assertEqual(live.suggestions("i thinks so ok then", cache, set(), MODE), [])

    def test_two_cached_lines(self):
        text = "i thinks so ok\nnothing here\nshe go home now"
        cache = cached(("i thinks so ok", "I think so ok"), ("she go home now", "she goes home now"))
        found = live.suggestions(text, cache, set(), MODE)
        self.assertEqual([(s.old, s.new) for s in found], [("i thinks", "I think"), ("go", "goes")])
        for s in found:
            self.assertEqual(live.text_at(text, s.start, s.end), s.old)

    def test_another_mode_does_not_reuse_the_result(self):
        cache = cached(("i thinks so ok", "I think so ok"))
        other = writer.BUILTIN_MODES[3]
        self.assertEqual(live.suggestions("i thinks so ok", cache, set(), other), [])

    def test_applying_one_keeps_the_others_without_a_new_check(self):
        line = "i thinks we should of gone"
        cache = cached((line, "I think we should have gone"))
        first, second = live.suggestions(line, cache, set(), MODE)
        edited = line[:first.start] + first.new + line[first.end:]
        live.applied(cache, MODE, first)
        self.assertTrue(cache.has(edited, MODE))
        left = live.suggestions(edited, cache, set(), MODE)
        self.assertEqual([(s.old, s.new) for s in left], [(second.old, second.new)])
        self.assertEqual(live.text_at(edited, left[0].start, left[0].end), second.old)

    def test_raw_mac_line_breaks(self):
        text = "first ok\r\ni thinks so ok\rlast"
        cache = cached(("i thinks so ok", "I think so ok"))
        found = live.suggestions(text, cache, set(), MODE)
        self.assertEqual(found[0].start, live.units("first ok\r\n"))
        self.assertEqual(live.text_at(text, found[0].start, found[0].end), "i thinks")
        self.assertEqual(live.paragraph_at(text, live.units(text)), (live.units(text) - 4, live.units(text)))

    def test_rtl_and_mixed_direction(self):
        line = "אני חושב שזה עובד ok 3"
        cache = cached((line, "אני חושב שזה עובד, ok 3"))
        found = live.suggestions(line, cache, set(), MODE)
        self.assertEqual([(s.old, s.new) for s in found], [("עובד", "עובד,")])
        self.assertEqual(live.text_at(line, found[0].start, found[0].end), "עובד")


class CacheTest(unittest.TestCase):
    def test_least_recently_used_is_evicted(self):
        cache = live.Cache(size=2)
        cache.put("a", MODE, "A")
        cache.put("b", MODE, "B")
        cache.get("a", MODE)
        cache.put("c", MODE, "C")
        self.assertEqual(cache.get("a", MODE), "A")
        self.assertIsNone(cache.get("b", MODE))
        self.assertTrue(cache.has("c", MODE))

    def test_custom_mode_instructions_are_part_of_the_key(self):
        cache = live.Cache()
        cache.put("a", writer.Mode("Mine", "one"), "A")
        self.assertIsNone(cache.get("a", writer.Mode("Mine", "two")))


if __name__ == "__main__":
    unittest.main()
