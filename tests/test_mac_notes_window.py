import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from harness_discussion import meeting_copy, meetings


@unittest.skipUnless(sys.platform == 'darwin', 'AppKit requires macOS')
class NotesWindowTest(unittest.TestCase):
    def setUp(self):
        import AppKit
        from harness_discussion.mac.notes_window import NotesWindow
        AppKit.NSApplication.sharedApplication()
        self.tmp = tempfile.TemporaryDirectory()
        self.old_dir = meetings.DIR
        meetings.DIR = Path(self.tmp.name)
        for ident, state, start in [('done', 'done', '2026-11-10T10:00:00+00:00'),
                                    ('recording', 'recording', '2026-11-11T10:00:00+00:00')]:
            folder = meetings.DIR / ident
            folder.mkdir()
            meetings.save(meetings.Meeting(ident, ident.title(), 'Zoom', start,
                                           '2026-11-10T10:30:00+00:00' if ident == 'done' else None, state))
        meetings.save_notes('done', '## Summary\nFixture notes')
        shutil.copy(Path(__file__).parent / 'fixtures/meetings/transcript.jsonl', meetings.DIR / 'done/transcript.jsonl')
        self.host = SimpleNamespace(save_text=Mock(), copy_text=Mock(), notify=Mock(), reveal=Mock(), confirm=Mock())
        self.app = SimpleNamespace(host=self.host, meeting_busy=lambda ident: ident == 'recording', meeting_progress=lambda _: (0, 0))
        self.window = NotesWindow(self.app, 'done')

    def tearDown(self):
        self.window.timer.invalidate()
        self.window.win.close()
        meetings.DIR = self.old_dir
        self.tmp.cleanup()

    def test_selection_transcript_and_export(self):
        self.assertEqual([m.id for m in self.window.items], ['recording', 'done'])
        self.assertEqual(self.window.title.stringValue(), 'Done')
        self.assertIn('Fixture notes', self.window.text.string())
        self.window.tabs.setSelectedSegment_(1)
        self.window._tab()
        self.assertTrue(self.window.text.string().startswith('00:00  You'))
        self.window._export()
        name, content = self.host.save_text.call_args.args
        self.assertEqual(name, meeting_copy.export_name(self.window.meeting))
        self.assertIn('## Transcript', content)
        self.window.select('recording')
        self.assertFalse(self.window.delete_button.isEnabled())
