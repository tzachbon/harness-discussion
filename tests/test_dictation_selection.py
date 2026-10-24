"""Mouse-selection work must not compete with a held dictation shortcut."""
import unittest
import threading
from unittest.mock import Mock, patch

from harness_discussion import core
from tests import test_core as fixtures


class DictationSelectionTest(unittest.TestCase):
    def make(self):
        return fixtures.CoreTest.make(self, fixtures.Engine("macOS"))

    def test_selection_does_not_start_accessibility_work_during_recording(self):
        app = self.make()
        app._holding = True
        with patch.object(core.threading, "Thread") as thread:
            app._selected(10, 20)
        thread.assert_not_called()

    def test_already_queued_selection_cannot_open_the_toolbar_during_recording(self):
        app = self.make()
        app._holding = True
        app._handle("show", "Selected.", 10, 20, False)
        self.assertFalse(app.popup.visible)

    def test_selection_worker_does_not_read_while_a_recording_is_starting(self):
        app = self.make()
        app._holding = True
        app.host.read_selection = Mock()
        with patch.object(core.time, "sleep"):
            app._read(app._seq, 10, 20)
        app.host.read_selection.assert_not_called()

    def test_selection_queued_before_an_entire_hold_stays_stale_after_it_ends(self):
        app = self.make()
        old_seq = app._seq
        app._seq += 1  # an accepted dictation down invalidates prior selection work
        app._holding = False
        app._handle("show", "Old selection.", 10, 20, False, old_seq)
        self.assertFalse(app.popup.visible)
        app.host.read_selection = Mock()
        with patch.object(core.time, "sleep"):
            app._read(old_seq, 10, 20)
        app.host.read_selection.assert_not_called()

    def test_selection_read_that_overlaps_a_whole_hold_cannot_publish(self):
        app = self.make()

        def read(_clipboard):
            app._seq += 1
            return "Old selection."

        app.host.read_selection = read
        app.writer_ready = app.cfg["writer"] = True
        app.edit = Mock()
        with patch.object(core.time, "sleep"):
            app._read(app._seq, 10, 20)
        app.edit.capture.assert_not_called()
        self.assertTrue(app.events.empty())

    def test_recording_cleanup_blocks_mouse_and_read_shortcut_work(self):
        app = self.make()
        app._recorder = object()
        with patch.object(core.threading, "Thread") as thread:
            app._selected(10, 20)
            app._hotkey()
        thread.assert_not_called()
        app._handle("show", "Selected.", 10, 20, False)
        self.assertFalse(app.popup.visible)

    def test_late_read_shortcut_result_cannot_start_playback(self):
        app = self.make()
        old_wait = app._hotkey_wait = object()
        app._holding = True
        app._hotkey_shown(old_wait, "Selected.", 10, 20)
        self.assertFalse(app.popup.visible)
        self.assertFalse(app.popup.playing)

    def test_native_mouse_callback_invalidates_target_before_ui_dispatch(self):
        app = self.make()
        app.refresh_statuses = Mock()
        app._dictation_target_activity = threading.Event()
        app.start()
        self.host.triggered[1](10, 20)
        self.assertTrue(app._dictation_target_activity.is_set())


if __name__ == "__main__":
    unittest.main()
