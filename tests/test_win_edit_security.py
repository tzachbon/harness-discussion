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

from tests.win_edit_fakes import UNSUPPORTED, Document, Range, clipboard, native_host, target

class CaptureSafety(unittest.TestCase):
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


    def test_webview2_in_winui_is_read_only_when_its_host_is_inside_the_foreground_window(self):
        """WhatsApp: focus stops at the WinUI bridge, and the WebView2 browser window is its own top-level window."""
        from harness_discussion.win import edit, selection
        classes = {43: "Chrome_RenderWidgetHostHWND", 44: "Chrome_WidgetWin_1", 45: "Chrome_WidgetWin_0", 46: "Bridge"}
        roots = {42: 42, 43: 44, 44: 44, 45: 42, 46: 42}
        for case in ("hosted", "password", "unvouched", "unvouched, window unsupported", "inner password", "no inner"):
            with self.subTest(case=case):
                document = Document()
                def pane(hwnd, parent, kind=selection.uia.ControlType.PaneControl, password=UNSUPPORTED):
                    control = Mock(ControlType=kind, NativeWindowHandle=hwnd, ClassName=classes.get(hwnd, ""))
                    control.Element.GetCurrentPropertyValueEx.return_value = password
                    control.GetParentControl.return_value = parent
                    return control
                window = pane(42, None, selection.uia.ControlType.WindowControl,
                              True if case == "password" else UNSUPPORTED if case.endswith("unsupported") else False)
                bridge = pane(46, window, password=False)
                bridge.ClassName = selection.WINUI_BRIDGE
                host = pane(45, bridge)
                browser = pane(44, window if case.startswith("unvouched") else host)
                document.IsPassword = case == "inner password"
                renderer = pane(43, browser)
                document.GetParentControl = lambda: renderer
                document.GetPattern = Mock(wraps=document.GetPattern)
                handles = {c.NativeWindowHandle: c for c in (window, bridge, host, browser, renderer)}
                native_class = lambda hwnd, buf, size: setattr(buf, "value", classes.get(hwnd, "")) or 1
                with target(bridge), native_host(window), \
                        patch.object(selection.uia, "ControlFromHandle", side_effect=handles.get), \
                        patch.object(selection.uia, "GetAncestor", side_effect=lambda hwnd, flag: roots[hwnd]), \
                        patch.object(selection.u32, "GetClassNameW", side_effect=native_class), \
                        patch.object(selection.uia.Control, "CreateControlFromElement", return_value=document):
                    bridge.Element.FindFirst.return_value = None if case == "no inner" else object()
                    context = edit.capture(False)
                    self.assertEqual(selection.read(False), "old" if case == "hosted" else "")
                if case == "hosted":
                    self.assertEqual((context.text, context.secure), ("old", False))
                elif case in ("password", "inner password"):
                    self.assertTrue(context.secure)
                else:
                    self.assertIsNone(context)
                if case != "hosted":
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
