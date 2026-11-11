"""Store contract tests use only temporary meeting directories."""
import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import harness_discussion.meetings as meetings

FIXTURES = Path(__file__).parent / "fixtures" / "meetings"


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.patcher = patch.object(meetings, "DIR", self.directory)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def new(self, title="Secret title", now=None):
        return meetings.new(title, "Zoom", None, False, now=now or datetime(2026, 11, 11, 10, 2, 13, tzinfo=timezone.utc))

    def test_new_id_and_round_trip(self):
        m = self.new()
        self.assertRegex(m.id, r"^2026-11-11T10-02-13-[0-9a-f]{4}$")
        self.assertNotIn(m.title, m.id)
        m.error = "safe"
        meetings.save(m)
        self.assertEqual(meetings.load(m.id), m)

    def test_all_newest_first_skips_broken_json(self):
        old = self.new(now=datetime(2026, 11, 10, tzinfo=timezone.utc))
        new = self.new(now=datetime(2026, 11, 11, tzinfo=timezone.utc))
        broken = self.directory / "broken"
        broken.mkdir()
        (broken / "meeting.json").write_text("{", encoding="utf-8")
        self.assertEqual([m.id for m in meetings.all()], [new.id, old.id])

    def test_delete_refuses_parent_traversal(self):
        with self.assertRaises(meetings.Error):
            meetings.delete("../outside")

    def test_parts_ignores_truncated_line_and_keeps_last(self):
        m = self.new()
        meetings.add_part(m.id, meetings.Part("you", 0, 2, "old"))
        replacement = meetings.Part("you", 0, 2, "new")
        meetings.add_part(m.id, replacement)
        with (m.folder / "transcript.jsonl").open("a", encoding="utf-8") as f:
            f.write('{"track":')
        self.assertEqual(meetings.parts(m.id), [replacement])

    def test_transcribed_until_and_unfinished(self):
        m = self.new()
        meetings.add_part(m.id, meetings.Part("you", 0, 2, "hello"))
        meetings.add_part(m.id, meetings.Part("them", 0, 3, "hello"))
        self.assertEqual(meetings.transcribed_until(m.id, "you"), 2)
        self.assertEqual(meetings.transcribed_until(m.id, "them"), 3)
        self.assertEqual(meetings.transcribed_until(m.id, "missing"), 0)
        for state in meetings.STATES:
            m.state = state
            meetings.save(m)
            self.assertEqual(m.id in [item.id for item in meetings.unfinished()],
                             state in ("recording", "interrupted", "transcribing", "noting"))

    def test_remove_echo_fixture_and_lines(self):
        raw = [meetings.Part(**json.loads(line)) for line in
               (FIXTURES / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
        cleaned = meetings.remove_echo(raw)
        self.assertEqual(len(cleaned), 4)
        texts = [p.text for p in cleaned]
        self.assertIn("Hi everyone, thanks for joining.", texts)
        self.assertIn("Thanks. Let's start with the beta date.", texts)
        self.assertIn("I think Friday works if QA signs off.", texts)
        self.assertIn("Agreed. Dana will send the release note by Thursday.", texts)
        lines = meetings.lines(raw)
        self.assertEqual([line[0] for line in lines], sorted(line[0] for line in lines))
        self.assertEqual([line[1] for line in lines], ["You", "Them", "You", "Them"])

    def test_markdown_exact_export(self):
        m = meetings.Meeting("id", "Weekly sync", "Zoom", "2026-11-11T10:02:13+02:00",
                             "2026-11-11T10:33:40+02:00", "done",
                             event={"title": "Weekly sync", "start": "2026-11-11T10:00:00+02:00",
                                    "end": "2026-11-11T10:30:00+02:00", "source": "ics"})
        notes = "## Summary\n...\n\n## Decisions\n- ...\n\n## Action items\n- [ ] Dana: send the release note by Thursday."
        lines = [(0, "You", "Hi everyone, thanks for joining."),
                 (12, "Them", "Thanks. Let's start with the beta date.")]
        expected = """# Weekly sync

- Date: 2026-11-11 10:02–10:33 (31 min)
- Calendar: Weekly sync, 10:00–10:30
- App: Zoom

## Summary
...

## Decisions
- ...

## Action items
- [ ] Dana: send the release note by Thursday.

## Transcript
**00:00 You:** Hi everyone, thanks for joining.
**00:12 Them:** Thanks. Let's start with the beta date.
"""
        self.assertEqual(meetings.markdown(m, notes, lines), expected)
