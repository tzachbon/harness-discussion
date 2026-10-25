"""Windows edit safety through public edit boundaries."""
import sys
import unittest
import ctypes
import subprocess

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness_discussion.win import clipboard

class ClipboardSafety(unittest.TestCase):
    def test_prepared_cleanup_relinquishes_original_after_concurrent_copy(self):
        native = Mock()
        native.GetClipboardSequenceNumber.return_value = 99
        with patch.object(clipboard, "u32", native), \
                patch.object(clipboard, "_allocate", return_value=[]), \
                patch.object(clipboard, "_install_locked") as install:
            with clipboard.PreparedRestore([(13, b"pre-test")]) as cleanup:
                self.assertIsNone(cleanup.apply(expected_sequence=12))
        install.assert_not_called()

    def test_returned_sequence_survives_close_and_synthesized_text_rendering(self):
        native = Mock()
        current = [10]
        native.CreateWindowExW.return_value = 123
        native.GetClipboardOwner.return_value = 123
        native.GetClipboardSequenceNumber.side_effect = lambda: current[0]
        native.IsClipboardFormatAvailable.return_value = True
        def render(fmt):
            current[0] += 1
            return 1
        native.GetClipboardData.side_effect = render
        native.CloseClipboard.side_effect = lambda: current.__setitem__(0, current[0] + 3)
        with patch.object(clipboard, "u32", native), \
                patch.object(clipboard, "_snapshot_locked", return_value=[]), \
                patch.object(clipboard, "_allocate", return_value=[]), \
                patch.object(clipboard, "_install_locked"):
            owned = clipboard.restore([], expected_sequence=10)
            self.assertEqual(owned, 17)
            self.assertEqual(clipboard.sequence(), owned)
        native.DestroyWindow.assert_not_called()

    def test_copy_by_another_owner_during_close_is_not_adopted(self):
        native = Mock()
        native.CreateWindowExW.return_value = 123
        native.GetClipboardSequenceNumber.return_value = 99
        native.GetClipboardOwner.return_value = 456
        with patch.object(clipboard, "u32", native), \
                patch.object(clipboard, "_snapshot_locked", return_value=[]), \
                patch.object(clipboard, "_allocate", return_value=[]), \
                patch.object(clipboard, "_install_locked"):
            self.assertIsNone(clipboard.restore([]))
            with clipboard.PreparedRestore([]) as cleanup:
                self.assertIsNone(cleanup.apply(expected_sequence=99))

    def test_prepared_cleanup_does_not_snapshot_unsupported_temporary_data(self):
        native = Mock()
        native.CreateWindowExW.return_value = 123
        native.GetClipboardOwner.return_value = 123
        native.GetClipboardSequenceNumber.return_value = 12
        with patch.object(clipboard, "u32", native), \
                patch.object(clipboard, "_allocate", return_value=[[13, 123]]), \
                patch.object(clipboard, "_install_locked") as install, \
                patch.object(clipboard, "_snapshot_locked", side_effect=OSError("temporary metafile")):
            with clipboard.PreparedRestore([(13, b"original")]) as cleanup:
                self.assertEqual(cleanup.apply(expected_sequence=12), 12)
        install.assert_called_once()

    def test_restore_rechecks_sequence_after_opening_before_clearing(self):
        native = Mock()
        native.GetClipboardSequenceNumber.return_value = 12
        native.OpenClipboard.return_value = 1
        native.CreateWindowExW.return_value = 123
        with patch.object(clipboard, "u32", native):
            self.assertIsNone(clipboard.restore([], expected_sequence=11))
        native.OpenClipboard.assert_called_once()
        native.EmptyClipboard.assert_not_called()

    def test_unsupported_or_unreadable_snapshot_refuses_before_clearing(self):
        native = Mock()
        native.EnumClipboardFormats.side_effect = [14, 0]
        with patch.object(clipboard, "u32", native):
            with self.assertRaises(OSError):
                clipboard.snapshot()
        native.GetClipboardData.assert_not_called()
        native.EmptyClipboard.assert_not_called()

    def test_allocation_failure_leaves_the_clipboard_untouched(self):
        native = Mock()
        native.GetClipboardSequenceNumber.return_value = 11
        native.EnumClipboardFormats.return_value = 0
        with patch.object(clipboard, "u32", native), patch.object(clipboard.k32, "GlobalAlloc", return_value=None):
            with self.assertRaises(OSError):
                clipboard.restore([(13, b"new\0")], expected_sequence=11)
        native.EmptyClipboard.assert_not_called()

    def test_complete_multi_format_snapshot_survives_a_failed_set(self):
        buffers, stored = {}, {}
        native, kernel = Mock(), Mock()
        sequence = [10]
        fail_once = [False]
        def allocate(flags, size):
            data = ctypes.create_string_buffer(size)
            address = ctypes.addressof(data)
            buffers[address] = data
            return address
        def enum(previous):
            return next((fmt for fmt in sorted(stored) if fmt > previous), 0)
        def empty():
            stored.clear()
            sequence[0] += 1
            return 1
        def put(fmt, address):
            if fail_once[0]:
                fail_once[0] = False
                return 0
            stored[fmt] = address
            sequence[0] += 1
            return address
        native.EnumClipboardFormats.side_effect = enum
        native.GetClipboardData.side_effect = lambda fmt: stored.get(fmt, 0)
        native.SetClipboardData.side_effect = put
        native.EmptyClipboard.side_effect = empty
        native.GetClipboardSequenceNumber.side_effect = lambda: sequence[0]
        kernel.GlobalAlloc.side_effect = allocate
        kernel.GlobalLock.side_effect = lambda address: address
        kernel.GlobalSize.side_effect = lambda address: len(buffers[address])
        kernel.GlobalFree.side_effect = lambda address: buffers.pop(address, None)
        for fmt, data in ((13, "sentinel\0".encode("utf-16-le")), (49353, b"html sentinel\0")):
            address = allocate(0, len(data))
            ctypes.memmove(address, data, len(data))
            stored[fmt] = address
        with patch.object(clipboard, "u32", native), patch.object(clipboard, "k32", kernel):
            original = clipboard.snapshot()
            self.assertEqual(dict(original), {13: "sentinel\0".encode("utf-16-le"), 49353: b"html sentinel\0"})
            fail_once[0] = True
            with self.assertRaises(OSError):
                clipboard.restore([(13, "replacement\0".encode("utf-16-le"))], expected_sequence=10)
            self.assertEqual(clipboard.snapshot(), original)


