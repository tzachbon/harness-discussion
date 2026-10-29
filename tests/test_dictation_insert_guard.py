"""Mac paste cancellation at the native boundary, without driving an application."""
from contextlib import nullcontext
import importlib.util
from dataclasses import replace
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

from harness_discussion.edit_logic import Context


class MacInsertionGuard(unittest.TestCase):
    def setUp(self):
        appkit, quartz = Mock(), Mock()
        services = types.ModuleType("ApplicationServices")
        for name in ("AXUIElementCopyAttributeValue", "AXUIElementCreateApplication",
                     "AXUIElementIsAttributeSettable", "AXUIElementSetAttributeValue", "AXValueCreate",
                     "AXValueGetValue", "kAXErrorSuccess", "kAXValueCFRangeType"):
            setattr(services, name, Mock())
        foundation = types.ModuleType("CoreFoundation")
        foundation.CFEqual, foundation.CFRangeMake = lambda a, b: a == b, Mock()
        board = types.ModuleType("harness_discussion.mac.pasteboard")
        board._borrow = threading.RLock()
        board.types, board.safe_to_borrow = Mock(return_value=[]), Mock(return_value=True)
        board.snapshot, board.restore = Mock(return_value=[]), Mock()
        permission = types.ModuleType("harness_discussion.mac.permission")
        selection = types.ModuleType("harness_discussion.mac.selection")
        modules = {"AppKit": appkit, "Quartz": quartz, "ApplicationServices": services,
                   "objc": types.SimpleNamespace(autorelease_pool=nullcontext),
                   "CoreFoundation": foundation, "harness_discussion.mac.pasteboard": board,
                   "harness_discussion.mac.permission": permission,
                   "harness_discussion.mac.selection": selection}
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, modules).start()
        spec = importlib.util.spec_from_file_location(
            "harness_discussion.mac._dictation_guard_test_edit",
            Path(__file__).parents[1] / "src/harness_discussion/mac/edit.py")
        self.edit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.edit)
        self.edit.pasteboard = board
        self.edit.permission, self.edit.selection = permission, selection
        self.board = appkit.NSPasteboard.generalPasteboard.return_value
        self.board.changeCount.return_value = 10
        self.activity = threading.Event()
        self.ctx = Context("", 1, 2, True, False, "field", (5, 0), b"document")
        self.capture = self.edit.capture
        self.edit.capture = Mock(return_value=self.ctx)
        self.edit._frontmost_pid, self.edit._focused = Mock(return_value=1), Mock(return_value="field")
        self.edit._key = Mock()
        self.edit._field_state = Mock(return_value=((5, 0), 14))
        self.edit.PASTE_ACK_S = 0

    def test_late_focused_field_must_match_the_preparation_target(self):
        self.edit._focused.return_value = "other"
        self.assertFalse(self.edit.type_at_caret("new", self.ctx, cancelled=self.activity.is_set))
        self.edit._key.assert_not_called()

    def test_programmatic_focus_change_during_final_state_read_prevents_input(self):
        def state(element):
            self.edit.capture.return_value = Context("", 1, 2, True, False, "other", (5, 0))
            self.edit._focused.return_value = "other"
            return ((5, 0), 14)
        self.edit._field_state.side_effect = state
        self.assertFalse(self.edit.type_at_caret("new", self.ctx, cancelled=self.activity.is_set))
        self.edit._key.assert_not_called()

    def test_selected_content_change_at_the_same_range_prevents_input(self):
        ctx = Context("old", 1, 2, True, False, "field", (5, 3), b"document")
        self.edit.capture.return_value = Context("new", 1, 2, True, False, "field", (5, 3), b"document")
        self.edit._field_state.return_value = ((5, 3), 14)
        self.assertFalse(self.edit.type_at_caret("words", ctx, cancelled=self.activity.is_set))
        self.edit._key.assert_not_called()

    def test_document_change_outside_unchanged_caret_prevents_input(self):
        self.edit.capture.return_value = replace(self.ctx, _digest=b"changed")
        self.assertFalse(self.edit.type_at_caret("new", self.ctx))
        self.edit._key.assert_not_called()

    def test_missing_document_proof_cannot_insert(self):
        for ctx in (None, replace(self.ctx, _digest=None)):
            with self.subTest(ctx=ctx):
                self.assertFalse(self.edit.type_at_caret("new", ctx))
        self.edit._key.assert_not_called()
        self.edit.pasteboard.snapshot.assert_not_called()

    def test_clipboard_change_during_final_validation_prevents_input(self):
        def state(element):
            self.board.changeCount.return_value = 11
            return ((5, 0), 14)
        self.edit._field_state.side_effect = state
        self.assertFalse(self.edit.type_at_caret("new", self.ctx))
        self.edit._key.assert_not_called()
        self.edit.pasteboard.restore.assert_not_called()

    def test_unchanged_proven_target_inserts_once(self):
        self.assertTrue(self.edit.type_at_caret("new", self.ctx))
        self.edit._key.assert_called_once()

    def native_capture(self):
        self.edit.capture = self.capture
        self.edit.permission.trusted = Mock(return_value=True)
        self.edit.selection.SECURE = "AXSecureTextField"
        self.edit.selection.secure_input = Mock(return_value=False)
        self.edit.selection._expose = Mock()
        self.edit.selection._via_ax = Mock(return_value=("ancestor", False))
        self.edit._window_number = Mock(return_value=2)
        self.edit._range = Mock(return_value=(5, 3))
        self.edit._field_state.return_value = ((5, 3), 14)
        attrs = {"AXRole": "AXTextArea", "AXSubrole": None, "AXValue": "Left old right"}
        self.edit._attr = Mock(side_effect=lambda element, name: attrs.get(name))
        return attrs

    def test_actual_capture_anchors_text_and_rejects_late_focus_change(self):
        self.native_capture()
        baseline = self.edit.capture(False)
        self.assertEqual(baseline.text, "old")
        self.assertIsNotNone(baseline._digest)
        self.edit.selection._via_ax.assert_not_called()
        self.edit._key.assert_not_called()
        for loss in ("focus", "clipboard"):
            with self.subTest(loss=loss):
                armed = []
                self.edit._focused.return_value = "field"
                self.board.changeCount.return_value = 10
                self.edit.pasteboard.restore.reset_mock()
                attr = self.edit._attr.side_effect
                def changed(element, name):
                    value = attr(element, name)
                    if armed and name == "AXRole":
                        self.edit._focused.return_value = "other"
                        if loss == "clipboard":
                            self.board.changeCount.return_value = 11
                    return value
                self.edit._attr.side_effect = changed
                self.edit.pasteboard.snapshot.side_effect = lambda: armed.append(True) or []
                self.assertFalse(self.edit.type_at_caret("new", baseline))
                self.edit._key.assert_not_called()
                if loss == "focus":
                    self.edit.pasteboard.restore.assert_called_once_with([])
                else:
                    self.edit.pasteboard.restore.assert_not_called()
                self.edit._attr.side_effect = attr

    def test_actual_capture_requires_bounded_document_and_valid_utf16_range(self):
        attrs = self.native_capture()
        for value, rng in ((None, (5, 3)), (17, (5, 3)), ("x" * (self.edit.MAX_DOCUMENT + 1), (5, 3)),
                           ("short", (-1, 0)), ("short", (5, 1))):
            with self.subTest(rng=rng, value_type=type(value).__name__):
                attrs["AXValue"] = value
                self.edit._range.return_value = rng
                self.assertIsNone(self.edit.capture(False)._digest)

    def test_actual_capture_uses_utf16_ranges_and_accepts_an_empty_document(self):
        attrs = self.native_capture()
        attrs["AXValue"] = "x😀old"
        self.edit._range.return_value = (3, 3)
        ctx = self.edit.capture(False)
        self.assertEqual(ctx.text, "old")
        self.assertIsNotNone(ctx._digest)
        attrs["AXValue"] = ""
        self.edit._range.return_value = (0, 0)
        self.assertIsNotNone(self.edit.capture(False)._digest)

    def test_secure_capture_never_reads_the_document_value(self):
        attrs = self.native_capture()
        attrs["AXRole"] = "AXSecureTextField"
        self.edit.AXUIElementIsAttributeSettable.return_value = (1, False)
        ctx = self.edit.capture(False)
        self.assertTrue(ctx.secure)
        self.assertIsNone(ctx._digest)
        self.assertEqual(ctx.text, "")
        self.assertNotIn("AXValue", [call.args[1] for call in self.edit._attr.call_args_list])

    def test_actual_capture_rechecks_foreground_after_the_last_focus_read(self):
        self.native_capture()
        reads = []
        def focused(pid):
            reads.append(pid)
            if len(reads) == 2:
                self.edit._frontmost_pid.return_value = 3
            return "field"
        self.edit._focused.side_effect = focused
        self.assertIsNone(self.edit.capture(False))
        self.edit._key.assert_not_called()

    def test_activity_during_last_native_read_prevents_paste_and_restores_clipboard(self):
        def read(element):
            self.activity.set()
            return ((5, 0), 14)
        self.edit._field_state = Mock(side_effect=read)
        self.assertFalse(self.edit.type_at_caret("new", self.ctx, cancelled=self.activity.is_set))
        self.edit._key.assert_not_called()
        self.edit.pasteboard.restore.assert_called_once_with([])

    def test_cancelled_paste_does_not_restore_over_a_new_clipboard_write(self):
        def read(element):
            self.activity.set()
            self.board.changeCount.return_value = 11
            return ((5, 0), 14)
        self.edit._field_state = Mock(side_effect=read)
        self.assertFalse(self.edit.type_at_caret("new", self.ctx, cancelled=self.activity.is_set))
        self.edit._key.assert_not_called()
        self.edit.pasteboard.restore.assert_not_called()

    def test_already_cancelled_does_not_borrow_the_clipboard(self):
        self.activity.set()
        self.assertFalse(self.edit.type_at_caret("new", self.ctx, cancelled=self.activity.is_set))
        self.edit._key.assert_not_called()
        self.edit.pasteboard.snapshot.assert_not_called()

    def test_partial_key_post_failure_keeps_transcript_on_clipboard(self):
        self.edit._field_state = Mock(return_value=((5, 0), 14))
        self.edit._key.side_effect = RuntimeError("partial native post")
        with self.assertRaises(RuntimeError):
            self.edit.type_at_caret("new", self.ctx, cancelled=self.activity.is_set)
        self.edit.pasteboard.restore.assert_not_called()

    def test_mac_native_exception_through_application_never_copies_or_retries(self):
        from tests.test_app_writer import _App
        for failure in ("partial_post", "acknowledgement"):
            with self.subTest(failure=failure):
                app = _App()
                app.edit, app._dictate_ctx = self.edit, self.ctx
                self.edit._key.reset_mock(side_effect=True)
                self.edit._field_state.side_effect = None
                self.edit.PASTE_ACK_S = .1
                if failure == "partial_post":
                    self.edit._key.side_effect = RuntimeError("private native details")
                    self.edit._field_state.return_value = ((5, 0), 14)
                else:
                    self.edit._field_state.side_effect = [((5, 0), 14), RuntimeError("private acknowledgement")]
                app.deliver(app._rewrite_id, None, ("words", "Local", None), (0, 0, None),
                            (0, .1, None, "Local"))
                self.edit._key.assert_called_once()
                self.assertEqual(app.copied, [])
                self.assertFalse(app.toast_.visible)
                self.assertNotIn("private", repr(app.toast_.shown))

    def test_mac_explicit_refusal_through_application_automatically_copies_once(self):
        from tests.test_app_writer import _App
        app = _App()
        app.edit, app._dictate_ctx = self.edit, self.ctx
        self.edit._focused.return_value = "other"
        app.deliver(app._rewrite_id, None, ("words", "Local", None), (0, 0, None),
                    (0, .1, None, "Local"))
        self.edit._key.assert_not_called()
        self.assertEqual(app.copied, ["words"])
        self.assertEqual(app.toast_.shown[-1][0], "Copied to clipboard")
