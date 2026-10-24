"""Check as you type on Windows: observe, bounds, and select_range against deterministic UIA providers."""
import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

from types import SimpleNamespace
from unittest.mock import patch

from tests.win_edit_fakes import Document, Range, target


class LiveRange(Range):
    """A range whose Character unit is the provider's: CRLF counts as two, and `document.chunks` can join several
    code points into one character, as some providers do for emoji sequences."""

    def Clone(self):
        return LiveRange(self.document, self.start, self.end)

    def _step(self, at, count):
        stops = self.document.stops()
        i = stops.index(at) if at in stops else min(range(len(stops)), key=lambda k: abs(stops[k] - at))
        return stops[max(0, min(len(stops) - 1, i + count))]

    def MoveEndpointByUnit(self, endpoint, unit, count, waitTime=0):
        if endpoint == 0:
            self.start = self._step(self.start, count)
            self.end = max(self.end, self.start)
        else:
            self.end = self._step(self.end, count)
            self.start = min(self.start, self.end)
        return count

    def GetBoundingRectangles(self):
        if not self.document.visible or self.start == self.end:
            return []
        line = self.document.text[:self.start].count("\n")
        column = self.start - (self.document.text.rfind("\n", 0, self.start) + 1)
        return [SimpleNamespace(left=column * 8, top=line * 20, right=(column + self.end - self.start) * 8,
                                bottom=line * 20 + 18)]


class LiveDocument(Document):
    def __init__(self, text, caret=None, chunks=()):
        caret = len(text) if caret is None else caret
        super().__init__(text, caret, caret)
        self.chunks, self.visible, self.reads = list(chunks), True, []
        self.BoundingRectangle = SimpleNamespace(left=10, top=20, right=410, bottom=220)

    def stops(self):
        inside = {i for a, b in self.chunks for i in range(a + 1, b)}
        return [i for i in range(len(self.text) + 1) if i not in inside]

    @property
    def DocumentRange(self):
        return LiveRange(self, 0, len(self.text))

    def GetSelection(self):
        return [LiveRange(self, self.start, self.end)]


def units(text):
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


class Observe(unittest.TestCase):
    def setUp(self):
        from harness_discussion.win import edit
        self.edit = edit
        exe = patch.object(edit, "_exe", return_value="notepad.exe")
        exe.start()
        self.addCleanup(exe.stop)

    def test_reads_the_field_without_clipboard_or_keys(self):
        document = LiveDocument("first line\r\nsecond 👋 line")
        with target(document) as (_, key, snap, _):
            field = self.edit.observe()
            key.assert_not_called()
            snap.assert_not_called()
        self.assertEqual(field.text, "first line\nsecond 👋 line")
        self.assertEqual(field.caret, (units(field.text), units(field.text)))
        self.assertEqual((field.app_name, field.label, field.rect), ("notepad.exe", "Notepad", (10, 20, 410, 220)))

    def test_refuses_our_own_window_before_asking_uia(self):
        document = LiveDocument("some text here")
        with target(document), patch.object(self.edit.os, "getpid", return_value=9), \
                patch.object(self.edit.uia, "GetFocusedControl") as focused:
            self.assertIsNone(self.edit.observe())
            focused.assert_not_called()

    def test_refuses_password_read_only_and_long_fields(self):
        from harness_discussion import live
        document = LiveDocument("secret words here")
        document.IsPassword = True
        with target(document):
            self.assertIsNone(self.edit.observe())
        document = LiveDocument("read only words")
        document.writable = False
        with target(document):
            self.assertIsNone(self.edit.observe())
        document = LiveDocument("x" * (live.LIVE_MAX_DOC + 1))
        with target(document):
            self.assertIsNone(self.edit.observe())


