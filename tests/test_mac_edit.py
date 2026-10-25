"""The Mac edit guards with capture and paste patched: no app is driven."""
import sys
import unittest
from unittest import mock

if sys.platform != "darwin":
    raise unittest.SkipTest("macOS only")

from Foundation import NSString

from harness_discussion.edit_logic import Context
from harness_discussion.mac import edit

FIELD, OTHER = NSString.stringWithString_("field"), NSString.stringWithString_("other")
CTX = Context("", 1, 2, True, False, FIELD, (5, 0), b"document")


class GuardTest(unittest.TestCase):
    def typed(self, now):
        pasted = []
        with mock.patch.object(edit, "capture", return_value=now), \
                mock.patch.object(edit, "_paste", side_effect=lambda t, ctx=None, **kw: pasted.append(t) or True):
            ok = edit.type_at_caret("hello", CTX)
        return ok, pasted

    def test_dictation_types_only_at_the_captured_caret_in_the_captured_field(self):
        self.assertEqual(self.typed(Context("", 1, 2, True, False, FIELD, (5, 0), b"document")), (True, ["hello"]))
        self.assertEqual(self.typed(Context("", 1, 2, True, False, OTHER, (5, 0))), (False, []))
        self.assertEqual(self.typed(Context("x", 1, 2, True, False, FIELD, (0, 3))), (False, []))  # moved selection
        self.assertEqual(self.typed(Context("", 1, 2, True, True, FIELD, (5, 0))), (False, []))  # now secure
        self.assertEqual(self.typed(None), (False, []))

    def test_rewrite_refuses_the_same_text_selected_elsewhere(self):
        ctx = Context("word", 1, 2, True, False, FIELD, (5, 4))
        with mock.patch.object(edit, "capture", return_value=Context("word", 1, 2, True, False, FIELD, (20, 4))), \
                mock.patch.object(edit, "_paste") as paste:
            self.assertFalse(edit.replace("Word", ctx))
        paste.assert_not_called()

    def test_an_unconfirmed_paste_leaves_the_text_on_the_pasteboard(self):
        with mock.patch.object(edit.pasteboard, "safe_to_borrow", return_value=True), \
                mock.patch.object(edit.pasteboard, "snapshot", return_value=[]), \
                mock.patch.object(edit.pasteboard, "restore") as restore, \
                mock.patch.object(edit, "AppKit"), \
                mock.patch.object(edit, "_frontmost_pid", return_value=1), mock.patch.object(edit, "_key"), \
                mock.patch.object(edit, "PASTE_ACK_S", 0.05):
            def reads(values):  # each call returns the next value, then the last one again
                values = list(values)
                return lambda _: values.pop(0) if len(values) > 1 else values[0]

            def paste(focus, states):
                with mock.patch.object(edit, "_focused", side_effect=reads(focus)), \
                        mock.patch.object(edit, "_field_state", side_effect=reads(states)):
                    self.assertTrue(edit._paste("hello"))
            paste([FIELD], [None])  # unreadable from the start
            paste([FIELD], [(0, 0, 3)])  # never changed
            paste([FIELD], [(0, 0, 3), None])  # stopped answering
            paste([FIELD, OTHER], [(0, 0, 3), (5, 0, 8)])  # another field changed
            restore.assert_not_called()
            paste([FIELD], [(0, 0, 3), (5, 0, 8)])
            restore.assert_called_once()

    def test_select_back_accepts_the_moved_caret_after_a_paste(self):
        with mock.patch.object(edit, "capture", return_value=Context("", 1, 2, True, False, FIELD, (9, 0))), \
                mock.patch.object(edit, "_focused", return_value=None), mock.patch.object(edit, "_key"):
            self.assertTrue(edit.select_back(4, CTX))
        with mock.patch.object(edit, "capture", return_value=Context("", 1, 2, True, False, OTHER, (9, 0))):
            self.assertFalse(edit.select_back(4, CTX))


if __name__ == "__main__":
    unittest.main()
