"""Bound Mac observations; native AX calls are mocked."""
import sys
import unittest

if sys.platform != "darwin":
    raise unittest.SkipTest("macOS only")

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from tests import test_mac_live as fixtures
from harness_discussion import live
from harness_discussion.edit_logic import Context
from harness_discussion.mac import edit

FIELD, OTHER = fixtures.FIELD, fixtures.OTHER


class BoundObserve(unittest.TestCase):
    app = fixtures.Observe.app

    @contextmanager
    def bound_target(self):
        state = SimpleNamespace(text="old name", rng=(8, 0), parent=None, role="AXTextArea", subrole=None,
                                count=None, during_read=lambda: None, during_range=lambda: None)
        def attr(element, name):
            if name == "AXValue":
                state.during_read()
                return state.text
            if element is OTHER:
                return "AXSecureTextField" if name == "AXRole" else None
            return {"AXRole": state.role, "AXSubrole": state.subrole, "AXParent": state.parent,
                    "AXNumberOfCharacters": state.count, "AXSelectedTextRange": "range"}.get(name)
        def range_value(*args):
            state.during_range()
            return True, state.rng
        with mock.patch.object(edit.permission, "trusted", return_value=True), self.app("com.google.Chrome"), \
                mock.patch.object(edit.selection, "_expose") as expose, \
                mock.patch.object(edit, "_enhance") as enhance, \
                mock.patch.object(edit, "_focused", return_value=FIELD) as focus, \
                mock.patch.object(edit, "_frontmost_pid", return_value=7) as pid, \
                mock.patch.object(edit, "_window_number", return_value=2) as window, \
                mock.patch.object(edit, "_attr", side_effect=attr) as read, \
                mock.patch.object(edit, "AXValueGetValue", side_effect=range_value), \
                mock.patch.object(edit, "AXUIElementIsAttributeSettable", return_value=(edit.kAXErrorSuccess, True)) as writable, \
                mock.patch.object(edit.selection, "secure_input", return_value=False) as secure, \
                mock.patch.object(edit, "_rect_of", return_value=(0, 0, 10, 10)), \
                mock.patch.object(edit, "AXUIElementSetAttributeValue") as write, \
                mock.patch.object(edit, "_key") as key, mock.patch.object(edit.pasteboard, "snapshot") as clipboard:
            state.ctx = Context("", 7, 2, True, False, FIELD, (0, 0), b"old digest")
            state.focus, state.pid, state.window = focus, pid, window
            state.read, state.writable, state.secure = read, writable, secure
            yield state
            write.assert_not_called()
            key.assert_not_called()
            clipboard.assert_not_called()
            expose.assert_not_called()
            enhance.assert_not_called()

    def assert_no_text_reads(self, read):
        self.assertFalse(any(call.args[1] in ("AXValue", "AXSelectedText") for call in read.call_args_list))

    def test_bound_observe_allows_document_and_caret_edits_with_raw_utf16_offsets(self):
        with self.bound_target() as state:
            state.text, state.rng = "new 👋\r\nname", (8, 4)
            field = edit.observe(state.ctx)
            self.assertEqual((field.text, field.caret), (state.text, (8, 12)))
            self.assertIs(field.handle, FIELD)
            self.assertNotEqual(field.digest, state.ctx._digest)

    def test_bound_identity_mismatch_never_reads_text(self):
        for change in ({"app": 8}, {"window": 3}, {"field": OTHER}, {"app": 0}, {"window": 0},
                       {"field": None}, {"secure": True}, {"editable": False}):
            with self.subTest(change=change), self.bound_target() as state:
                self.assertIsNone(edit.observe(replace(state.ctx, **change)))
                self.assert_no_text_reads(state.read)

    def test_secure_ancestors_unknown_roles_and_read_only_fields_block_text_reads(self):
        for change in ("ancestor", "role", "subrole", "secure input", "unknown role", "read only", "no window"):
            with self.subTest(change=change), self.bound_target() as state:
                if change == "ancestor":
                    state.parent = OTHER
                elif change == "role":
                    state.role = "AXSecureTextField"
                elif change == "subrole":
                    state.subrole = "AXSecureTextField"
                elif change == "secure input":
                    state.secure.return_value = True
                elif change == "unknown role":
                    state.role = None
                elif change == "read only":
                    state.writable.return_value = edit.kAXErrorSuccess, False
                else:
                    state.window.return_value = 0
                self.assertIsNone(edit.observe(state.ctx))
                self.assert_no_text_reads(state.read)

    def test_focus_identity_and_security_changes_during_read_fail_closed(self):
        for change in ("field", "process", "window", "password", "ancestor", "secure input", "read only"):
            with self.subTest(change=change), self.bound_target() as state:
                def during_read():
                    if change == "field":
                        state.focus.return_value = OTHER
                    elif change == "process":
                        state.pid.return_value = 8
                    elif change == "window":
                        state.window.return_value = 3
                    elif change == "password":
                        state.role = "AXSecureTextField"
                    elif change == "ancestor":
                        state.parent = OTHER
                    elif change == "secure input":
                        state.secure.return_value = True
                    else:
                        state.writable.return_value = edit.kAXErrorSuccess, False
                state.during_read = during_read
                self.assertIsNone(edit.observe(state.ctx))

    def test_password_transition_during_range_query_prevents_value_read(self):
        with self.bound_target() as state:
            state.during_range = lambda: setattr(state, "parent", OTHER)
            self.assertIsNone(edit.observe(state.ctx))
            self.assert_no_text_reads(state.read)

    def test_malformed_native_ax_ranges_fail_closed(self):
        for rng in (None, (), (1,), (1, 0, 0), "bad", (-1, 0), (0, -1), (True, 0), (0, False),
                    (1.5, 0), ("1", 0), (0, live.LIVE_MAX_DOC + 1), (9, 0), (7, 2)):
            with self.subTest(rng=rng), self.bound_target() as state:
                state.rng = rng
                self.assertIsNone(edit.observe(state.ctx))

    def test_surrogate_boundaries_and_utf16_document_cap_fail_closed(self):
        for rng in ((1, 0), (0, 1), (1, 1)):
            with self.subTest(rng=rng), self.bound_target() as state:
                state.text, state.rng = "👋!", rng
                self.assertIsNone(edit.observe(state.ctx))
        with self.bound_target() as state:
            state.text, state.rng = "👋" * (live.LIVE_MAX_DOC // 2 + 1), (0, 0)
            self.assertIsNone(edit.observe(state.ctx))
        for count in (-1, True, "bad", live.LIVE_MAX_DOC + 1):
            with self.subTest(count=count), self.bound_target() as state:
                state.count = count
                self.assertIsNone(edit.observe(state.ctx))
                self.assert_no_text_reads(state.read)
