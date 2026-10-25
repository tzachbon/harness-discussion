"""Windows edit safety through public clipboard and edit boundaries, without user input."""
import sys
import unittest
import ctypes
import subprocess

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness_discussion.win import clipboard

UNSUPPORTED = object()


@contextmanager
def native_host(window):
    from harness_discussion.win import selection
    client = SimpleNamespace(IUIAutomation=SimpleNamespace(CheckNotSupported=lambda value: value is UNSUPPORTED))
    native_class = lambda hwnd, buf, size: setattr(buf, "value", "Chrome_RenderWidgetHostHWND") or 27
    with patch.object(selection.uia, "GetForegroundWindow", return_value=42), \
            patch.object(selection.uia, "GetAncestor", return_value=42), \
            patch.object(selection.uia, "ControlFromHandle", return_value=window), \
            patch.object(selection.uia, "ControlsAreSame", side_effect=lambda left, right: left is right), \
            patch.object(selection.u32, "GetClassNameW", side_effect=native_class), \
            patch.object(selection.automation._AutomationClient, "instance", return_value=client):
        yield


class Document:
    """A deterministic UIA provider, including independent selection and document state."""
    def __init__(self, text="Left old right", start=5, end=8):
        self.text, self.start, self.end = text, start, end
        self.IsPassword, self.writable, self.identity = False, True, [1, 2]
        self.Element = SimpleNamespace(GetCurrentPropertyValueEx=lambda prop, suppress: self.IsPassword)

    def GetParentControl(self):
        return None

    def GetRuntimeId(self):
        return self.identity

    def GetPattern(self, pattern):
        from harness_discussion.win import edit
        if pattern == edit.uia.PatternId.TextPattern:
            return self
        if pattern == edit.uia.PatternId.ValuePattern:
            return SimpleNamespace(IsReadOnly=not self.writable)

    @property
    def DocumentRange(self):
        return Range(self, 0, len(self.text))

    def GetSelection(self):
        return [Range(self, self.start, self.end)]


class Range:
    def __init__(self, document, start, end):
        self.document, self.start, self.end = document, start, end

    def GetText(self, maximum):
        text = self.document.text[self.start:self.end]
        return text if maximum < 0 else text[:maximum]

    def Clone(self):
        return Range(self.document, self.start, self.end)

    @property
    def textRange(self):
        return self

    def GetChildren(self):
        return []

    def CompareEndpoints(self, endpoint, other, other_endpoint):
        return (self.start if endpoint == 0 else self.end) - (other.start if other_endpoint == 0 else other.end)

    def MoveEndpointByRange(self, endpoint, other, other_endpoint, waitTime=0):
        value = other.start if other_endpoint == 0 else other.end
        if endpoint == 0:
            self.start = value
        else:
            self.end = value
        return True

    def MoveEndpointByUnit(self, endpoint, unit, count, waitTime=0):
        if endpoint != 0 or count != -1 or self.start == 0:
            return 0
        self.start -= 1
        return -1

    def Select(self, waitTime=0):
        self.document.start, self.document.end = self.start, self.end
        return True


@contextmanager
def target(document):
    from harness_discussion.win import edit
    native = Mock()
    native.GetForegroundWindow.return_value = 42
    native.GetClassNameW.side_effect = lambda hwnd, buf, n: setattr(buf, "value", "Notepad") or 7
    def pid(hwnd, out):
        out._obj.value = 9
        return 1
    native.GetWindowThreadProcessId.side_effect = pid
    with ExitStack() as stack:
        stack.enter_context(patch.object(edit, "u32", native))
        stack.enter_context(patch.object(edit.uia, "GetFocusedControl", return_value=document))
        snap = stack.enter_context(patch.object(edit.clipboard, "snapshot_with_sequence", return_value=([], 10)))
        restore = stack.enter_context(patch.object(edit.clipboard, "restore", return_value=11))
        cleanup = stack.enter_context(patch.object(edit.clipboard, "PreparedRestore")).return_value.__enter__.return_value
        cleanup.apply.return_value = 12
        stack.enter_context(patch.object(edit.clipboard, "sequence", return_value=11))
        key = stack.enter_context(patch.object(edit.keys, "chord", return_value=False))
        yield native, key, snap, cleanup.apply


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
        native.GetClipboardSequenceNumber.side_effect = lambda: current[0]
        native.IsClipboardFormatAvailable.return_value = True
        def render(fmt):
            current[0] += 1
            return 1
        native.GetClipboardData.side_effect = render
        native.DestroyWindow.side_effect = lambda hwnd: current.__setitem__(0, current[0] + 1)
        with patch.object(clipboard, "u32", native), \
                patch.object(clipboard, "_snapshot_locked", return_value=[]), \
                patch.object(clipboard, "_allocate", return_value=[]), \
                patch.object(clipboard, "_install_locked"):
            owned = clipboard.restore([], expected_sequence=10)
            self.assertEqual(owned, 14)
            self.assertEqual(clipboard.sequence(), owned)
        native.DestroyWindow.assert_not_called()

    def test_prepared_cleanup_does_not_snapshot_unsupported_temporary_data(self):
        native = Mock()
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


