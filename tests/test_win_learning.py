"""Bound Windows observations; native UIA calls are mocked."""
import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

from dataclasses import replace
from unittest.mock import patch

from tests import test_win_live as fixtures
from tests.win_edit_fakes import Document, Range, target

LiveDocument, units = fixtures.LiveDocument, fixtures.units


class BoundObserve(unittest.TestCase):
    setUp = fixtures.Observe.setUp

    def test_bound_observe_allows_document_and_caret_edits(self):
        document = LiveDocument("old name")
        with target(document) as (_, key, snap, cleanup):
            ctx = self.edit.capture(False)
            document.text = "new 👋\r\nname"
            document.start = document.end = len(document.text)
            field = self.edit.observe(ctx)
            self.assertEqual(field.text, "new 👋\nname")
            self.assertEqual(field.caret, (units(field.text), units(field.text)))
            self.assertEqual(field.handle, ctx._element)
            self.assertNotEqual(field.digest, ctx._digest)
            key.assert_not_called()
            snap.assert_not_called()
            cleanup.assert_not_called()

    def test_bound_identity_mismatch_never_reads_text(self):
        document = LiveDocument("private words")
        with target(document):
            ctx = self.edit.capture(False)
            for change in ({"app": 10}, {"window": 43}, {"_element": (3, 4)},
                           {"app": 0}, {"window": 0}, {"_element": ()},
                           {"secure": True}, {"editable": False}):
                with self.subTest(change=change), patch.object(Range, "GetText", side_effect=AssertionError(
                        "a different or unknown target must not be read")) as read:
                    self.assertIsNone(self.edit.observe(replace(ctx, **change)))
                    read.assert_not_called()

    def test_password_ancestors_and_read_only_fields_block_bound_text_reads(self):
        document, parent = LiveDocument("private words"), Document()
        document.GetParentControl = lambda: parent
        with target(document):
            ctx = self.edit.capture(False)
            for secure, writable in ((True, True), (False, False)):
                parent.IsPassword, document.writable = secure, writable
                with self.subTest(secure=secure), patch.object(Range, "GetText", side_effect=AssertionError(
                        "security must be checked before GetText")) as read:
                    self.assertIsNone(self.edit.observe(ctx))
                    read.assert_not_called()

    def test_focus_identity_and_security_changes_during_read_fail_closed(self):
        for bound in (False, True):
            for change in ("field", "focus", "password", "ancestor", "window", "read only", "unknown security"):
                with self.subTest(bound=bound, change=change):
                    document, parent = LiveDocument("private words"), Document()
                    document.GetParentControl = lambda: parent
                    other = LiveDocument("another field")
                    other.identity = [8, 9]
                    with target(document) as (native, _, _, _):
                        ctx = self.edit.capture(False) if bound else None
                        original = Range.GetText
                        def read(span, maximum):
                            result = original(span, maximum)
                            if change == "field":
                                document.identity = [3, 4]
                            elif change == "focus":
                                self.edit.uia.GetFocusedControl.return_value = other
                            elif change == "password":
                                document.IsPassword = True
                            elif change == "ancestor":
                                parent.IsPassword = True
                            elif change == "window":
                                native.GetForegroundWindow.return_value = 43
                            elif change == "read only":
                                document.writable = False
                            else:
                                parent.Element.GetCurrentPropertyValueEx = lambda *a: None
                            return result
                        with patch.object(Range, "GetText", read):
                            self.assertIsNone(self.edit.observe(ctx))

    def test_bound_observe_rejects_missing_identity_and_utf16_over_cap(self):
        document = LiveDocument("hello")
        with target(document):
            ctx = self.edit.capture(False)
            for identity in ([], [8, 9]):
                document.identity = identity
                with self.subTest(identity=identity), patch.object(Range, "GetText") as read:
                    self.assertIsNone(self.edit.observe(ctx))
                    read.assert_not_called()
            document.identity = list(ctx._element)
            document.text = "👋" * (self.edit.live.LIVE_MAX_DOC // 2 + 1)
            document.start = document.end = len(document.text)
            self.assertIsNone(self.edit.observe(ctx))
