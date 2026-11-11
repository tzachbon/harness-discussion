"""Notes tests exercise source fallback without network access."""
import re
import unittest

import harness_discussion.meeting_notes as notes
from harness_discussion import writer

VALID = "## Summary\nSummary.\n## Decisions\n- None.\n## Action items\n- [ ] None."
MAP = "## Points\n- [00:00] Point.\n## Decisions\n- None.\n## Action items\n- None."


class Fake:
    def __init__(self, name="fake", short_prompt=False, reply=VALID, error=None):
        self.name, self.short_prompt, self.reply, self.error = name, short_prompt, reply, error
        self.calls = []

    def status(self):
        return "ready"

    def rewrite(self, system, user, model, effort, cancelled):
        self.calls.append((system, user))
        if self.error:
            raise self.error
        return MAP if "<transcript>" in user and "## Points" in system else self.reply


def lines(n):
    return [(i, "You", "privateword " * 80) for i in range(n)]


class NotesTest(unittest.TestCase):
    def test_single_pass_and_cloud_long_transcript(self):
        for data in (lines(1), [(0, "You", "x" * 30000)]):
            source = Fake()
            result, name = notes.make(data, [source])
            self.assertEqual((result, name), (VALID, "fake"))
            self.assertEqual(len(source.calls), 1)

    def test_local_map_reduce_and_about_budget(self):
        source = Fake(short_prompt=True)
        data = lines(30)
        about = "a" * 400
        text = notes.transcript_text(data)
        parts = notes.split(text, notes.LOCAL_CHARS - len(about))
        notes.make(data, [source], about=about)
        self.assertGreater(len(parts), 1)
        self.assertEqual(len(source.calls), len(parts) + 1)
        for system, user in source.calls:
            inner = re.sub(r"^<(transcript|notes)>\s*|\s*</(transcript|notes)>$", "", user)
            self.assertLessEqual(len(inner), notes.LOCAL_CHARS - 400)
            self.assertLess(len(system) - len(about), 2000)

    def test_fallback_exception_and_invalid_reply_with_safe_logs(self):
        for first in (Fake("bad", error=ValueError("privateword")), Fake("bad", reply="invalid")):
            second = Fake("good")
            with self.assertLogs("harness_discussion", level="INFO") as logs:
                result = notes.make(lines(1), [first, second], title="Secret title", event_title="https://secret.invalid")
            self.assertEqual(result, (VALID, "good"))
            for secret in ("privateword", "Secret title", "https://secret.invalid"):
                self.assertNotIn(secret, " ".join(logs.output))

    def test_cancelled_propagates(self):
        with self.assertRaises(writer.Cancelled):
            notes.make(lines(1), [Fake(error=writer.Cancelled())])

    def test_clean_thinking_and_preamble(self):
        source = Fake(reply="<think>private reasoning</think>\nHere are the notes:\n" + VALID)
        self.assertEqual(notes.make(lines(1), [source])[0], VALID)

    def test_split_limit_and_line_boundaries(self):
        text = "first line\nsecond line\n" + "word " * 20
        chunks = notes.split(text, 24)
        self.assertTrue(all(len(chunk) <= 24 for chunk in chunks))
        self.assertTrue(any("first line" in chunk for chunk in chunks))
        self.assertTrue(any("second line" in chunk for chunk in chunks))
        self.assertEqual(" ".join(" ".join(chunks).split()), " ".join(text.split()))

    def test_all_prompt_variants_under_2000(self):
        source = Fake(short_prompt=True)
        notes.make(lines(30), [source])
        self.assertGreater(len(source.calls), 1)
        self.assertTrue(all(len(system) < 2000 for system, _ in source.calls))
