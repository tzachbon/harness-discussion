"""Exercise the Windows meeting window with real Tk and a temporary store."""
import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

import tempfile
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from harness_discussion import meeting_copy as copy, meetings
from harness_discussion.win.host import TkHost
from harness_discussion.win.notes_window import NotesWindow


class FakeHost:
    def __init__(self):
        self.copied = self.saved = self.revealed = self.message = None
        self.confirmed = True
    def copy_text(self, text):
        self.copied = text
        return True
    def notify(self, message):
        self.message = message
    def save_text(self, name, text):
        self.saved = name, text
        return True
    def reveal(self, path):
        self.revealed = path
    def confirm(self, *args, **kwargs):
        return self.confirmed


class FakeApp:
    def __init__(self):
        self.host = FakeHost()
        self.busy = set()
        self.retries = []
    def meeting_progress(self, meeting_id):
        return (2, 4)
    def meeting_busy(self, meeting_id):
        return meeting_id in self.busy
    def delete_meeting(self, meeting_id):
        if self.meeting_busy(meeting_id) or meetings.load(meeting_id).state != "done":
            return False
        meetings.delete(meeting_id)
        return True
    def redo_transcript(self, meeting_id):
        self.retries.append(("transcript", meeting_id))
    def redo_notes(self, meeting_id):
        self.retries.append(("notes", meeting_id))


class NotesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = patch.object(meetings, "DIR", Path(self.tmp.name))
        self.store.start()
        self.addCleanup(self.store.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = FakeApp()
        now = datetime.now().astimezone().replace(microsecond=0)
        self.first = meetings.new("Weekly sync", "Zoom", None, False, now=now - timedelta(hours=1))
        self.first.ended_at = (now - timedelta(minutes=29)).isoformat()
        self.first.state = "done"
        meetings.save(self.first)
        meetings.save_notes(self.first.id, "## Summary\nThe team agreed to ship Friday.\n\n## Decisions\n- Ship the beta.\n")
        meetings.add_part(self.first.id, meetings.Part("you", 0, 2, "Hi everyone, thanks for joining."))
        meetings.add_part(self.first.id, meetings.Part("them", 12, 15, "Let's start with the beta date."))
        self.second = meetings.new("Design review", "Teams", None, False, now=now)
        self.second.ended_at = (now + timedelta(minutes=4)).isoformat()
        self.second.state = "done"
        meetings.save(self.second)
        meetings.save_notes(self.second.id, "## Summary\nReviewed the design.")
        self.window = NotesWindow(self.root, self.app, self.first.id)
        self.root.update()

    def test_list_detail_transcript_copy_export_and_delete(self):
        w = self.window
        self.assertEqual(list(w.list.get_children()), [self.second.id, self.first.id])
        self.assertEqual(w.title.cget("text"), "Weekly sync")
        self.assertIn("The team agreed", w.notes.get("1.0", "end"))
        self.assertIn("00:00  You  Hi everyone", w.transcript.get("1.0", "end"))
        w.copy_notes()
        self.assertIn("## Summary", self.app.host.copied)
        self.assertNotIn("## Transcript", self.app.host.copied)
        self.assertEqual(w._copy_toast.label.cget("text"), copy.NOTES_COPIED)
        w.export()
        self.assertEqual(self.app.host.saved[0], copy.export_name(self.first))
        self.assertIn("## Transcript", self.app.host.saved[1])
        self.app.host.confirmed = False
        w.delete()
        self.assertTrue(self.first.folder.exists())
        self.app.host.confirmed = True
        w.delete()
        self.assertFalse(self.first.folder.exists())

    def test_recording_and_processing_cannot_be_deleted(self):
        self.first.state = "recording"
        meetings.save(self.first)
        self.window.refresh(self.first.id)
        self.assertTrue(self.window.delete_button.instate(["disabled"]))
        self.window.delete()
        self.assertTrue(self.first.folder.exists())
        self.first.state = "done"
        meetings.save(self.first)
        self.app.busy.add(self.first.id)
        self.window.refresh(self.first.id)
        self.assertTrue(self.window.delete_button.instate(["disabled"]))

    def test_meeting_escape_does_not_replace_writer_cancel(self):
        host = object.__new__(TkHost)
        host.app = self.app
        host.app.rewriting = False
        host._writer_esc = []
        writer_cancel = lambda: None
        host._cancel_writer = writer_cancel
        host.app.meeting_toast = object()
        with patch("harness_discussion.win.host.Escape"):
            host.esc_watcher(lambda: None)
        self.assertIs(host._cancel_writer, writer_cancel)

    def test_host_save_dialog_writes_markdown(self):
        host = object.__new__(TkHost)
        host.root = self.root
        path = Path(self.tmp.name) / "export.md"
        with patch("harness_discussion.win.host.filedialog.asksaveasfilename", return_value=str(path)):
            self.assertTrue(host.save_text("export.md", "# Notes\n"))
        self.assertEqual(path.read_text(encoding="utf-8"), "# Notes\n")


if __name__ == "__main__":
    unittest.main()
