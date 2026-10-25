"""The Get Started tour's Practice mixin: the adapter install, the guards, and the canned results."""
import queue
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness_discussion import app_live, app_practice, app_summarize, app_writer, dictation, practice, tour_copy, writer
from harness_discussion import live as live_mod
from harness_discussion.edit_logic import Context

from tests.dictation_helpers import Devices, DictationCase, RECORDER
from tests.test_app_live import FakeField, FakeMarks, Toast, window
from tests.test_app_writer import FakeRecorder, Source
from tests.test_practice import FakeBox


def make_tour(box, frontmost):
    """The TourWindow stub T4 replaces: alive, its box, frontmost, and where clicks land."""
    return SimpleNamespace(alive=True, box=box, frontmost=frontmost, hit=lambda x, y: "box")


class Base:
    """The core.App members the composition lacks; _read and play record what reached them."""

    def _init_base(self):
        self.reads, self.plays = [], []

    def _read(self, seq, x, y):
        self.reads.append((seq, x, y))
        self.host.read_selection(self.cfg["clipboard_fallback"])

    def play(self, text, lang_tag, only=None, start=0):
        self.plays.append((text, lang_tag, only, start))


class _App(app_practice.Practice, app_live.Live, app_summarize.Summaries, app_writer.Writer, Base):
    def __init__(self, box=None, step="rewrite", frontmost=True):
        self.events = queue.Queue()
        self.box_ = box if box is not None else FakeBox()
        self.frontmost_value = frontmost
        self.tour = make_tour(self.box_, lambda: self.frontmost_value)
        self.step, self.tour_marks, self.tour_samples, self.sandbox = step, [], [], set()
        self.writer_source, self.toast_, self.marks_, self.review_ = None, Toast(), FakeMarks(), window()
        self.field_ = FakeField()
        self.host = SimpleNamespace(
            edit=self.field_, make_toast=lambda: self.toast_, make_review=lambda: self.review_,
            make_marks=lambda: self.marks_,
            esc_watcher=lambda fn: SimpleNamespace(start=lambda: None, stop=lambda: None),
            pointer=lambda: (10, 10), after=lambda ms, fn: None, copy_text=lambda text, **kw: True,
            set_shortcut=lambda *a: None, mic_permission=lambda then: then(True),
            open_mic_settings=lambda: None, read_selection=lambda fallback: "outside text",
            dictation_target=lambda: (1, 2))
        self.cfg = {"writer": True, "live_check": True, "live_off_apps": [],
                    "writer_sources": [["Cursor", True]], "writer_models": {}, "modes": [],
                    "default_mode": "Fix errors", "clipboard_fallback": True, "stt_sources": [],
                    "dictate_polish": False, "dictate_mode": "Clean dictation", "dictate_microphone": ""}
        self.popup = SimpleNamespace(visible=False, set_rewriting=lambda on: None, set_last_mode=lambda m: None,
                                     hide=lambda: None, contains=lambda x, y: False)
        self.settings_win, self.summary_server, self._summary_gen = None, SimpleNamespace(stop=lambda: None), 0
        self._init_base()
        self._init_writer()
        self.source = Source("Cursor")
        self.writer_sources = [self.source]
        self._live_stop.set()  # the tests drive polls themselves

    # the Tour members T4 adds; the marks and samples record what the mixin reports

    def tour_step(self):
        return self.step

    def tour_mark(self, step):
        self.tour_marks.append(step)

    def tour_sample(self, step):
        self.tour_samples.append(step)

    def sandboxed(self, *keys):
        return any(k in self.sandbox for k in keys)

    def tour_writer_source(self):
        return self.writer_source

    def change(self, key, value, save=True):
        self.cfg[key] = value

    def _arm_summary_stop(self):
        pass

    def drain(self, until=None):
        while True:
            kind, *args = self.events.get(timeout=5)
            if kind in ("recording_done", "dictated"):
                owner = self._recorder if kind == "recording_done" else self._dictation_job
                identity = (owner.generation, owner.binding) if owner is not None else None
                incoming = tuple(args[:2]) if kind == "recording_done" else tuple(args[1:3])
                if owner is not None and incoming == identity:
                    owner.thread.join(timeout=5)
                    assert not owner.thread.is_alive(), "worker failed to finish"
            if kind == "call":
                args[0]()
            elif kind in self.LIVE_EVENTS:
                getattr(self, self.LIVE_EVENTS[kind])(*args)
            else:
                getattr(self, self.WRITER_EVENTS[kind])(*args)
            if kind == until or (until is None and kind in ("rewrite", "dictated")):
                return

    def pump(self):
        while not self.events.empty():
            kind, *args = self.events.get_nowait()
            if kind == "call":
                args[0]()
            elif kind in self.LIVE_EVENTS:
                getattr(self, self.LIVE_EVENTS[kind])(*args)
            elif kind in self.WRITER_EVENTS:
                getattr(self, self.WRITER_EVENTS[kind])(*args)


