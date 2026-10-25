"""Recording ownership with queued UI events and explicit worker barriers, no devices or providers."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness_discussion import app_writer, core, mic
from harness_discussion.edit_logic import Context
from tests.test_app_writer import _App

from tests.dictation_helpers import DictationCase, RECORDER


class LifecycleTest(DictationCase):
    def test_duplicate_down_during_permission_keeps_one_hold(self):
        app, devices = self.make_app()
        answers = []
        app.host.mic_permission = answers.append
        app._dictate_down()
        app._dictate_down()
        self.assertEqual(len(answers), 1)
        self.assertIsNotNone(app._recorder)
        self.assertTrue(app._holding)
        app._dictate_up()
        app.drain("recording_done")
        answers[0](True)
        app.drain("mic_answer")
        self.assertEqual(devices.streams, [])
        self.assertFalse(app.rewriting)

    def test_recording_errors_log_the_stage_without_private_exception_details(self):
        for stage in ("permission", "target capture", "start", "stop"):
            with self.subTest(stage=stage):
                app, devices = self.make_app()
                private = "private device or editor details"
                if stage == "permission":
                    app.mic_ok = False
                elif stage == "target capture":
                    app.edit.capture = Mock(side_effect=RuntimeError(private))
                with self.assertLogs("harness_discussion", level="INFO") as logs:
                    if stage == "start":
                        with patch.object(RECORDER, "start", side_effect=RuntimeError(private)):
                            app._dictate_down()
                            app.drain("recording_done")
                    elif stage == "stop":
                        self.start(app)
                        stop = app._recorder.recorder.stop
                        def fail_stop():
                            stop()
                            raise RuntimeError(private)
                        app._recorder.recorder.stop = fail_stop
                        app._dictate_up()
                        app.drain("recording_done")
                    else:
                        app._dictate_down()
                        app.drain("recording_done")
                self.assertIn("failed during " + stage, "\n".join(logs.output))
                self.assertNotIn(private, "\n".join(logs.output))
                self.assertNotIn(private, repr(app.toast_.shown))
                self.assertIsNone(app._recorder)
                app.stt_sources[0].transcribe.assert_not_called()

    def test_duplicate_down_during_start_and_stop_never_opens_another_device(self):
        for phase in ("start", "stop"):
            with self.subTest(phase=phase):
                app, devices = self.make_app(phase)
                app._dictate_down()
                app.drain("mic_answer")
                if phase == "stop":
                    app.drain("recording_ready")
                    app._dictate_up()
                self.assertTrue(devices.entered.wait(5))
                owner = app._recorder
                app._dictate_down()
                self.assertIs(app._recorder, owner)
                self.assertEqual(len(devices.streams), 1)
                app._cancel_dictation()
                devices.release.set()
                app.drain("recording_done")
                self.assertEqual(devices.active, 0)

    def test_timeout_keeps_ownership_until_start_stop_or_close_exits(self):
        for phase in ("start", "stop", "close"):
            with self.subTest(phase=phase):
                app, devices = self.make_app(phase)
                app._dictate_down()
                app.drain("mic_answer")
                if phase != "start":
                    app.drain("recording_ready")
                    app._dictate_up()
                self.assertTrue(devices.entered.wait(5))
                owner = app._recorder
                self.fire(app, app_writer.START_TIMEOUT_S if phase == "start" else app_writer.STOP_TIMEOUT_S)
                self.assertFalse(app.toast_.shown[-1][2])
                self.assertFalse(app._holding)
                self.assertIs(app._recorder, owner)
                app._dictate_down()
                app.rewrite_selection()
                self.assertIn("still finishing", app.toast_.shown[-1][0])
                self.assertFalse(app.toast_.shown[-1][2])
                self.assertIs(app._recorder, owner)
                self.assertEqual(len(devices.streams), 1)
                devices.release.set()
                app.drain("recording_done")
                self.assertIsNone(app._recorder)
                self.assertFalse(app.rewriting)
                self.assertIn("still finishing", app.toast_.shown[-1][0])
                app.stt_sources[0].transcribe.assert_not_called()
                self.start(app)
                self.finish(app)
                self.assertEqual(len(devices.streams), 2)
                self.assertEqual(devices.maximum, 1)
                app.host.edit.type_at_caret.assert_called_once()

    def test_early_release_during_start_closes_without_listening_or_counting_startup_time(self):
        app, devices = self.make_app("start")
        app._dictate_down()
        app.drain("mic_answer")
        self.assertTrue(devices.entered.wait(5))
        app._dictate_up()
        devices.release.set()
        app.drain("recording_done")
        self.assertFalse(any(message.startswith("Listening") for message, _, _ in app.toast_.shown))
        self.assertEqual([operation for operation, _ in devices.calls], ["start", "stop", "close"])
        self.assertFalse(app.toast_.visible)
        app.stt_sources[0].transcribe.assert_not_called()

    def test_release_while_start_blocks_discards_audio_delivered_before_and_after_release(self):
        app, devices = self.make_app("start")
        devices.audio_before_start = True
        app._dictate_down()
        app.drain("mic_answer")
        self.assertTrue(devices.entered.wait(5))  # One full second was delivered before the barrier.
        app._dictate_up()
        devices.release.set()
        app.drain("recording_done")
        self.assertFalse(any(message.startswith("Listening") for message, _, _ in app.toast_.shown))
        self.assertEqual([operation for operation, _ in devices.calls], ["start", "stop", "close"])
        app.stt_sources[0].transcribe.assert_not_called()
        self.assertFalse(app.toast_.visible)

    def test_context_and_every_device_call_are_on_one_worker(self):
        app, devices = self.make_app()
        threads = []
        capture = app.edit.capture
        app.edit.capture = lambda fallback: threads.append(threading.get_ident()) or capture(fallback)
        self.start(app)
        app._cancel_dictation()
        app.drain("recording_done")
        device_threads = {ident for _, ident in devices.calls}
        self.assertEqual(device_threads, set(threads))
        self.assertEqual(len(device_threads), 1)
        self.assertNotIn(threading.get_ident(), device_threads)

    def test_escape_and_quit_during_blocked_start_cannot_transcribe_or_show_late_listening(self):
        for action in ("_escape", "_close_writer"):
            with self.subTest(action=action):
                app, devices = self.make_app("start")
                app._dictate_down()
                app.drain("mic_answer")
                self.assertTrue(devices.entered.wait(5))
                getattr(app, action)()
                self.assertIsNotNone(app._recorder)
                devices.release.set()
                app.drain("recording_done")
                self.assertFalse(app.toast_.visible)
                self.assertIsNone(app._recorder)
                self.assertFalse(any(message.startswith("Listening") for message, _, _ in app.toast_.shown))
                app.stt_sources[0].transcribe.assert_not_called()

    def test_cancel_action_works_in_preparing_listening_and_stopping(self):
        for phase in ("preparing", "listening", "stopping"):
            with self.subTest(phase=phase):
                app, devices = self.make_app("stop" if phase == "stopping" else None)
                if phase == "preparing":
                    app.host.mic_permission = lambda answer: None
                    app._dictate_down()
                else:
                    self.start(app)
                    if phase == "stopping":
                        app._dictate_up()
                        self.assertTrue(devices.entered.wait(5))
                app.toast_.action("×")()
                devices.release.set()
                app.drain("recording_done")
                self.assertFalse(app.toast_.visible)
                self.assertFalse(app.rewriting)
                app.stt_sources[0].transcribe.assert_not_called()

    def test_lost_key_up_finalizes_after_the_recording_limit(self):
        app, devices = self.make_app()
        app._dictate_down()
        session = app._recorder
        # Simulate expiry of the worker's bounded recording wait without waiting a minute.
        with patch.object(session.stop, "wait", return_value=False) as wait:
            app.drain("recording_done")
        wait.assert_called_once_with(60)
        app.drain("dictated")
        app.host.edit.type_at_caret.assert_called_once()
        self.assertEqual(devices.active, 0)
        self.assertFalse(app._holding)

    def test_queued_edges_from_old_binding_are_ignored_and_rebind_cancels_capture(self):
        app, devices = self.make_app()
        installed = []
        app.host.set_shortcut = lambda *args, **kwargs: installed.append(args)
        app._apply_writer("dictate_hotkey", "F8")
        old_down, old_up = installed[-1][2:]
        old_down()
        app._apply_writer("dictate_hotkey", "F9")
        new_down, new_up = installed[-1][2:]
        app.drain("dictate_down")
        self.assertIsNone(app._recorder)
        new_down()
        app.drain("recording_ready")
        old_up()
        app.drain("dictate_up")
        self.assertTrue(app._holding)
        self.assertFalse(app._recorder.stop.is_set())
        app._apply_writer("dictate_hotkey", "F10")
        app.drain("recording_done")
        new_down()
        new_up()
        app.drain("dictate_up")
        self.assertIsNone(app._recorder)
        app.stt_sources[0].transcribe.assert_not_called()
        self.assertEqual(len(devices.streams), 1)

    def test_stale_ready_done_error_and_timers_cannot_change_next_hold(self):
        app, devices = self.make_app()
        self.start(app)
        old = app._recorder
        app._cancel_dictation()
        app.drain("recording_done")
        old_timers = [fn for _, fn in app.deadlines]
        self.start(app)
        current = app._recorder
        shown = list(app.toast_.shown)
        for callback in old_timers:
            callback()
            if not app.events.empty():
                app.drain("dictation_timer")
        app.events.put(("recording_ready", old.generation, old.binding, 0))
        app.events.put(("recording_stopping", old.generation, old.binding))
        app.events.put(("recording_done", old.generation, old.binding, b"", 0, None, "old error"))
        app.drain("recording_done")
        self.assertIs(app._recorder, current)
        self.assertEqual(app.toast_.shown, shown)
        self.assertTrue(app._holding)
        self.finish(app)

    def test_pending_rewrite_timer_is_invalidated_and_rewrite_is_gated(self):
        app, devices = self.make_app()
        app._seq, app._hotkey_wait = 17, object()
        app._rewrite_hotkey()
        pending = app.deadlines[-1][1]
        self.start(app)
        self.assertEqual(app._seq, 18)
        self.assertIsNone(app._hotkey_wait)
        app._rewrite_hotkey()
        app.rewrite_selection()
        pending()
        self.assertFalse(app.rewriting)
        self.assertEqual(app.picks, [])
        self.assertEqual(app.writer_sources[0].texts, [])
        app._cancel_dictation()
        app.drain("recording_done")
        pending()
        self.assertEqual(app.writer_sources[0].texts, [])

    def test_cancel_during_capture_does_not_open_a_device_after_capture_returns(self):
        app, devices = self.make_app()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def capture(fallback):
            entered.set()
            self.assertTrue(release.wait(5))
            return app.edit.ctx
        app.edit.capture = capture
        app._dictate_down()
        app.drain("mic_answer")
        self.assertTrue(entered.wait(5))
        app._cancel_dictation()
        release.set()
        app.drain("recording_done")
        self.assertEqual(devices.streams, [])
        self.assertFalse(app.toast_.visible)

    def test_secure_fields_are_refused_before_opening_a_device(self):
        app, devices = self.make_app()
        app.edit.ctx = Context("", 1, 2, True, True)
        app._dictate_down()
        app.drain("recording_done")
        self.assertEqual(devices.streams, [])
        app.stt_sources[0].transcribe.assert_not_called()
        self.assertFalse(app.toast_.shown[-1][2])

    def test_field_refusal_and_final_capture_errors_offer_copy_and_recover(self):
        for refusal in ("secure", "capture", "type"):
            with self.subTest(refusal=refusal):
                app, devices = self.make_app()
                self.start(app)
                app._dictate_up()
                app.drain("recording_done")
                capture = app.edit.capture
                if refusal == "secure":
                    app.edit.ctx = Context("", 1, 2, True, True)
                elif refusal == "capture":
                    app.edit.capture = Mock(side_effect=RuntimeError("private capture details"))
                else:
                    app.edit.type_at_caret.side_effect = RuntimeError("private insertion details")
                app.drain("dictated")
                self.assertFalse(app.rewriting)
                self.assertEqual(app.copied, [] if refusal == "type" else ["Hi there."])
                if refusal == "type":
                    self.assertFalse(app.toast_.visible)
                else:
                    self.assertEqual(app.toast_.shown[-1][0], "Copied to clipboard")
                self.assertNotIn("private", repr(app.toast_.shown))
                if refusal != "type":
                    app.edit.type_at_caret.assert_not_called()
                app.edit.ctx = Context("", 1, 2, True, False)
                app.edit.capture = capture
                app.edit.type_at_caret.side_effect = None
                self.start(app)
                self.finish(app)
                self.assertFalse(app.toast_.visible)

    def test_capture_exception_or_permission_exception_is_generic_and_recoverable(self):
        for phase in ("permission", "capture"):
            with self.subTest(phase=phase):
                app, devices = self.make_app()
                target = app.host if phase == "permission" else app.edit
                attribute = "mic_permission" if phase == "permission" else "capture"
                with patch.object(target, attribute, side_effect=RuntimeError("private field details")):
                    app._dictate_down()
                    app.drain("recording_done")
                self.assertNotIn("private", repr(app.toast_.shown))
                self.assertFalse(app._holding)
                self.assertIsNone(app._recorder)
                self.assertEqual(devices.streams, [])
                self.start(app)
                self.finish(app)

    def test_transcription_cancel_or_timeout_allows_restart_and_drops_late_results(self):
        for stage in ("transcribe", "tidy"):
            for timeout in (False, True):
                with self.subTest(stage=stage, timeout=timeout):
                    app, devices = self.make_app()
                    entered, release = threading.Event(), threading.Event()
                    self.addCleanup(release.set)
                    def blocked(*args, **kwargs):
                        entered.set()
                        if not release.wait(5):
                            raise AssertionError("test did not release provider barrier")
                        return "private words"
                    source = app.stt_sources[0] if stage == "transcribe" else app.writer_sources[0]
                    operation = "transcribe" if stage == "transcribe" else "rewrite"
                    original = getattr(source, operation)
                    setattr(source, operation, Mock(side_effect=blocked))
                    self.start(app)
                    app._dictate_up()
                    app.drain("recording_done")
                    self.assertTrue(entered.wait(5))
                    job = app._dictation_job
                    old_timers = [fn for _, fn in app.deadlines]
                    old_cancel = app.toast_.action("×")
                    if timeout:
                        self.fire(app, app_writer.TRANSCRIBE_TIMEOUT_S)
                    else:
                        old_cancel()
                    self.assertTrue(job.cancel.is_set())
                    self.assertTrue(job.thread.is_alive())
                    self.assertFalse(app.rewriting)
                    self.assertIsNone(app._dictation_job)
                    self.start(app)
                    current = app._recorder
                    self.assertIsNotNone(current)
                    self.assertTrue(app._holding)
                    self.assertEqual(len(devices.streams), 2)
                    shown = list(app.toast_.shown)
                    old_cancel()
                    for callback in old_timers:
                        callback()
                        if not app.events.empty():
                            app.drain("dictation_timer")
                    release.set()
                    job.thread.join(5)
                    self.assertFalse(job.thread.is_alive())
                    app.drain("dictated")
                    self.assertIs(app._recorder, current)
                    self.assertTrue(app._holding)
                    self.assertEqual(app.toast_.shown, shown)
                    self.assertTrue(app.toast_.visible)
                    app.host.edit.type_at_caret.assert_not_called()
                    self.assertEqual(app.copied, [])
                    setattr(source, operation, original)
                    self.finish(app)
                    app.host.edit.type_at_caret.assert_called_once_with(
                        "Hi there.", app.edit.ctx, cancelled=unittest.mock.ANY)
                    self.assertEqual(devices.maximum, 1)


if __name__ == "__main__":
    unittest.main()
