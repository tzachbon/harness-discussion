"""The practice adapter: what reaches the tour's box, what the sandbox blocks, and the shared box logic."""
import os
import unittest

from harness_discussion import edit_logic, live, practice, practice_box
from harness_discussion.practice import PracticeCtx

SAMPLE = "We recieved the report yesterday"


class FakeReal:
    """The platform edit module: it records calls and answers with `answer`."""

    NEWLINE = "\r\n"

    def __init__(self, answer=None):
        self.calls, self.answer = [], answer

    def capture(self, clipboard_fallback=False):
        self.calls.append(("capture", clipboard_fallback))
        return self.answer

    def same_context(self, ctx):
        self.calls.append(("same_context", ctx))
        return True

    def replace(self, text, ctx):
        self.calls.append(("replace", text, ctx))
        return True

    def type_at_caret(self, text, ctx, **kw):
        self.calls.append(("type_at_caret", text, ctx, kw))
        return True

    def select_back(self, n, ctx):
        self.calls.append(("select_back", n, ctx))
        return True

    def observe(self):
        self.calls.append(("observe",))
        return self.answer

    def bounds(self, field, spans):
        self.calls.append(("bounds", field, spans))
        return self.answer

    def select_range(self, field, start, end, expected):
        self.calls.append(("select_range", field, start, end, expected))
        return self.answer


class FakeRealWithUndo(FakeReal):
    """The Windows shape: it undoes by itself and can collapse the selection."""

    def undo(self, ctx, original, inserted):
        self.calls.append(("undo", ctx, original, inserted))
        return True

    def collapse(self):
        self.calls.append(("collapse",))


class FakeBox(practice_box.Box):
    """A box over a plain string; offsets are UTF-16 units, as in the real fields."""

    def __init__(self, text="", selection=(0, 0)):
        self.text_value, self.sel = text, selection

    def text(self):
        return self.text_value

    def selection(self):
        return self.sel

    def _py(self, offset):
        at = 0
        for i, ch in enumerate(self.text_value):
            if at >= offset:
                return i
            at += edit_logic.utf16_len(ch)
        return len(self.text_value)

    def _set(self, start, end, new):
        s, e = self._py(start), self._py(end)
        self.text_value = self.text_value[:s] + new + self.text_value[e:]
        self.sel = (start + edit_logic.utf16_len(new),) * 2

    def _select(self, start, end):
        self.sel = (start, end)

    def _rects(self, start, end):
        return [(0, 0, 8 * (end - start), 24)]

    def screen_rect(self):
        return (0, 0, 400, 300)


class FakeApp:
    """The app members PracticeEdit reads; on_main is immediate, since the tests run on one thread."""

    def __init__(self, box=None, step=None, sandbox=()):
        self.box, self.step, self.sandbox = box, step, set(sandbox)
        self.frontmost, self.marks = False, []

    def tour_box(self):
        return self.box

    def practice_box(self):
        return self.box if self.frontmost else None

    def tour_step(self):
        return self.step

    def sandboxed(self, *keys):
        return any(k in self.sandbox for k in keys)

    def practice_done(self, kind):
        self.marks.append(kind)

    def on_main(self, fn, default=None):
        return fn()


def make(real=None, box=None, step=None, sandbox=(), frontmost=False, read=None, target=None):
    app = FakeApp(box=box, step=step, sandbox=sandbox)
    app.frontmost = frontmost
    return app, practice.PracticeEdit(real if real is not None else FakeReal(), read, target, app)


class CaptureTest(unittest.TestCase):
    def test_the_frontmost_box_answers_with_a_practice_context(self):
        app, edit = make(box=FakeBox(SAMPLE, (3, 11)), frontmost=True)
        ctx = edit.capture()
        self.assertIsInstance(ctx, PracticeCtx)
        self.assertEqual(ctx.text, "recieved")
        self.assertEqual(ctx.caret, (3, 8))
        self.assertEqual((ctx.app, ctx.window), (os.getpid(), practice.PRACTICE_WINDOW))
        self.assertEqual(app.marks, [])

    def test_capture_passes_through_when_the_tour_is_not_frontmost(self):
        real = FakeReal(answer=edit_logic.Context("outside", 4242, 99, True, False))
        app, edit = make(real=real, box=FakeBox(SAMPLE), frontmost=False)
        self.assertIs(edit.capture(False), real.answer)
        self.assertEqual(real.calls, [("capture", False)])

    def test_capture_stays_home_while_writing_is_sandboxed(self):
        real = FakeReal()
        app, edit = make(real=real, box=FakeBox(SAMPLE), sandbox=("writer",))
        self.assertIsNone(edit.capture())
        self.assertEqual(real.calls, [])

    def test_the_newline_and_the_rest_of_the_module_come_through(self):
        real = FakeReal()
        real.extra = "kept"
        app, edit = make(real=real, box=FakeBox(SAMPLE))
        self.assertEqual(edit.NEWLINE, "\r\n")
        self.assertEqual(edit.extra, "kept")