class PracticeMixinTest(unittest.TestCase):
    def setUp(self):
        self.app = _App()
        self.box = self.app.box_

    def practice_ctx(self, text=tour_copy.REWRITE):
        self.box.text_value, self.box.sel = text, (0, live_mod.units(text))
        return self.box.context()


class InitTest(PracticeMixinTest):
    def test_init_wraps_the_edit_module_and_the_host_reads(self):
        self.assertIsInstance(self.app.edit, practice.PracticeEdit)
        self.assertIs(self.app.edit.real, self.app.field_)
        self.assertEqual(self.app.host.read_selection, self.app.edit.read_selection)
        self.assertEqual(self.app.host.dictation_target, self.app.edit.dictation_target)

    def test_with_the_tour_closed_capture_reaches_the_real_module(self):
        seen, real = [], self.app.field_.capture
        self.app.tour.alive = False
        self.app.field_.capture = lambda fallback=False: seen.append(fallback) or real(fallback)
        self.app.edit.capture(False)
        self.assertEqual(seen, [False])


class RewriteSampleTest(PracticeMixinTest):
    def error(self, result=writer.Error("No writing source is ready.")):
        ctx, rid = self.practice_ctx(), self.app._rewrite_id
        self.app._rewrite_done(rid, ctx, "Fix errors", result, None, (10, 10, None))
        return ctx

    def test_a_failed_rewrite_of_a_practice_context_fixes_the_sample(self):
        ctx = self.error()
        self.assertEqual(self.box.text(), tour_copy.sample_fix(tour_copy.REWRITE))
        self.assertEqual(self.app.toast_.shown[-1][0], "Fix errors · Sample")
        self.assertEqual(self.app.tour_marks, ["rewrite"])
        self.assertEqual(self.app.tour_samples, ["rewrite"])
        self.assertEqual(self.app._last_edit[0], ctx)

    def test_a_cancelled_rewrite_hides_the_toast_and_touches_nothing(self):
        self.app._toast("Rewriting · Fix errors", busy=True)
        self.error(writer.Cancelled("Cancelled."))
        self.assertEqual(self.box.text(), tour_copy.REWRITE)
        self.assertFalse(self.app.toast_.visible)
        self.assertEqual((self.app.tour_marks, self.app.tour_samples), ([], []))

    def test_a_real_context_keeps_the_existing_error_toast(self):
        self.practice_ctx()
        ctx = Context("outside", 4242, 99, True, False)
        self.app._rewrite_done(self.app._rewrite_id, ctx, "Fix errors",
                               writer.Error("No writing source is ready."), None, (10, 10, None))
        self.assertEqual(self.app.toast_.shown[-1][0], "No writing source is ready.")
        self.assertEqual((self.app.tour_marks, self.app.tour_samples, self.box.text()), ([], [], tour_copy.REWRITE))

    def test_a_stale_rewrite_result_records_no_sample(self):
        ctx = self.practice_ctx()
        self.app._rewrite_done(self.app._rewrite_id + 1, ctx, "Fix errors",
                                writer.Error("No writing source is ready."), None, (10, 10, None))
        self.assertEqual((self.app.tour_marks, self.app.tour_samples, self.box.text()), ([], [], tour_copy.REWRITE))


