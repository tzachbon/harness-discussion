"""The Writer flow with a fake host, editor, and sources: paste guards, toast actions, undo, review, Esc, hotkey."""
import queue
import unittest
import unittest.mock
from types import SimpleNamespace

from harness_discussion import app_summarize, app_writer, writer
from harness_discussion.edit_logic import Context

CTX = Context("hi there ", app=1, window=2, editable=True, secure=False)


class FakeEdit:
    NEWLINE = "\n"

    def __init__(self, ctx=CTX):
        self.ctx, self.same, self.pasted, self.log, self.ctxs = ctx, True, [], [], []

    def capture(self, clipboard_fallback):
        return self.ctx

    def same_context(self, ctx):
        return self.same

    def replace(self, text, ctx):
        self.pasted.append(text)
        self.ctxs.append(ctx)
        self.ctx = Context("", self.ctx.app, self.ctx.window, True, False)
        return True

    def select_back(self, n, ctx):
        self.log.append(("select_back", n))
        self.ctx = Context(self.pasted[-1], self.ctx.app, self.ctx.window, True, False)
        return True

    def collapse(self):
        self.log.append(("collapse",))


class FakeToast:
    visible = False

    def __init__(self):
        self.shown, self.on_hide = [], lambda: None
        self.levels = []

    def show(self, message, x, y, actions=(), busy=False, above=None):
        self.visible = True
        self.at = (x, y, above)
        self.shown.append((message, dict(actions), busy))

    def hide(self):
        self.visible = False
        self.on_hide()

    def set_input_level(self, level):
        self.levels.append(level)

    def action(self, title):
        return self.shown[-1][1][title]


class Source:
    def __init__(self, name, reply="Hi there.", status="ready"):
        self.name, self.reply, self._status, self.texts, self.prompts = name, reply, status, [], []

    def status(self):
        return self._status

    def rewrite(self, system, text, model, effort, cancelled):
        self.texts.append(text)
        self.prompts.append(system)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class _App(app_summarize.Summaries, app_writer.Writer):
    def __init__(self, sources=None):
        self.events, self.timers, self.copied, self.picks = queue.Queue(), [], [], []
        self.mic_ok = True
        self.toast_ = FakeToast()
        self.review_ = SimpleNamespace(shown=[], show=lambda *a: self.review_.shown.append(a))
        self.esc = SimpleNamespace(on=False, start=lambda: setattr(self.esc, "on", True),
                                   stop=lambda: setattr(self.esc, "on", False))
        self.host = SimpleNamespace(
            mic_permission=lambda then: then(self.mic_ok), open_mic_settings=lambda: None,
            edit=FakeEdit(), make_toast=lambda: self.toast_, make_review=lambda: self.review_,
            esc_watcher=lambda fn: self.esc, pointer=lambda: (10, 10), after=lambda ms, fn: self.timers.append(fn),
            copy_text=lambda text, **kw: self.copied.append(text) or True, set_shortcut=lambda *a, **kw: None,
            pick_mode=lambda names, current, pick: self.picks.append((names, current, pick)))
        self.host.dictation_target = lambda: ((self.host.edit.ctx.app, self.host.edit.ctx.window)
                                               if self.host.edit.ctx is not None else None)
        self.cfg = {"writer": True, "writer_sources": [["Cursor", True], ["Codex", True], ["Local", True]],
                    "stt_sources": [["Codex", True], ["Cursor", True]],
                    "writer_models": {}, "modes": [], "default_mode": "Fix errors", "clipboard_fallback": True,
                    "dictate_polish": True, "dictate_mode": "Clean dictation"}
        self.popup = SimpleNamespace(visible=False, hidden=0, rewriting=False, mode=None,
                                     set_rewriting=lambda on: setattr(self.popup, "rewriting", on),
                                     set_last_mode=lambda m: setattr(self.popup, "mode", m),
                                     hide=lambda: setattr(self.popup, "hidden", self.popup.hidden + 1))
        self.summary_server = SimpleNamespace(stop=lambda: None)
        self._summary_gen = 0
        self._init_writer()
        self.writer_sources = sources or [Source("Cursor")]

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
            else:
                getattr(self, self.WRITER_EVENTS[kind])(*args)
            if kind == until or (until is None and kind in ("rewrite", "dictated")):
                return


    def deliver(self, rid, mode, result, at, timing):
        session = app_writer._Recording(self._dictate_generation, self._dictate_binding, None,
                                       cancel=self._rewrite_cancel, rid=rid,
                                       thread=SimpleNamespace(is_alive=lambda: False))
        self._dictation_job = session
        self._dictated(rid, session.generation, session.binding, mode, result, at, timing)