class ObserveTest(unittest.TestCase):
    def test_the_live_step_reads_the_box_as_a_field(self):
        box = FakeBox("I recieved the files yesterday", (28, 28))
        app, edit = make(box=box, step="live", frontmost=True)
        field = edit.observe()
        self.assertIsInstance(field, edit_logic.Field)
        self.assertEqual(field.text, box.text())
        self.assertEqual(field.caret, (28, 28))
        self.assertEqual((field.app, field.window), (os.getpid(), practice.PRACTICE_WINDOW))
        self.assertIs(field.handle, box.target)

    def test_the_other_practice_steps_are_never_checked(self):
        real = FakeReal()
        app, edit = make(real=real, box=FakeBox(SAMPLE), step="rewrite", frontmost=True)
        self.assertIsNone(edit.observe())
        self.assertEqual(real.calls, [])

    def test_observe_passes_through_when_the_tour_is_not_frontmost(self):
        real = FakeReal(answer="field")
        app, edit = make(real=real, box=FakeBox(SAMPLE), step="live", frontmost=False)
        self.assertIs(edit.observe(), "field")
        self.assertEqual(real.calls, [("observe",)])

    def test_observe_stays_home_while_a_live_setting_is_sandboxed(self):
        real = FakeReal()
        app, edit = make(real=real, box=FakeBox(SAMPLE), sandbox=("live_check",))
        self.assertIsNone(edit.observe())
        self.assertEqual(real.calls, [])


class ReplaceTest(unittest.TestCase):
    def test_a_real_context_goes_to_the_real_module(self):
        real = FakeReal()
        ctx = edit_logic.Context("outside", 4242, 99, True, False)
        app, edit = make(real=real, box=FakeBox(SAMPLE), frontmost=True)
        self.assertTrue(edit.replace("new", ctx))
        self.assertEqual(real.calls, [("replace", "new", ctx)])

    def test_a_practice_context_edits_the_box_and_marks_once(self):
        box = FakeBox(SAMPLE, (3, 11))
        app, edit = make(box=box, frontmost=True)
        ctx = edit.capture()
        self.assertTrue(edit.replace("received", ctx))
        self.assertEqual(box.text(), "We received the report yesterday")
        self.assertEqual(box.selection(), (11, 11))
        self.assertEqual(app.marks, ["replace"])

    def test_a_stale_context_is_refused_without_a_mark(self):
        box = FakeBox(SAMPLE, (3, 11))
        app, edit = make(box=box, frontmost=True)
        ctx = edit.capture()
        box._select(0, 2)
        self.assertFalse(edit.replace("received", ctx))
        self.assertEqual(box.text(), SAMPLE)
        self.assertEqual(app.marks, [])


class TypeTest(unittest.TestCase):
    def test_a_practice_context_types_at_the_caret_and_marks(self):
        box = FakeBox("Hello", (5, 5))
        app, edit = make(box=box, frontmost=True)
        ctx = edit.capture()
        self.assertTrue(edit.type_at_caret(" there", ctx, cancelled=lambda: False))
        self.assertEqual(box.text(), "Hello there")
        self.assertEqual(box.selection(), (11, 11))
        self.assertEqual(app.marks, ["type"])

    def test_a_real_context_forwards_the_cancelled_keyword(self):
        real = FakeReal()
        ctx, cancelled = edit_logic.Context("outside", 4242, 99, True, False), lambda: False
        app, edit = make(real=real, box=FakeBox("Hi"), frontmost=False)
        self.assertTrue(edit.type_at_caret("new", ctx, cancelled=cancelled))
        self.assertEqual(real.calls, [("type_at_caret", "new", ctx, {"cancelled": cancelled})])