class LiveSeedTest(PracticeMixinTest):
    def setUp(self):
        super().setUp()
        self.app.step, self.app.tour.alive = "live", True
        self.mode = self.app._find_mode(None)

    def test_a_finished_check_without_a_source_seeds_the_sample_line(self):
        self.app._live_check_done()
        self.assertEqual(self.app.live_cache.get(tour_copy.LIVE, self.mode), tour_copy.sample_fix(tour_copy.LIVE))
        self.assertEqual(self.app.tour_samples, ["live"])

    def test_a_ready_source_or_another_step_seeds_nothing(self):
        self.app.writer_source = "Cursor"  # a cancelled check with a ready source
        self.app._live_check_done()
        self.assertFalse(self.app.live_cache.has(tour_copy.LIVE, self.mode))
        self.assertEqual(self.app.tour_samples, [])
        self.app.writer_source, self.app.step = None, "rewrite"
        self.app._live_check_done()
        self.app.tour.alive = False
        self.app._live_check_done()
        self.assertFalse(self.app.live_cache.has(tour_copy.LIVE, self.mode))
        self.assertEqual(self.app.tour_samples, [])


class TurnOffTest(PracticeMixinTest):
    def test_turning_it_off_in_the_practice_box_saves_no_app_name(self):
        self.app.cfg["live_off_apps"] = []
        self.app._live_turn_off(self.box.field())
        self.assertEqual(self.app.cfg["live_off_apps"], [])
        self.assertFalse(self.app.toast_.visible)
        self.assertIsNone(self.app.marks_.shown)

    def test_a_real_field_still_learns_its_app_name(self):
        field = FakeField().field()
        self.app._live_turn_off(field)
        self.assertEqual(self.app.cfg["live_off_apps"], ["notepad.exe"])


class ReadGuardTest(PracticeMixinTest):
    def setUp(self):
        super().setUp()
        self.app.step = "read"
        self.reads = []
        adapter = self.app.host.read_selection
        self.app.host.read_selection = lambda fallback: self.reads.append(fallback) or adapter(fallback)

    def test_a_click_on_the_tour_window_is_not_a_selection(self):
        self.app.tour.hit = lambda x, y: "window"
        self.app._read(1, 10, 20)
        self.assertEqual(self.reads, [])
        self.assertEqual(self.app.reads, [])

    def test_a_click_on_the_box_still_reads(self):
        self.app._read(1, 10, 20)
        self.assertEqual(self.reads, [True])
        self.assertEqual(self.app.reads, [(1, 10, 20)])


class DictateGuardTest(PracticeMixinTest):
    def setUp(self):
        super().setUp()
        self.app.step = "dictate"

    def test_a_sandboxed_writer_with_the_tour_in_the_back_starts_no_recording(self):
        self.app.sandbox, self.app.frontmost_value = {"writer"}, False
        self.app._dictate_down()
        self.assertIsNone(self.app._recorder)
        self.assertEqual(self.app.toast_.shown, [])

    def test_a_sandboxed_writer_with_the_box_frontmost_still_starts(self):
        self.app.sandbox = {"writer"}
        with patch.object(app_writer.mic, "Recorder", FakeRecorder):
            self.app._dictate_down()
            self.assertIsNotNone(self.app._recorder)
            self.app._cancel_dictation()
            self.app.drain("recording_done")


class MarkTest(PracticeMixinTest):
    def test_play_on_the_read_step_marks_read(self):
        self.app.step = "read"
        self.app.play("Some text", "en-US")
        self.assertEqual(self.app.plays, [("Some text", "en-US", None, 0)])
        self.assertEqual(self.app.tour_marks, ["read"])

    def test_play_on_another_step_marks_nothing(self):
        self.app.step = "voice"
        self.app.play("Some text", "en-US")
        self.assertEqual(self.app.tour_marks, [])

    def test_opening_the_review_after_a_practice_edit_marks_review(self):
        for ctx, marked in ((self.practice_ctx(), ["review"]), (Context("outside", 4242, 99, True, False), [])):
            with self.subTest(practice=isinstance(ctx, practice.PracticeCtx)):
                self.app.tour_marks, self.app._last_edit = [], (ctx, "old", "new", "Fix errors")
                self.app.open_review()
                self.assertEqual(self.app.tour_marks, marked)
                self.assertTrue(self.app.review_.shown)


