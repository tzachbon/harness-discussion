import unittest

from harness_discussion import learning, live
from harness_discussion.edit_logic import Field


def field(text, caret=None):
    return Field(1, 2, "fixture", "Fixture", text, caret or (live.units(text),) * 2, b"digest")


class Learning(unittest.TestCase):
    def test_learns_names_joined_words_and_unicode(self):
        self.assertEqual(learning.corrections("Ask Zach about it", "Ask Tzach about it"), ["Tzach"])
        self.assertEqual(learning.corrections("Use open whisper today", "Use OpenWhispr today"), ["OpenWhispr"])
        self.assertEqual(learning.corrections("Ask Jonatan today", "Ask Yonatan today"), ["Yonatan"])
        self.assertEqual(learning.corrections("פגשתי יונתן אתמול", "פגשתי יהונתן אתמול"), ["יהונתן"])

    def test_skips_content_edits_symbols_case_punctuation_and_insertions(self):
        for old, new in [("why is this here", "what is this here"), ("hello there", "Hello there!"),
                         ("Feed the cat", "Feed the cats"), ("Walk down the street", "Walk down the stream"),
                         ("We start working", "We start walking"),
                         ("We study together", "We studied together"),
                         ("They carry books", "They carried books"),
                         ("Ask Zach", "Ask Zach tomorrow"), ("Use /tmp/old", "Use /tmp/new"),
                         ("mail a@old.com", "mail a@new.com"), ("use abc_123", "use abc_124"),
                         ("Ask Zach", "Remember everything completely differently")]:
            with self.subTest(old=old, new=new):
                self.assertEqual(learning.corrections(old, new), [])

    def test_normalization_and_hint_budget(self):
        self.assertEqual(learning.normalize([" Tzach ", "tzach", 1, "a@b.com", "hello\nworld"]), ["tzach"])
        self.assertEqual(len(learning.normalize(["Word" + chr(65 + n // 26) + chr(65 + n % 26)
                                               for n in range(220)])), 200)
        hint = learning.hint(["Vocabulary" + chr(0x400 + n) for n in range(200)])
        self.assertLessEqual(len(hint), 1200)
        self.assertIn("Vocabulary" + chr(0x400 + 199), hint)

    def test_watch_requires_exact_insertion_and_anchors(self):
        before = field("👋 left  right", (8, 8))
        watch = learning.Watch(object(), before, "Ask Zach today", 0)
        self.assertEqual(watch.poll(field("👋 left Ask Zach today right"), 0.1), [])
        # The text alone is insufficient: the caret must confirm where the paste landed.
        self.assertTrue(watch.closed)
        before = field("👋 left  right", (8, 8))
        watch = learning.Watch(object(), before, "Ask Zach today", 0)
        after = field("👋 left Ask Zach today right", (22, 22))
        self.assertEqual(watch.poll(after, 0.1), [])
        self.assertTrue(watch.confirmed)
        changed = field("👋 left Ask Tzach today right", (18, 18))
        self.assertEqual(watch.poll(changed, 1), [])
        self.assertEqual(watch.poll(changed, 2.6), ["Tzach"])
        self.assertEqual(watch.poll(changed, 4), [])
        watch.poll(field("👋 LEFT Ask Tzach today right", (18, 18)), 5)
        self.assertTrue(watch.closed)

    def test_missing_field_timeout_and_unconfirmed_paste_end_watch(self):
        before = field("")
        for later, now in [(None, 0.1), (field("unexpected"), 0.1), (field("Ask Zach today"), 31)]:
            watch = learning.Watch(object(), before, "Ask Zach today", 0)
            self.assertEqual(watch.poll(later, now), [])
            self.assertTrue(watch.closed)


if __name__ == "__main__":
    unittest.main()