class InputSafety(unittest.TestCase):
    def test_held_keys_refuse_before_the_attempt_callback(self):
        from harness_discussion.win import input as keys
        for held in (0x11, 0x5B, ord("V")):
            with self.subTest(held=held):
                native, attempt = Mock(), Mock()
                native.GetAsyncKeyState.side_effect = lambda vk: 0x8000 if vk == held else 0
                with patch.object(keys, "u32", native):
                    self.assertFalse(keys.chord(ord("V"), on_attempt=attempt))
                attempt.assert_not_called()
                native.SendInput.assert_not_called()

    def test_attempt_callback_precedes_initial_delivery_and_never_repeats_for_cleanup(self):
        from harness_discussion.win import input as keys
        for outcome in (4, 0, 1, OSError("synthetic failure")):
            with self.subTest(outcome=outcome):
                native, attempt = Mock(), Mock()
                native.GetAsyncKeyState.return_value = 0
                results = iter((outcome, 2))
                def send(*args):
                    attempt.assert_called_once_with()
                    result = next(results)
                    if isinstance(result, Exception):
                        raise result
                    return result
                native.SendInput.side_effect = send
                with patch.object(keys, "u32", native):
                    self.assertIs(keys.chord(ord("V"), on_attempt=attempt), outcome == 4)
                attempt.assert_called_once_with()
                self.assertEqual(native.SendInput.call_count, 1 if outcome in (0, 4) else 2)

    def test_partial_paste_releases_synthetic_control_without_retry(self):
        from harness_discussion.win import input as keys
        native = Mock()
        native.GetAsyncKeyState.return_value = 0
        native.SendInput.side_effect = [1, 1]
        with patch.object(keys, "u32", native):
            self.assertFalse(keys.chord(ord("V")))
        first, cleanup = native.SendInput.call_args_list
        self.assertEqual(first.args[0], 4)
        self.assertEqual(cleanup.args[0], 1)
        self.assertEqual(cleanup.args[1][0].ki.wVk, 0x11)
        self.assertEqual(cleanup.args[1][0].ki.dwFlags, 2)

    def test_held_modifier_and_failed_cleanup_do_not_retry_the_action(self):
        from harness_discussion.win import input as keys
        native = Mock()
        native.GetAsyncKeyState.return_value = 0x8000
        with patch.object(keys, "u32", native):
            self.assertFalse(keys.chord(ord("V")))
        native.SendInput.assert_not_called()
        native.GetAsyncKeyState.return_value = 0
        native.SendInput.side_effect = [2, 0]
        with patch.object(keys, "u32", native):
            self.assertFalse(keys.chord(ord("V")))
        self.assertEqual(native.SendInput.call_count, 2)
        cleanup = native.SendInput.call_args.args[1]
        self.assertTrue(all(event.ki.dwFlags == 2 for event in cleanup))