class CaptureSafety(unittest.TestCase):
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

    def test_blind_reader_never_copies_when_user_copy_can_precede_first_sample(self):
        from harness_discussion.win import selection
        document = Document()
        document.GetPattern = Mock(return_value=None)
        current = [10]
        def two_copies(vk):
            current[0] = 12  # synthetic 11 and user 12 both finish before first observation
            return True
        with target(document) as (native, key, snap, cleanup):
            key.side_effect = two_copies
            with patch.object(ctypes.windll, "user32", native), \
                    patch.object(clipboard, "u32", native), \
                    patch.object(clipboard, "sequence", side_effect=lambda: current[0]), \
                    patch.object(clipboard, "_text_locked", return_value="newer user copy"):
                self.assertEqual(selection.read(True), "")
            key.assert_not_called()
            snap.assert_not_called()
            cleanup.assert_not_called()

    def test_blind_capture_stays_noneditable_without_copy_for_both_settings(self):
        from harness_discussion.win import edit, selection
        document = Document()
        document.GetPattern = Mock(return_value=None)
        with target(document) as (_, key, snap, cleanup):
            for enabled in (False, True):
                context = edit.capture(enabled)
                self.assertIsNotNone(context)
                self.assertEqual(context.text, "")
                self.assertFalse(context.editable)
                self.assertEqual(selection.read(enabled), "")
                self.assertFalse(edit.replace("new", context))
                self.assertFalse(edit.type_at_caret("new", context))
            key.assert_not_called()
            snap.assert_not_called()
            cleanup.assert_not_called()

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

    def test_empty_text_non_degenerate_range_and_embedded_objects_refuse_input(self):
        from harness_discussion.win import edit
        document = Document("abc", 1, 1)
        with target(document) as (_, key, snap, _):
            with patch.object(Range, "CompareEndpoints", return_value=-1):
                self.assertIsNone(edit.capture(False))
                self.assertFalse(edit.type_at_caret("new"))
            with patch.object(Range, "GetChildren", return_value=[object()]):
                self.assertIsNone(edit.capture(False))
            key.assert_not_called()
            snap.assert_not_called()

    def test_unsupported_security_property_refuses_capture_and_reader_copy(self):
        from harness_discussion.win import edit, selection
        document = Document()
        document.Element.GetCurrentPropertyValueEx = Mock(return_value=object())
        document.GetPattern = Mock(wraps=document.GetPattern)
        with target(document) as (_, key, snap, _):
            self.assertIsNone(edit.capture())
            self.assertEqual(selection.read(), "")
            key.assert_not_called()
            snap.assert_not_called()
        document.GetPattern.assert_not_called()

    def test_chromium_window_default_does_not_hide_safe_selected_text(self):
        from harness_discussion.win import edit, selection
        for kind in (selection.uia.ControlType.WindowControl, selection.uia.ControlType.PaneControl):
            with self.subTest(kind=kind):
                document = Document()
                password = Mock(return_value=UNSUPPORTED)
                window = SimpleNamespace(ControlType=kind, NativeWindowHandle=42,
                                         Element=SimpleNamespace(GetCurrentPropertyValueEx=password),
                                         GetParentControl=lambda: None, GetPattern=Mock(return_value=None))
                document.GetParentControl = lambda: window
                with target(document) as (_, key, snap, _), native_host(window):
                    self.assertEqual(selection.read(False), "old")
                    context = edit.capture(False)
                    self.assertIsNotNone(context)
                    self.assertEqual(context.text, "old")
                    key.assert_not_called()
                    snap.assert_not_called()
                window.GetPattern.assert_not_called()

    def test_unknown_inner_container_still_blocks_every_text_read(self):
        from harness_discussion.win import edit, selection
        document = Document()
        window = Mock(ControlType=selection.uia.ControlType.PaneControl, NativeWindowHandle=43)
        window.Element.GetCurrentPropertyValueEx.return_value = UNSUPPORTED
        document.GetParentControl = lambda: window
        document.GetPattern = Mock(wraps=document.GetPattern)
        with target(document), native_host(window):
            self.assertEqual(selection.read(False), "")
            self.assertIsNone(edit.capture(False))
        document.GetPattern.assert_not_called()

    def test_default_password_metadata_is_only_used_for_window_ancestors(self):
        from harness_discussion.win import edit, selection
        for kind, ancestor, value in (
                (selection.uia.ControlType.DocumentControl, True, UNSUPPORTED),
                (selection.uia.ControlType.EditControl, True, UNSUPPORTED),
                (selection.uia.ControlType.TextControl, True, UNSUPPORTED),
                (selection.uia.ControlType.WindowControl, False, UNSUPPORTED),
                (selection.uia.ControlType.WindowControl, True, True),
                (selection.uia.ControlType.WindowControl, True, object())):
            with self.subTest(kind=kind, ancestor=ancestor, value=value):
                document = Document()
                window = Mock(ControlType=kind, NativeWindowHandle=42)
                window.Element.GetCurrentPropertyValueEx.return_value = value
                window.GetParentControl.return_value = None
                document.GetParentControl = lambda: window
                focused = document if ancestor else window
                document.GetPattern = Mock(wraps=document.GetPattern)
                with target(focused), native_host(window):
                    self.assertEqual(selection.read(False), "")
                    captured = edit.capture(False)
                    if value is True:
                        self.assertIsInstance(captured, edit.Context)
                        self.assertTrue(captured.secure)
                        self.assertEqual(captured.text, "")
                    else:
                        self.assertIsNone(captured)
                document.GetPattern.assert_not_called()
                window.GetPattern.assert_not_called()

    def test_host_boundary_requires_exact_native_identity(self):
        from harness_discussion.win import edit, selection
        document = Document()
        window = Mock(ControlType=selection.uia.ControlType.WindowControl, NativeWindowHandle=42)
        window.Element.GetCurrentPropertyValueEx.return_value = UNSUPPORTED
        document.GetParentControl = lambda: window
        document.GetPattern = Mock(wraps=document.GetPattern)
        with target(document), native_host(window), \
                patch.object(selection.uia, "ControlFromHandle", return_value=object()):
            self.assertEqual(selection.read(False), "")
            self.assertIsNone(edit.capture(False))
        document.GetPattern.assert_not_called()

    def test_native_chromium_renderer_is_skipped_but_outer_password_is_checked(self):
        from harness_discussion.win import edit, selection
        for secure in (False, True):
            with self.subTest(secure=secure):
                document = Document()
                window = Mock(ControlType=selection.uia.ControlType.WindowControl, NativeWindowHandle=42)
                window.Element.GetCurrentPropertyValueEx.return_value = True if secure else UNSUPPORTED
                window.GetParentControl.return_value = None
                renderer = Mock(ControlType=selection.uia.ControlType.PaneControl, NativeWindowHandle=43,
                                ClassName="Chrome_RenderWidgetHostHWND")
                renderer.Element.GetCurrentPropertyValueEx.return_value = UNSUPPORTED
                renderer.GetParentControl.return_value = window
                document.GetParentControl = lambda: renderer
                document.GetPattern = Mock(wraps=document.GetPattern)
                with target(document), native_host(window), \
                        patch.object(selection.uia, "ControlFromHandle", side_effect=lambda hwnd: window if hwnd == 42 else renderer):
                    self.assertEqual(selection.read(False), "" if secure else "old")
                    context = edit.capture(False)
                    self.assertIsNotNone(context)
                    self.assertEqual(context.secure, secure)
                    self.assertEqual(context.text, "" if secure else "old")
                renderer.GetPattern.assert_not_called()
                window.GetPattern.assert_not_called()
                if secure:
                    document.GetPattern.assert_not_called()

    def test_renderer_exception_requires_its_native_class_and_verified_outer_root(self):
        from harness_discussion.win import edit, selection
        for case in ("missing root", "malformed root", "root error", "UIA class", "native class", "wrong root"):
            with self.subTest(case=case):
                document = Document()
                window = Mock(ControlType=selection.uia.ControlType.WindowControl, NativeWindowHandle=42)
                window.Element.GetCurrentPropertyValueEx.return_value = UNSUPPORTED
                window.GetParentControl.return_value = None
                renderer = Mock(ControlType=selection.uia.ControlType.PaneControl, NativeWindowHandle=43,
                                ClassName="Chrome_RenderWidgetHostHWND")
                renderer.Element.GetCurrentPropertyValueEx.return_value = UNSUPPORTED
                renderer.GetParentControl.return_value = window
                document.GetParentControl = lambda: renderer
                document.GetPattern = Mock(wraps=document.GetPattern)
                if case == "missing root":
                    renderer.GetParentControl.return_value = None
                elif case == "malformed root":
                    window.Element.GetCurrentPropertyValueEx.return_value = object()
                elif case == "root error":
                    window.Element.GetCurrentPropertyValueEx.side_effect = OSError("provider failed")
                elif case == "UIA class":
                    renderer.ClassName = "UnverifiedPane"
                with target(document), native_host(window), ExitStack() as stack:
                    stack.enter_context(patch.object(selection.uia, "ControlFromHandle",
                                                     side_effect=lambda hwnd: window if hwnd == 42 else renderer))
                    if case == "native class":
                        stack.enter_context(patch.object(selection.u32, "GetClassNameW", return_value=0))
                    elif case == "wrong root":
                        stack.enter_context(patch.object(selection.uia, "GetAncestor", return_value=99))
                    self.assertEqual(selection.read(False), "")
                    self.assertIsNone(edit.capture(False))
                document.GetPattern.assert_not_called()
                renderer.GetPattern.assert_not_called()
                window.GetPattern.assert_not_called()

    def test_empty_child_text_pattern_does_not_hide_parent_selection(self):
        from harness_discussion.win import selection
        for collapsed in (True, False):
            with self.subTest(collapsed=collapsed):
                child, parent = Document("child", 0, 0), Document("selected parent", 0, 15)
                if not collapsed:
                    child.GetSelection = lambda: []
                child.GetParentControl = lambda: parent
                with target(child):
                    self.assertEqual(selection.read(False), "selected parent")

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

    def test_read_selection_checks_password_ancestors_before_patterns(self):
        from harness_discussion.win import selection
        parent = Mock()
        parent.IsPassword = True
        parent.Element.GetCurrentPropertyValueEx.return_value = True
        child = Mock()
        child.IsPassword = False
        child.Element.GetCurrentPropertyValueEx.return_value = False
        child.GetParentControl.return_value = parent
        child.GetPattern.return_value.GetSelection.return_value = [Mock(GetText=Mock(return_value="synthetic secret"))]
        with patch.object(selection.uia, "GetFocusedControl", return_value=child):
            self.assertEqual(selection.read(False), "")
        child.GetPattern.assert_not_called()

    def test_password_metadata_prevents_every_text_read(self):
        from harness_discussion.win import edit
        control = Mock()
        control.IsPassword = True
        control.Element.GetCurrentPropertyValueEx.return_value = True
        native = Mock()
        native.GetForegroundWindow.return_value = 42
        native.GetClassNameW.side_effect = lambda hwnd, buf, n: setattr(buf, "value", "Notepad") or 7
        def pid(hwnd, out):
            out._obj.value = 9
            return 1
        native.GetWindowThreadProcessId.side_effect = pid
        with patch.object(edit, "u32", native), patch.object(edit.uia, "GetFocusedControl", return_value=control):
            context = edit.capture(False)
        self.assertTrue(context.secure)
        self.assertEqual(context.text, "")
        control.GetPattern.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