class OnMainTest(PracticeMixinTest):
    def pump_once(self):
        for _ in range(500):
            if not self.app.events.empty():
                break
            time.sleep(0.01)
        self.app.pump()

    def test_a_worker_waits_for_the_main_thread(self):
        out = []

        def work():
            out.append(self.app.on_main(lambda: "value", "default"))

        t = threading.Thread(target=work)
        t.start()
        self.pump_once()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(out, ["value"])

    def test_a_worker_times_out_and_returns_the_default(self):
        out = []
        with patch.object(practice, "HOP_S", 0.05):
            t = threading.Thread(target=lambda: out.append(self.app.on_main(lambda: "value", "default")))
            t.start()
            t.join(5)
        self.assertEqual(out, ["default"])
        self.app.pump()  # the late call still runs
        self.assertTrue(self.app.events.empty())

    def test_a_failed_read_returns_the_default_and_the_next_pump_raises_nothing(self):
        out = []

        def boom():
            raise RuntimeError("private box details")

        with patch.object(practice, "HOP_S", 0.05):
            t = threading.Thread(target=lambda: out.append(self.app.on_main(boom, "default")))
            t.start()
            t.join(5)
        self.assertEqual(out, ["default"])
        self.app.pump()
        self.assertTrue(self.app.events.empty())

    def test_on_the_main_thread_it_queues_no_event(self):
        self.assertEqual(self.app.on_main(lambda: "value", "default"), "value")
        self.assertTrue(self.app.events.empty())
        self.assertEqual(self.app.on_main(lambda: 1 / 0, "fallback"), "fallback")


class ApplyAllTest(PracticeMixinTest):
    def test_the_shortcut_applies_the_seeded_line_and_marks_live(self):
        self.app.step = "live"
        end = live_mod.units(tour_copy.LIVE)
        self.box.text_value, self.box.sel = tour_copy.LIVE, (end, end)
        mode = self.app._find_mode(None)
        self.app.live_cache.put(tour_copy.LIVE, mode, tour_copy.sample_fix(tour_copy.LIVE))
        self.app.rewrite_selection()
        self.assertEqual(self.box.text(), tour_copy.sample_fix(tour_copy.LIVE))
        self.assertEqual(self.box.selection(), (live_mod.units(tour_copy.sample_fix(tour_copy.LIVE)),) * 2)
        self.assertEqual(self.app.tour_marks, ["live"])
        self.assertEqual(self.app.toast_.shown[-1][0], "Applied")


class SampleDictationTest(DictationCase):
    """A hold that fails to transcribe still types the canned line into the practice box."""

    def make_app(self, blocked=None):
        app = _App(step="dictate")
        app.stt_sources = [SimpleNamespace(name="Local", status=Mock(return_value="ready"),
                                           transcribe=Mock(side_effect=dictation.Error("No speech source is ready.")))]
        devices = Devices(blocked)
        patcher = patch.object(app_writer.mic, "Recorder", lambda device="": RECORDER(devices.factory, device=device))
        patcher.start()
        timers = []
        app.host.after = lambda ms, fn: timers.append((ms, fn))
        app.deadlines = timers
        self.addCleanup(patcher.stop)
        self.addCleanup(app._close_writer)
        self.addCleanup(devices.release.set)
        return app, devices

    def test_a_failed_transcription_types_the_sample_and_marks_dictate(self):
        app, devices = self.make_app()
        self.start(app)
        self.finish(app)
        self.assertEqual(app.box_.text(), tour_copy.DICTATED)
        self.assertEqual(app.tour_marks, ["dictate"])
        self.assertEqual(app.tour_samples, ["dictate"])


if __name__ == "__main__":
    unittest.main()