class FakeRecorder:
    duration = 2.0

    def __init__(self, audio=b"RIFFwav", device=""):
        self.audio, self.started, self.device = audio, False, device

    def start(self):
        self.started = True

    def level(self):
        return 0.5

    def stop(self):
        return self.audio


class DictationTest(unittest.TestCase):
    def setUp(self):
        FakeRecorder.duration = 2.0
        self.app = _App()
        self.addCleanup(self.app._close_writer)
        self.app.host.edit.type_at_caret = lambda text, ctx, **kw: self.app.host.edit.pasted.append(text) or self.app.host.edit.ctxs.append(ctx) or True
        self.app.stt_sources = [SimpleNamespace(name="Codex", status=lambda: "ready",
                                                transcribe=lambda audio, language="": "i thinks so")]
        patcher = unittest.mock.patch.object(app_writer.mic, "Recorder", FakeRecorder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def hold(self, seconds):
        FakeRecorder.duration = seconds
        self.app._dictate_down()
        self.app.drain("recording_ready")
        self.assertEqual(self.app.toast_.shown[-1][0], "Listening")
        self.app._dictate_up()
        self.app.drain("recording_done")

    def test_old_permission_answers_cannot_start_a_new_hold_or_change_its_target(self):
        answers = []
        self.app.host.mic_permission = answers.append
        self.app._dictate_down()
        self.assertTrue(self.app.esc.on)
        self.app._dictate_up()
        self.app.drain("recording_done")
        self.app.host.edit.ctx = Context("", 3, 4, True, False)
        self.app._dictate_down()
        answers[0](True)
        answers[1](True)
        self.app.drain("recording_ready")
        self.app._dictate_up()
        self.app.drain("recording_done")
        self.assertEqual(self.app._dictate_ctx.app, 3)
        self.app.drain()

    def test_escape_and_shutdown_invalidate_pending_permission(self):
        for cancel in ("_escape", "_close_writer"):
            with self.subTest(cancel=cancel):
                app = _App()
                answers = []
                app.host.mic_permission = answers.append
                app._dictate_down()
                getattr(app, cancel)()
                answers[0](True)
                app.drain("recording_done")
                self.assertIsNone(app._recorder)
                self.assertFalse(app._holding)

    def test_failed_recording_stop_is_recoverable_on_release_escape_and_shutdown(self):
        for cancel in ("_dictate_up", "_escape", "_close_writer"):
            with self.subTest(cancel=cancel):
                app = _App()
                with unittest.mock.patch.object(FakeRecorder, "stop", side_effect=RuntimeError("private device details")) as stop:
                    app._dictate_down()
                    app.drain("recording_ready")
                    getattr(app, cancel)()
                    app.drain("recording_done")
                stop.assert_called_once_with()
                self.assertIsNone(app._recorder)
                self.assertFalse(app._holding)
                self.assertNotIn("private device details", repr(app.toast_.shown))

    def test_hold_transcribes_rewrites_and_types_at_the_caret(self):
        self.hold(2)
        self.app.drain()
        self.assertEqual(self.app.host.edit.pasted, ["Hi there."])
        self.assertEqual(self.app.host.edit.ctxs, [CTX])  # typed only into the field captured at key down
        self.assertEqual(self.app.writer_sources[0].texts, [writer.wrap("i thinks so")])
        self.assertFalse(self.app.toast_.visible)
        self.assertEqual(self.app.copied, [])

    def test_dictation_uses_its_custom_prompt_independently_of_the_writing_default(self):
        self.app.cfg.update(default_mode="Email", dictate_mode="My dictation",
                            modes=[{"name": "My dictation", "instructions": "Turn spoken steps into a numbered list."}])
        self.hold(2)
        self.app.drain()
        mode = writer.find_mode("My dictation", writer.all_modes(self.app.cfg["modes"]))
        self.assertEqual(self.app.writer_sources[0].prompts, [writer.system_prompt(mode)])
        self.assertFalse(self.app.toast_.visible)
        self.assertEqual(self.app.copied, [])
        self.assertEqual(self.app.cfg["default_mode"], "Email")

    def test_missing_dictation_mode_uses_clean_dictation_instead_of_the_writing_default(self):
        self.app.cfg.update(default_mode="Email", dictate_mode="Deleted")
        self.hold(2)
        self.app.drain()
        self.assertFalse(self.app.toast_.visible)
        self.assertEqual(self.app.copied, [])

    def test_spoken_steps_are_passed_to_cleanup_and_the_numbered_reply_is_typed_intact(self):
        transcript = "Um, first check the logs, second restart the app, fourth, no, third notify the team."
        numbered = "1. Check the logs.\n2. Restart the app.\n3. Notify the team."
        self.app.stt_sources[0].transcribe = lambda audio, language="": transcript
        self.app.writer_sources[0].reply = numbered
        self.hold(2)
        self.app.drain()
        self.assertEqual(self.app.writer_sources[0].texts, [writer.wrap(transcript)])
        self.assertEqual(self.app.host.edit.pasted, [numbered])
        prompt = self.app.writer_sources[0].prompts[0]
        self.assertIn("number the actual items consecutively", prompt)

    def test_writing_still_uses_its_own_default(self):
        self.app.cfg.update(default_mode="Email", dictate_mode="Clean dictation")
        self.app.rewrite_selection()
        self.app.drain()
        self.assertEqual(self.app.writer_sources[0].prompts,
                         [writer.system_prompt(writer.find_mode("Email", writer.BUILTIN_MODES))])

    def test_cancel_on_the_transcribing_toast_types_nothing(self):
        self.hold(2)
        self.assertEqual(self.app.toast_.shown[-1][0], "Transcribing")
        self.app.toast_.action("×")()
        self.assertFalse(self.app.rewriting)
        self.app.drain()
        self.assertEqual(self.app.host.edit.pasted, [])
        self.assertFalse(self.app.toast_.visible)

    def test_each_dictation_logs_its_timings_but_never_its_text(self):
        with self.assertLogs("harness_discussion", level="INFO") as logs:
            self.hold(2)
            self.app.drain()
        out = "\n".join(logs.output)
        self.assertRegex(out, r"dictated 9 chars with Codex then Cursor in \d+\.\ds "
                              r"\(transcribe \d+\.\ds, rewrite \d+\.\ds\)")
        self.assertNotIn("thinks", out)
        self.assertNotIn("Hi there", out)

    def test_with_polish_off_the_raw_transcript_is_typed_without_a_rewrite(self):
        self.app.cfg["dictate_polish"] = False
        with self.assertLogs("harness_discussion", level="INFO") as logs:
            self.hold(2)
            self.app.drain()
        self.assertEqual(self.app.host.edit.pasted, ["i thinks so"])
        self.assertEqual(self.app.writer_sources[0].texts, [])
        self.assertFalse(self.app.toast_.visible)
        self.assertEqual(self.app.copied, [])
        self.assertIn("rewrite -)", "\n".join(logs.output))

    def test_a_failed_rewrite_logs_both_timings_and_the_transcript_source(self):
        self.app.writer_sources[0].reply = writer.Error("Cursor failed.")
        with self.assertLogs("harness_discussion", level="INFO") as logs:
            self.hold(2)
            self.app.drain()
        self.assertRegex("\n".join(logs.output),
                         r"dictation failed: \w+ after \d+\.\ds \(transcribe \d+\.\ds with Codex, rewrite \d+\.\ds\)")

    def test_the_timings_are_logged_even_when_the_window_changed(self):
        with self.assertLogs("harness_discussion", level="INFO") as logs:
            self.hold(2)
            self.app.host.edit.ctx = None
            self.app.drain()
        self.assertEqual(self.app.host.edit.pasted, [])
        self.assertRegex("\n".join(logs.output), r"dictated 9 chars with Codex then Cursor in \d+\.\ds")

    def test_dictation_warms_only_after_ready_and_skips_writer_when_polish_is_off(self):
        calls = []
        self.app.writer_sources[0].prewarm = calls.append
        self.app.cfg["writer_models"] = {"Cursor": {"model": "fast"}}
        for polish in (True, False):
            self.app.cfg["dictate_polish"] = polish
            self.app._dictate_down()
            self.assertEqual(calls, [] if polish else ["fast"])
            with unittest.mock.patch.object(app_writer.threading, "Thread",
                    lambda target, args, daemon: SimpleNamespace(start=lambda: target(*args))):
                self.app.drain("recording_ready")
            self.assertEqual(calls, ["fast"])
            self.app._dictate_up()
            self.app.drain("recording_done")
            self.app.drain()

    def test_a_tap_cancels(self):
        self.hold(0.1)
        self.assertIsNone(self.app._recorder)
        self.assertIsNone(self.app._dictation_job)
        self.assertFalse(self.app.toast_.visible)

    def test_no_microphone_permission_offers_settings(self):
        self.app.mic_ok = False
        self.app._dictate_down()
        self.app.drain("recording_done")
        self.assertEqual(sorted(self.app.toast_.shown[-1][1]), ["Open Settings"])
        self.assertIsNone(self.app._recorder)

    def test_a_window_change_offers_copy_instead_of_typing(self):
        FakeRecorder.duration = 2
        self.app._dictate_down()
        self.app.drain("recording_ready")
        self.app.host.edit.ctx = Context("", 9, 9, True, False)
        self.app._dictate_up()
        self.app.drain("recording_done")
        self.app.drain()
        self.assertEqual(self.app.host.edit.pasted, [])
        self.assertEqual(self.app.toast_.shown[-1][0], "Copied to clipboard")
        self.assertEqual(self.app.copied, ["Hi there."])


    def test_missing_input_automatically_copies_the_dictation(self):
        app = self.app
        app._dictate_ctx = None
        app.host.edit.ctx = None
        app.host.edit.type_at_caret = unittest.mock.Mock()
        app.deliver(app._rewrite_id, None, ("hello", "Local", None), (10, 10, None), (0, .1, None, "Local"))
        self.assertEqual(app.copied, ["hello"])
        app.host.edit.type_at_caret.assert_not_called()
        self.assertEqual(app.toast_.shown[-1][0], "Copied to clipboard")
        self.assertEqual(app.toast_.shown[-1][1], {})

    def test_changed_or_secure_input_copies_without_requesting_paste(self):
        for current in (None, Context("", 9, 9, True, False), Context("", 1, 2, True, True)):
            with self.subTest(current=current):
                app = _App()
                app._dictate_ctx = CTX
                app.host.edit.ctx = current
                app.host.edit.type_at_caret = unittest.mock.Mock()
                app.deliver(app._rewrite_id, None, ("hello", "Local", None), (10, 10, None),
                              (0, .1, None, "Local"))
                self.assertEqual(app.copied, ["hello"])
                app.host.edit.type_at_caret.assert_not_called()

    def test_copy_failure_is_not_reported_as_success_and_can_be_retried(self):
        app = self.app
        app.host.copy_text = unittest.mock.Mock(return_value=False)
        app.deliver(app._rewrite_id, None, ("hello", "Local", None), (10, 10, None), (0, .1, None, "Local"))
        self.assertEqual(app.toast_.shown[-1][0], "Could not copy the dictation.")
        app.host.copy_text.assert_called_once_with("hello", cancelled=unittest.mock.ANY)
        app.toast_.action("Copy")()
        self.assertEqual(app.host.copy_text.call_count, 2)

    def test_cancellation_during_copy_suppresses_feedback_even_if_host_returns_success(self):
        app = self.app
        app._dictate_ctx = None
        def copy(text, *, cancelled):
            app._native_escape()
            self.assertTrue(cancelled())
            return True
        app.host.copy_text = unittest.mock.Mock(side_effect=copy)
        app.deliver(app._rewrite_id, None, ("hello", "Local", None), (10, 10, None), (0, .1, None, "Local"))
        self.assertFalse(app.toast_.visible)
        self.assertEqual(app.toast_.shown, [])
        app.host.copy_text.assert_called_once()

    def test_copy_exception_offers_an_explicit_retry_without_automatic_retry(self):
        app = self.app
        app._dictate_ctx = None
        app.host.copy_text = unittest.mock.Mock(side_effect=OSError("private clipboard details"))
        app.deliver(app._rewrite_id, None, ("hello", "Local", None), (10, 10, None), (0, .1, None, "Local"))
        self.assertEqual(app.toast_.shown[-1][0], "Could not copy the dictation.")
        app.host.copy_text.assert_called_once()
        self.assertNotIn("private", repr(app.toast_.shown))
        app.host.copy_text.side_effect = None
        app.toast_.action("Copy")()
        app.host.copy_text.assert_called_with("hello")

    def test_canceled_stale_or_failed_results_never_copy(self):
        for stale, result in ((True, ("hello", "Local", None)),
                              (False, writer.Cancelled("Canceled")), (False, writer.Error("Failed"))):
            with self.subTest(stale=stale, result=result):
                app = _App()
                app.deliver(app._rewrite_id - int(stale), None, result, (10, 10, None), (0, .1, None, "Local"))
                self.assertEqual(app.copied, [])

    def test_recording_x_cancels_and_its_stale_callback_cannot_cancel_a_new_hold(self):
        self.app._dictate_down()
        self.app.drain("recording_ready")
        cancel = self.app.toast_.action("×")
        cancel()
        self.app.drain("recording_done")
        self.assertIsNone(self.app._recorder)
        self.assertFalse(self.app.toast_.visible)
        self.app._dictate_down()
        self.app.drain("recording_ready")
        recorder = self.app._recorder
        cancel()
        self.assertIs(self.app._recorder, recorder)
        self.app._cancel_dictation()
        self.app.drain("recording_done")

    def test_consecutive_recording_x_clicks_are_not_debounced(self):
        clock = [10.0]
        with unittest.mock.patch.object(app_writer.time, "monotonic", side_effect=lambda: clock[0]):
            for now in (10.0, 10.1):
                clock[0] = now
                self.app._dictate_down()
                self.app.drain("recording_ready")
                cancel = self.app.toast_.action("×")
                self.app.toast_.hide()
                cancel()
                self.app.drain("recording_done")
                self.assertIsNone(self.app._recorder)
                self.assertFalse(self.app._holding)
                self.assertFalse(self.app.toast_.visible)
        self.assertTrue(self.app.events.empty())

    def test_unconfirmed_paste_finishes_quietly_without_copying_or_retrying(self):
        ctx = SimpleNamespace(text="", app=1, window=2, editable=True, secure=False, _insertion_attempted=False)
        self.app.host.edit.ctx = ctx
        def request(text, context, **kwargs):
            context._insertion_attempted = True
            return False
        self.app.host.edit.type_at_caret = unittest.mock.Mock(side_effect=request)
        self.hold(2)
        self.app.drain()
        self.assertFalse(self.app.toast_.visible)
        self.app.host.edit.type_at_caret.assert_called_once()
        self.assertEqual(self.app.copied, [])

    def test_refused_insertion_with_a_shared_context_automatically_copies(self):
        self.app.host.edit.type_at_caret = unittest.mock.Mock(return_value=False)
        self.hold(2)
        self.app.drain()
        self.assertEqual(self.app.toast_.shown[-1][0], "Copied to clipboard")
        self.app.host.edit.type_at_caret.assert_called_once()
        self.assertEqual(self.app.copied, ["Hi there."])

    def test_input_meter_stops_updating_when_recording_ends(self):
        self.app._dictate_down()
        self.app.drain("recording_ready")
        recorder = self.app._recorder
        self.assertEqual(self.app.toast_.levels, [0.5])
        self.app._show_input_level(recorder)
        self.assertEqual(self.app.toast_.levels, [0.5, 0.5])
        self.app._cancel_dictation()
        self.app._show_input_level(recorder)
        self.assertEqual(self.app.toast_.levels, [0.5, 0.5])
        self.app.drain("recording_done")
        self.app._dictate_down()
        self.app.drain("recording_ready")
        self.app._show_input_level(recorder)
        self.assertEqual(len(self.app.toast_.levels), 3)
        self.app._cancel_dictation()
        self.app.drain("recording_done")

    def test_recording_uses_the_saved_microphone(self):
        self.app.cfg["dictate_microphone"] = "HyperX, MME"
        self.app._dictate_down()
        self.app.cfg["dictate_microphone"] = "Other, MME"
        self.app.drain("recording_ready")
        self.assertEqual(self.app._recorder.recorder.device, "HyperX, MME")
        self.app._cancel_dictation()
        self.app.drain("recording_done")

    def test_local_speech_download_uses_its_own_model_and_refreshes_completion(self):
        app = _App()
        notices = []
        app.host.notify, app.settings_win = notices.append, None
        app.refresh_writer, app.summarize_action = unittest.mock.Mock(), unittest.mock.Mock()
        with unittest.mock.patch.object(app_writer.local_stt, "download") as download, unittest.mock.patch.object(
                app_writer.threading, "Thread", lambda target, daemon: SimpleNamespace(start=target)):
            app.fix_writer("Local", True)
            app.stt_statuses["Local"] = "missing"  # an older settings refresh finishes during this download
            app.fix_writer("Local", True)  # a second click while downloading must not start another worker
        download.assert_called_once_with()
        self.assertTrue(app._local_stt_downloading)
        kind, error = app.events.get_nowait()
        self.assertEqual((kind, error), ("local_stt_downloaded", None))
        with unittest.mock.patch.object(app_writer.local_stt, "downloaded", return_value=True):
            app._local_stt_downloaded(error)
        self.assertEqual(app.stt_statuses["Local"], "ready")
        self.assertEqual(notices, [])
        app.summarize_action.assert_not_called()

    def test_local_speech_download_notifies_only_on_failure(self):
        app = _App()
        notices = []
        app.host.notify, app.settings_win = notices.append, None
        app.refresh_writer = unittest.mock.Mock()
        with unittest.mock.patch.object(app_writer.local_stt, "download", side_effect=OSError("private details")), \
                unittest.mock.patch.object(app_writer.threading, "Thread",
                                          lambda target, daemon: SimpleNamespace(start=target)):
            app.fix_writer("Local", True)
        self.assertEqual(notices, [])
        kind, error = app.events.get_nowait()
        self.assertEqual((kind, error), ("local_stt_downloaded", True))
        with unittest.mock.patch.object(app_writer.local_stt, "downloaded", return_value=False):
            app._local_stt_downloaded(error)
        self.assertFalse(app._local_stt_downloading)
        self.assertEqual(app.stt_statuses["Local"], "missing")
        self.assertEqual(notices, ["Could not download the local dictation model. Try again in Settings → Dictation."])

    def test_tidy_off_never_checks_writing_sources_and_still_inserts(self):
        local = Source("Local")
        local.status = unittest.mock.Mock(side_effect=OSError("private details"))
        self.app.writer_sources = [local]
        self.app.cfg["dictate_polish"] = False
        self.hold(2)
        self.app.drain()
        local.status.assert_not_called()
        self.assertEqual(self.app.host.edit.pasted, ["i thinks so"])
        self.assertFalse(self.app.rewriting)

    def test_local_status_failure_does_not_abort_recording_or_source_selection(self):
        app = _App([Source("Cursor"), Source("Local")])
        self.addCleanup(app._close_writer)
        app.writer_sources[1].status = unittest.mock.Mock(side_effect=RuntimeError("private details"))
        app._dictate_down()
        app.drain("recording_ready")
        self.assertIsNotNone(app._recorder)
        self.assertEqual([s.name for s in app._dictation_writers()], ["Cursor", "Local"])
        app._cancel_dictation()
        app.drain("recording_done")

    def test_taps_and_escape_rearm_idle_cleanup_and_invalidate_older_timer(self):
        for end in ("_dictate_up", "_escape"):
            with self.subTest(end=end):
                app = _App()
                self.addCleanup(app._close_writer)
                app.summary_server.stop = unittest.mock.Mock()
                app._arm_summary_stop()
                old = app.timers[-1]
                FakeRecorder.duration = .1
                app._dictate_down()
                app.drain("recording_ready")
                old()
                app.summary_server.stop.assert_not_called()
                getattr(app, end)()
                app.drain("recording_done")
                with unittest.mock.patch.object(app_writer.threading, "Thread",
                        lambda target, daemon: SimpleNamespace(start=target)):
                    app.timers[-1]()
                app.summary_server.stop.assert_called_once_with()

    def test_local_tidy_is_preferred_without_changing_writing_order(self):
        app = _App([Source("Cursor"), Source("Local")])
        self.assertEqual([s.name for s in app._dictation_writers()], ["Local", "Cursor"])
        self.assertEqual([s.name for s in app.writer_sources], ["Cursor", "Local"])
        app.cfg["dictate_local_tidy"] = False
        self.assertEqual([s.name for s in app._dictation_writers()], ["Cursor", "Local"])
        app.cfg["dictate_local_tidy"] = True
        app.cfg["writer_sources"] = [["Local", False], ["Cursor", True]]
        self.assertEqual([s.name for s in app._dictation_writers()], ["Cursor"])

    def test_local_transcriber_warms_on_hold(self):
        app = _App()
        self.addCleanup(app._close_writer)
        warmed = []
        app.cfg["dictate_polish"] = False
        app.cfg["stt_sources"] = [["Local", True]]
        app.stt_sources = [SimpleNamespace(name="Local", prewarm=lambda: warmed.append(True))]
        app._dictate_down()
        self.assertEqual(warmed, [])
        with unittest.mock.patch.object(app_writer.threading, "Thread",
                lambda target, args, daemon: SimpleNamespace(start=lambda: target(*args))):
            app.drain("recording_ready")
        self.assertEqual(warmed, [True])
        app._cancel_dictation()
        app.drain("recording_done")


class FlowTest(unittest.TestCase):
    def test_repeated_review_replacements_use_the_last_inserted_text(self):
        app = _App()
        app._last_edit = (CTX, "original", "first", "Fix errors")
        app.open_review()
        replace = app.review_.shown[-1][2]
        with unittest.mock.patch.object(app_writer.edit_logic, "undo", return_value=True) as undo:
            replace("second")
            replace("third")
        self.assertEqual([(c.args[2], c.args[3]) for c in undo.call_args_list],
                         [("second", "first"), ("third", "second")])

    def test_a_rewrite_pastes_keeps_the_trailing_space_and_offers_undo_review_mode(self):
        app = _App()
        app.rewrite_selection()
        self.assertTrue(app.popup.rewriting and app.esc.on)
        self.assertEqual(app.toast_.shown[-1][0], "Rewriting · Fix errors")
        app.drain()
        self.assertEqual(app.host.edit.pasted, ["Hi there. "])
        self.assertEqual(app.host.edit.ctxs, [CTX])  # the paste carries the captured target
        message, actions, busy = app.toast_.shown[-1]
        self.assertEqual((message, sorted(actions), busy), ("Fix errors · Cursor", ["Mode", "Review", "Undo"], False))
        self.assertFalse(app.popup.rewriting)
        self.assertEqual(len(app.timers), 1)  # the local model's idle stop is armed again

    def test_the_toasts_sit_under_a_visible_bar_and_know_its_top(self):
        app = _App()
        app.popup.visible, app.popup.anchor = True, lambda: (100, 200, 42)
        app.rewrite_selection()
        self.assertEqual(app.toast_.at, (100, 248, 200))  # 6 under the bar, which stays up showing "Rewriting…"
        app.drain()
        self.assertEqual(app.toast_.at, (100, 248, 200))  # the result takes the busy toast's place

    def test_undo_selects_the_paste_and_puts_the_original_back(self):
        app = _App()
        app.rewrite_selection()
        app.drain()
        app.toast_.action("Undo")()
        self.assertEqual(app.host.edit.log, [("select_back", 10)])
        self.assertEqual(app.host.edit.pasted, ["Hi there. ", "hi there "])
        self.assertEqual(app.toast_.shown[-1][0], "Original restored.")

    def test_a_changed_context_never_pastes_and_offers_copy(self):
        app = _App()
        app.host.edit.same = False
        app.rewrite_selection()
        app.drain()
        self.assertEqual(app.host.edit.pasted, [])
        self.assertEqual(app.toast_.shown[-1][0], "Text changed. Copy the rewrite?")
        app.toast_.action("Copy")()
        self.assertEqual(app.copied, ["Hi there. "])

    def test_no_selection_and_password_fields_show_a_hint_only(self):
        app = _App()
        app.host.edit.ctx = Context("", 1, 2, True, False)
        app.rewrite_selection()
        app.drain()
        self.assertEqual(app.toast_.shown[-1][0], "Select text to rewrite.")
        app.host.edit.ctx = Context("secret", 1, 2, True, True)
        app.rewrite_selection()
        app.drain()
        self.assertEqual(app.toast_.shown[-1][0], "Password fields are never rewritten.")
        self.assertEqual(app.writer_sources[0].texts, [])

    def test_a_failing_source_falls_through_and_the_toast_names_the_next(self):
        app = _App([Source("Cursor", RuntimeError("x")), Source("Codex", status="signed_out"), Source("Local", "Hi.")])
        app.rewrite_selection()
        app.drain()
        self.assertEqual(app.toast_.shown[-1][0], "Fix errors · Local")

    def test_another_mode_rewrites_the_original_in_place_of_the_paste(self):
        app = _App()
        app.rewrite_selection()
        app.drain()
        app.writer_sources[0].reply = "Hello there!"
        mode_menu = app.toast_.action("Mode")
        dict((t, fn) for t, fn, _ in mode_menu)["Email"]()
        app.drain()
        self.assertEqual(app.writer_sources[0].texts, [writer.wrap("hi there ")] * 2)
        self.assertEqual(app.host.edit.pasted, ["Hi there. ", "Hello there! "])
        self.assertEqual(app.toast_.shown[-1][0], "Email · Cursor")

    def test_review_replace_swaps_the_paste(self):
        app = _App()
        app.rewrite_selection()
        app.drain()
        app.toast_.action("Review")()
        original, inserted, replace, copy = app.review_.shown[-1]
        self.assertEqual((original, inserted), ("hi there ", "Hi there. "))
        replace("hi there. ")
        self.assertEqual(app.host.edit.pasted[-1], "hi there. ")

    def test_escape_cancels_a_rewrite_and_its_late_result_is_ignored(self):
        app = _App()
        app.rewrite_selection()
        app._escape()
        self.assertFalse(app.rewriting)
        app.drain()
        self.assertEqual(app.host.edit.pasted, [])
        self.assertFalse(app.toast_.visible)

    def test_cancel_on_the_rewriting_toast_drops_the_late_result(self):
        app = _App()
        app.rewrite_selection()
        app.toast_.action("Cancel")()
        self.assertFalse(app.rewriting or app.popup.rewriting)
        app.drain()
        self.assertEqual(app.host.edit.pasted, [])
        self.assertFalse(app.toast_.visible)
        app.toast_.action("Cancel")()  # a stale click does nothing

    def test_a_double_press_opens_the_mode_menu_and_a_single_press_rewrites(self):
        app = _App()
        app._rewrite_hotkey()
        app._rewrite_hotkey()
        self.assertEqual(app.picks[0][:2], ([m.name for m in writer.BUILTIN_MODES], "Fix errors"))
        for timer in app.timers:
            timer()
        self.assertTrue(app.events.empty())  # the first press's timer was cancelled by the second
        app.timers.clear()
        app._press_at = 0.0
        app._rewrite_hotkey()
        app.timers[0]()
        app.drain()
        self.assertEqual(app.host.edit.pasted, ["Hi there. "])

    def test_the_log_never_holds_the_text(self):
        app = _App()
        with self.assertLogs("harness_discussion", level="INFO") as logs:
            app.rewrite_selection()
            app.drain()
        self.assertNotIn("hi there", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