class Place(unittest.TestCase):
    def setUp(self):
        from harness_discussion.win import edit
        self.edit = edit
        exe = patch.object(edit, "_exe", return_value="notepad.exe")
        exe.start()
        self.addCleanup(exe.stop)

    def span(self, field, old):
        start = units(field.text[:field.text.index(old)])
        return start, start + units(old), old

    def test_finds_text_after_many_line_breaks_and_emoji(self):
        text = "line 👋\r\n" * 20 + "i thinks so"
        document = LiveDocument(text)
        with target(document):
            field = self.edit.observe()
            rects = self.edit.bounds(field, [self.span(field, "thinks")])
        self.assertEqual(rects, [[(16, 400, 64, 418)]])

    def test_a_joined_emoji_sequence_counted_as_one_character(self):
        family = "👨‍👩‍👧"
        text = f"we {family} goes home"
        start = text.index(family)
        document = LiveDocument(text, chunks=[(start, start + len(family))])
        with target(document):
            field = self.edit.observe()
            self.assertEqual(len(self.edit.bounds(field, [self.span(field, "goes")])[0]), 1)
            self.assertIsNone(self.edit.bounds(field, [(units("we "), units("we 👨"), "👨")])[0])

    def test_changed_text_places_nothing_and_hidden_text_has_no_rectangles(self):
        document = LiveDocument("i thinks so ok")
        with target(document):
            field = self.edit.observe()
            document.visible = False
            self.assertEqual(self.edit.bounds(field, [self.span(field, "thinks")]), [[]])
            document.text += "!"
            self.assertIsNone(self.edit.bounds(field, [self.span(field, "thinks")]))

    def test_wrong_expected_text_is_not_placed(self):
        document = LiveDocument("i thinks so ok")
        with target(document):
            field = self.edit.observe()
            self.assertEqual(self.edit.bounds(field, [(2, 8, "think!")]), [None])


class SelectRange(unittest.TestCase):
    def setUp(self):
        from harness_discussion.win import edit
        self.edit = edit
        exe = patch.object(edit, "_exe", return_value="notepad.exe")
        exe.start()
        self.addCleanup(exe.stop)

    def test_apply_and_undo_through_the_existing_guards(self):
        from harness_discussion import edit_logic
        document = LiveDocument("ok\r\ni thinks so ok")
        with target(document) as (_, key, _, _):
            def paste(vk):
                data = dict(self.edit.clipboard.restore.call_args.args[0])[13]
                text = data.decode("utf-16-le")[:-1]
                document.text = document.text[:document.start] + text + document.text[document.end:]
                document.start += len(text)
                document.end = document.start
                return True
            key.side_effect = paste
            field = self.edit.observe()
            start = units("ok\ni ")
            ctx = self.edit.select_range(field, start, start + 6, "thinks")
            self.assertEqual((ctx._start, ctx._end, ctx.text), (start, start + 6, "thinks"))
            self.assertTrue(self.edit.replace("think", ctx))
            self.assertEqual(document.text, "ok\r\ni think so ok")
            self.assertTrue(edit_logic.undo(self.edit, ctx, "thinks", "think", self.edit.NEWLINE))
            self.assertEqual(document.text, "ok\r\ni thinks so ok")

    def test_a_selection_that_reads_back_wrong_is_collapsed(self):
        document = LiveDocument("i thinks so ok", caret=0)
        with target(document):
            field = self.edit.observe()
            wrong = lambda rng, waitTime=0: (setattr(document, "start", rng.start),
                                            setattr(document, "end", rng.end + 1), True)[-1]
            with patch.object(LiveRange, "Select", wrong):
                self.assertIsNone(self.edit.select_range(field, 2, 8, "thinks"))
            self.assertEqual(document.start, document.end)

    def test_changed_document_is_never_selected(self):
        document = LiveDocument("i thinks so ok", caret=0)
        with target(document):
            field = self.edit.observe()
            document.text = "i thinks so ok!"
            self.assertIsNone(self.edit.select_range(field, 2, 8, "thinks"))
            self.assertEqual((document.start, document.end), (0, 0))


if __name__ == "__main__":
    unittest.main()