class UndoTest(unittest.TestCase):
    def test_a_practice_context_swaps_the_inserted_text_back(self):
        box = FakeBox("New words", (9, 9))
        app, edit = make(box=box, frontmost=True)
        ctx = PracticeCtx("old words", os.getpid(), practice.PRACTICE_WINDOW, True, False, field=box, caret=(0, 9), target=box.target)
        self.assertTrue(edit.undo(ctx, "old words", "New words"))
        self.assertEqual(box.text(), "old words")
        self.assertEqual(box.selection(), (9, 9))
        self.assertEqual(app.marks, [])

    def test_a_real_context_on_a_module_without_undo_follows_edit_logic(self):
        real = FakeReal(answer=edit_logic.Context("new", 4242, 99, True, False, caret=(10, 3)))
        ctx = edit_logic.Context("old", 4242, 99, True, False, caret=(10, 3))
        app, edit = make(real=real, box=FakeBox(SAMPLE), frontmost=False)
        self.assertTrue(edit.undo(ctx, "old", "new"))
        self.assertEqual([c[0] for c in real.calls], ["select_back", "capture", "replace"])
        self.assertEqual(real.calls[0][1], 3)

    def test_a_real_module_with_undo_does_it_itself(self):
        real = FakeRealWithUndo()
        ctx = edit_logic.Context("old", 4242, 99, True, False)
        app, edit = make(real=real, box=FakeBox(SAMPLE), frontmost=False)
        self.assertTrue(edit.undo(ctx, "old", "new"))
        self.assertEqual([c[0] for c in real.calls], ["undo"])


class FieldCallTest(unittest.TestCase):
    def test_our_field_bounds_come_from_the_box(self):
        box = FakeBox(SAMPLE)
        field, real = box.field(), FakeReal()
        app, edit = make(real=real, box=box, frontmost=False)  # a field from the box answers wherever the tour is
        spans = [(3, 11, "recieved"), (23, 32, "yesterday")]
        self.assertEqual(edit.bounds(field, spans), [[(0, 0, 64, 24)], [(0, 0, 72, 24)]])
        self.assertEqual(real.calls, [])

    def test_a_foreign_field_goes_to_the_real_module(self):
        field = edit_logic.Field(4242, 99, "Other", "other", SAMPLE, (0, 0), b"", (0, 0, 0, 0), None)
        real = FakeReal(answer="real bounds")
        app, edit = make(real=real, box=FakeBox(SAMPLE), frontmost=True)
        spans = [(0, 2, "We")]
        self.assertIs(edit.bounds(field, spans), real.answer)
        self.assertEqual(real.calls, [("bounds", field, spans)])

    def test_select_range_selects_in_the_box_for_our_field(self):
        box = FakeBox(SAMPLE, (0, 0))
        field, real = box.field(), FakeReal()
        app, edit = make(real=real, box=box, frontmost=False)
        ctx = edit.select_range(field, 3, 11, "recieved")
        self.assertIsInstance(ctx, PracticeCtx)
        self.assertEqual(box.selection(), (3, 11))
        self.assertEqual(ctx.text, "recieved")
        self.assertEqual(ctx.caret, (3, 8))
        self.assertEqual(real.calls, [])

    def test_select_range_for_a_foreign_field_goes_to_the_real_module(self):
        field = edit_logic.Field(4242, 99, "Other", "other", SAMPLE, (0, 0), b"", (0, 0, 0, 0), None)
        real = FakeReal(answer=edit_logic.Context("x", 4242, 99, True, False))
        app, edit = make(real=real, box=FakeBox(SAMPLE), frontmost=True)
        self.assertIs(edit.select_range(field, 0, 2, "We"), real.answer)
        self.assertEqual(real.calls, [("select_range", field, 0, 2, "We")])


class HostCallTest(unittest.TestCase):
    def test_read_selection_answers_from_the_box_when_frontmost(self):
        reads = []

        def read(fallback):
            reads.append(fallback)
            return "outside"

        app, edit = make(box=FakeBox(SAMPLE, (3, 11)), frontmost=True, read=read)
        self.assertEqual(edit.read_selection(True), "recieved")
        self.assertEqual(reads, [])

    def test_read_selection_passes_through_when_not_frontmost(self):
        reads = []

        def read(fallback):
            reads.append(fallback)
            return "outside"

        app, edit = make(box=FakeBox(SAMPLE), frontmost=False, read=read)
        self.assertEqual(edit.read_selection(True), "outside")
        self.assertEqual(reads, [True])

    def test_dictation_target_is_the_box_when_frontmost(self):
        app, edit = make(box=FakeBox(SAMPLE), frontmost=True, target=lambda: (4242, 99))
        self.assertEqual(edit.dictation_target(), (os.getpid(), practice.PRACTICE_WINDOW))

    def test_dictation_target_passes_through_or_stays_none(self):
        app, edit = make(box=FakeBox(SAMPLE), frontmost=False, target=lambda: (4242, 99))
        self.assertEqual(edit.dictation_target(), (4242, 99))
        app, edit = make(box=FakeBox(SAMPLE), frontmost=False)
        self.assertIsNone(edit.dictation_target())


