"""Windows edit safety through public edit boundaries."""
import sys
import unittest
import ctypes
import subprocess
import threading

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.win_edit_fakes import UNSUPPORTED, Document, Range, clipboard, native_host, target

class CaptureSafety(unittest.TestCase):
    def test_dictation_activity_during_last_inspection_prevents_any_paste(self):
        from harness_discussion.win import edit
        document, activity = Document(), threading.Event()
        with target(document) as (_, key, _, cleanup):
            context = edit.capture(False)
            inspect, reads = edit._inspect, []

            def changed_after_read():
                found = inspect()
                reads.append(1)
                if len(reads) == 2:
                    document.start, document.end = 0, 4
                    activity.set()
                return found

            with patch.object(edit, "_inspect", side_effect=changed_after_read):
                self.assertFalse(edit.type_at_caret("new", context, cancelled=activity.is_set))
            self.assertEqual(len(reads), 2)
            self.assertEqual(document.text, "Left old right")
            key.assert_not_called()
            cleanup.assert_called_once_with(expected_sequence=11)

    def test_shared_undo_keeps_receipts_across_mode_and_repeated_review_replacements(self):
        from harness_discussion import edit_logic
        from harness_discussion.win import edit
        document = Document()
        with target(document) as (_, key, _, _):
            context = edit.capture(False)

            def paste(vk):
                data = dict(edit.clipboard.restore.call_args.args[0])[13]
                text = data.decode("utf-16-le")[:-1]
                document.text = document.text[:document.start] + text + document.text[document.end:]
                document.start += len(text)
                document.end = document.start
                return True

            key.side_effect = paste
            inserted = "A😀\nB"
            self.assertTrue(edit.replace(inserted, context))
            for revised in ("second😀\nline", "review replacement\nfour", "review again"):
                self.assertTrue(edit_logic.undo(edit, context, revised, inserted, edit.NEWLINE))
                self.assertEqual(document.text, "Left " + revised.replace("\n", "\r\n") + " right")
                inserted = revised
            self.assertTrue(edit_logic.undo(edit, context, "old", inserted, edit.NEWLINE))
            self.assertEqual(document.text, "Left old right")


    def test_dictation_replaces_only_the_unchanged_original_selection(self):
        from harness_discussion.win import edit
        document = Document()
        with target(document) as (_, key, _, _):
            context = edit.capture(False)
            key.side_effect = lambda vk: (setattr(document, "text", "Left new right"),
                                         setattr(document, "start", 8), setattr(document, "end", 8), True)[-1]
            self.assertTrue(edit.type_at_caret("new", context))
        document = Document()
        with target(document) as (_, key, snap, _):
            context = edit.capture(False)
            document.start = document.end = 8
            self.assertFalse(edit.type_at_caret("new", context))
            key.assert_not_called()
            snap.assert_not_called()


    def test_worker_shutdown_does_not_finalize_unreachable_tk_objects(self):
        code = '''
import gc
import tkinter as tk
from harness_discussion.win import uia_thread
gc.disable()
uia_thread.run(lambda: None)
root = tk.Tk()
root.withdraw()
variable = tk.StringVar(root, value="synthetic")
variable.cycle = variable
root.destroy()
del root, variable
uia_thread.close()
gc.collect()  # Tk objects must be finalized on their owning main thread.
print("clean shutdown")
'''
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "clean shutdown")


    def test_undo_refuses_identity_change_during_selection(self):
        from harness_discussion.win import edit
        document = Document()
        with target(document) as (_, key, _, _):
            context = edit.capture(False)
            def paste(vk):
                document.text = "Left new right"
                document.start = document.end = 8
                return True
            key.side_effect = paste
            self.assertTrue(edit.replace("new", context))
            def switched(span, waitTime=0):
                document.start, document.end = span.start, span.end
                document.identity = [99, 100]
                return True
            with patch.object(Range, "Select", switched):
                self.assertFalse(edit.select_back(3, context))


    def test_installed_text_range_wrapper_receives_native_compare_target(self):
        from harness_discussion.win import edit
        class NativeRange(Range):
            def Clone(self):
                return NativeRange(self.document, self.start, self.end)

            def MoveEndpointByRange(self, endpoint, other, other_endpoint):
                super().MoveEndpointByRange(endpoint, other, other_endpoint)
                return 0  # S_OK, the real wrapper converts this to bool

            def CompareEndpoints(self, endpoint, other, other_endpoint):
                if not isinstance(other, NativeRange):
                    raise TypeError("COM requires a native range")
                return super().CompareEndpoints(endpoint, other, other_endpoint)

        class NativeDocument(Document):
            @property
            def DocumentRange(self):
                return edit.uia.TextRange(NativeRange(self, 0, len(self.text)))

            def GetSelection(self):
                return [edit.uia.TextRange(NativeRange(self, self.start, self.end))]

        with target(NativeDocument()):
            context = edit.capture(False)
            self.assertIsNotNone(context)
            self.assertEqual(context.text, "old")


    def test_clipboard_changed_during_revalidation_never_gets_pasted(self):
        from harness_discussion.win import edit
        document = Document()
        with target(document) as (_, key, _, _):
            context = edit.capture(False)
            with patch.object(clipboard, "sequence", return_value=99):
                self.assertFalse(edit.replace("new", context))
            key.assert_not_called()


    def test_worker_keeps_its_mta_apartment_between_operations(self):
        code = '''
from unittest.mock import patch
from harness_discussion.win import edit, selection, uia_thread
with patch.object(edit.u32, 'GetForegroundWindow', return_value=0), \\
     patch.object(uia_thread.comtypes, 'CoInitializeEx') as initialize, \\
     patch.object(uia_thread.comtypes, 'CoUninitialize') as uninitialize, \\
     patch.object(uia_thread.ctypes.windll.user32, 'PeekMessageW', return_value=0) as pump, \\
     patch.object(selection.uia, 'GetFocusedControl', return_value=None):
    edit.capture(False)
    selection.read(False)
    edit.capture(False)
    uia_thread.close()
    print(initialize.call_count, uninitialize.call_count, pump.called)
'''
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "1 1 True")


    def test_replace_and_undo_refuse_a_secure_context_without_input(self):
        from harness_discussion.win import edit
        context = edit.Context("", 9, 42, False, True)
        with patch.object(edit.keys, "chord") as key, patch.object(edit.clipboard, "snapshot_with_sequence") as clip:
            self.assertFalse(edit.replace("sample", context))
            self.assertFalse(edit.type_at_caret("sample", context))
            self.assertFalse(edit.select_back(6, context))
        key.assert_not_called()
        clip.assert_not_called()


    def test_capture_preserves_whitespace_and_detects_field_range_and_document_changes(self):
        from harness_discussion.win import edit
        document = Document("  old  old  ", 2, 7)
        with target(document):
            context = edit.capture(False)
            self.assertEqual(context.text, "old  ")
            self.assertTrue(edit.same_context(context))
            document.start, document.end = 7, 12
            self.assertFalse(edit.same_context(context))
            document.start, document.end = 2, 7
            document.identity = [1, 3]
            self.assertFalse(edit.same_context(context))
            document.identity = [1, 2]
            document.text = "X old  old  "
            self.assertFalse(edit.same_context(context))


    def test_read_only_and_moved_caret_targets_never_receive_input(self):
        from harness_discussion.win import edit
        document = Document()
        with target(document) as (_, key, snap, _):
            context = edit.capture(False)
            document.writable = False
            self.assertFalse(edit.replace("new", context))
            key.assert_not_called()
            snap.assert_not_called()
        document = Document("abc", 1, 1)
        with target(document) as (_, key, snap, _):
            context = edit.capture(False)
            document.start = document.end = 2
            self.assertFalse(edit.type_at_caret("new", context))
            key.assert_not_called()
            snap.assert_not_called()


    def test_exact_replacement_records_safe_undo_then_fresh_context_restores_original(self):
        from harness_discussion.win import edit
        document = Document()
        with target(document) as (_, key, _, restore):
            context = edit.capture(False)
            def paste(vk):
                document.text = "Left new right"
                document.start = document.end = 8
                return True
            key.side_effect = paste
            self.assertTrue(edit.replace("new", context))
            self.assertEqual(document.text, "Left new right")
            self.assertEqual(restore.call_args.kwargs, {"expected_sequence": 11})
            self.assertTrue(edit.select_back(3, context))
            selected = edit.capture(False)
            self.assertEqual(selected.text, "new")
            restore.return_value = 14
            def undo(vk):
                document.text = "Left old right"
                document.start = document.end = 8
                return True
            key.side_effect = undo
            self.assertTrue(edit.replace("old", selected))
            self.assertEqual(document.text, "Left old right")


    def test_insertion_without_deletion_is_not_reported_as_replacement(self):
        from harness_discussion.win import edit
        document = Document()
        with target(document) as (_, key, _, _):
            context = edit.capture(False)
            def insert_only(vk):
                document.text = "Left newold right"
                document.start = document.end = 8
                return True
            key.side_effect = insert_only
            self.assertFalse(edit.replace("new", context))
            key.assert_called_once_with(ord("V"))
            self.assertFalse(edit.select_back(3, context))


    def test_undo_measures_emoji_and_linebreaks_without_guessing_navigation_steps(self):
        from harness_discussion.win import edit
        inserted = "A😀\r\nB"
        self.assertEqual(edit.utf16_length(inserted), 6)
        document = Document()
        with target(document) as (_, key, _, _):
            context = edit.capture(False)
            def paste(vk):
                document.text = "Left A😀\r\nB right"
                document.start = document.end = 10
                return True
            key.side_effect = paste
            self.assertTrue(edit.replace(inserted, context))
            self.assertTrue(edit.select_back(6, context))
            self.assertEqual(edit.capture(False).text, inserted)