class CollapseTest(unittest.TestCase):
    def test_the_frontmost_box_collapses(self):
        box = FakeBox(SAMPLE, (3, 11))
        app, edit = make(box=box, frontmost=True)
        edit.collapse()
        self.assertEqual(box.selection(), (11, 11))

    def test_without_the_box_it_collapses_the_real_module_when_it_can(self):
        real = FakeRealWithUndo()
        app, edit = make(real=real, box=FakeBox(SAMPLE), frontmost=False)
        edit.collapse()
        self.assertEqual([c[0] for c in real.calls], ["collapse"])
        bare = FakeReal()
        app, edit = make(real=bare, box=FakeBox(SAMPLE), frontmost=False)
        edit.collapse()
        self.assertEqual(bare.calls, [])


class TargetLifecycleTest(unittest.TestCase):
    def test_cancelled_dictation_neither_types_nor_marks(self):
        box = FakeBox("Hello", (5, 5))
        app, edit = make(box=box, frontmost=True)
        self.assertFalse(edit.type_at_caret(" lost", edit.capture(), cancelled=lambda: True))
        self.assertEqual((box.text(), app.marks), ("Hello", []))

    def test_old_context_and_live_snapshot_reject_identical_replacement_or_reset(self):
        for reused in (False, True):
            with self.subTest(reused=reused):
                box = FakeBox(SAMPLE, (3, 11))
                app, edit = make(box=box, step="live", frontmost=True)
                ctx, field = edit.capture(), edit.observe()
                if reused:
                    box.reset_target()
                else:
                    app.box = FakeBox(SAMPLE, (3, 11))
                self.assertIsNot(edit.observe().handle, field.handle)
                self.assertFalse(edit.same_context(ctx))
                self.assertFalse(edit.replace("received", ctx))
                self.assertFalse(edit.type_at_caret("lost", ctx))
                self.assertFalse(edit.select_back(3, ctx))
                self.assertFalse(edit.undo(ctx, "old", "recieved"))
                self.assertIsNone(edit.bounds(field, [(3, 11, "recieved")]))
                self.assertIsNone(edit.select_range(field, 3, 11, "recieved"))
                self.assertEqual((app.box.text(), app.marks), (SAMPLE, []))

    def test_main_thread_revalidates_queued_capture_observe_and_write(self):
        for operation in ("capture", "observe", "replace", "type_at_caret"):
            with self.subTest(operation=operation):
                box = FakeBox(SAMPLE, (3, 11))
                app, edit = make(box=box, step="live", frontmost=True)
                ctx = edit.capture()
                def delayed(fn, default=None):
                    box.reset_target()
                    return fn()
                app.on_main = delayed
                result = getattr(edit, operation)("received", ctx) if operation in ("replace", "type_at_caret") else getattr(edit, operation)()
                self.assertFalse(result)
                self.assertEqual((box.text(), app.marks), (SAMPLE, []))


class BoxTest(unittest.TestCase):
    def test_bounds_places_each_matching_span(self):
        box = FakeBox(SAMPLE, (0, 0))
        field = box.field()
        self.assertEqual(box.bounds(field, [(3, 11, "recieved"), (23, 32, "yesterday")]),
                         [[(0, 0, 64, 24)], [(0, 0, 72, 24)]])

    def test_bounds_gives_none_for_only_the_span_that_no_longer_matches(self):
        box = FakeBox(SAMPLE, (0, 0))
        field = box.field()
        self.assertEqual(box.bounds(field, [(3, 11, "recieved"), (23, 32, "tomorrow")]),
                         [[(0, 0, 64, 24)], None])

    def test_bounds_gives_none_when_the_whole_text_changed(self):
        box = FakeBox(SAMPLE, (0, 0))
        field = box.field()
        box.text_value = SAMPLE + " now"
        self.assertIsNone(box.bounds(field, [(3, 11, "recieved")]))

    def test_select_range_counts_utf16_units_past_an_emoji(self):
        box = FakeBox("\U0001F600 recieved", (0, 0))
        ctx = box.select_range(3, 11, "recieved")
        self.assertEqual(box.selection(), (3, 11))
        self.assertEqual(ctx.text, "recieved")
        self.assertEqual(live.text_at(box.text(), 3, 11), "recieved")


if __name__ == "__main__":
    unittest.main()
