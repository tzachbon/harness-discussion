"""Dictation target validation, cancellation and stale delivery events."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness_discussion import app_writer, core, mic
from harness_discussion.edit_logic import Context
from tests.test_app_writer import _App

from tests.dictation_helpers import DictationCase


class DeliveryTest(DictationCase):
    def test_unknown_or_changed_frozen_target_can_only_offer_copy(self):
        for identity in (None, (9, 10)):
            with self.subTest(identity=identity):
                app, devices = self.make_app()
                app.host.dictation_target = lambda: identity
                self.start(app)
                self.finish(app)
                app.host.edit.type_at_caret.assert_not_called()
                self.assertEqual(app.toast_.shown[-1][0], "Copied to clipboard")
                self.assertEqual(app.copied, ["Hi there."])

    def test_delayed_capture_cannot_adopt_another_window(self):
        app, devices = self.make_app()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def capture(fallback):
            self.assertFalse(fallback)
            entered.set()
            self.assertTrue(release.wait(5))
            return app.edit.ctx
        app.edit.capture = capture
        app._dictate_down()
        app.drain("mic_answer")
        self.assertTrue(entered.wait(5))
        app.edit.ctx = Context("", 8, 9, True, False)
        release.set()
        app.drain("recording_ready")
        self.finish(app)
        app.host.edit.type_at_caret.assert_not_called()

    def test_interaction_during_delayed_capture_cannot_adopt_a_field_or_selection(self):
        for change in ("field", "selection"):
            with self.subTest(change=change):
                app, devices = self.make_app()
                entered, release = threading.Event(), threading.Event()
                self.addCleanup(release.set)
                original = Context("", 1, 2, True, False, field="A", caret=(0, 0))
                app.edit.ctx = original

                def capture(_fallback):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("capture barrier was not released")
                    return app.edit.ctx

                app.edit.capture = capture
                app._dictate_down()
                app.drain("mic_answer")
                self.assertTrue(entered.wait(5))
                app.edit.ctx = Context("selected content", 1, 2, True, False,
                                       field="B" if change == "field" else "A", caret=(3, 16))
                app.popup.contains = lambda *_: False
                app.popup.menu_open = app.popup.playing = False
                core.App._handle(app, "press", 10, 20)
                release.set()
                app.drain("recording_ready")
                self.finish(app)
                app.host.edit.type_at_caret.assert_not_called()
                self.assertEqual(app.copied, ["Hi there."])

    def test_programmatic_focus_before_preparation_baseline_is_the_recording_target(self):
        app, devices = self.make_app()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        prepared = Context("", 1, 2, True, False, field="B", caret=(4, 0))

        def capture(_fallback):
            entered.set()
            if not release.wait(5):
                raise AssertionError("capture barrier was not released")
            return app.edit.ctx

        app.edit.capture = capture
        app._dictate_down()
        app.drain("mic_answer")
        self.assertTrue(entered.wait(5))
        app.edit.ctx = prepared
        release.set()
        app.drain("recording_ready")
        self.finish(app)
        self.assertIs(app.host.edit.type_at_caret.call_args.args[1], prepared)

    def test_activity_before_main_dispatch_is_latched_to_the_native_hold(self):
        app, devices = self.make_app()
        installed = []
        app.host.set_shortcut = lambda *args, **kwargs: installed.append((args, kwargs))
        app._apply_writer("dictate_hotkey", "<ctrl>+d")
        args, options = installed[-1]
        args[2]()  # native down queues startup, which main has not dispatched yet
        options["on_activity"]()
        app.drain("recording_ready")
        args[3]()
        app.drain("recording_done")
        app.drain("dictated")
        app.host.edit.type_at_caret.assert_not_called()
        self.assertEqual(app.copied, ["Hi there."])

    def test_activity_during_final_validation_prevents_insertion_without_ui_dispatch(self):
        app, devices = self.make_app()
        self.start(app)
        app._dictate_up()
        app.drain("recording_done")
        capture = app.edit.capture

        def validate(fallback):
            # The native callback runs while the UI is inside its blocking validation.
            app._invalidate_dictation_target()
            return capture(fallback)

        app.edit.capture = validate
        app.drain("dictated")
        app.host.edit.type_at_caret.assert_not_called()
        self.assertEqual(app.copied, ["Hi there."])

    def test_escape_during_capture_cancels_delivery_and_a_new_pending_hold_without_ui_dispatch(self):
        for new_hold in (False, True):
            with self.subTest(new_hold=new_hold):
                app, devices = self.make_app()
                installed = []
                app.host.set_shortcut = lambda *args, **kwargs: installed.append((args, kwargs))
                app._apply_writer("dictate_hotkey", "<ctrl>+d")
                args, options = installed[-1]
                args[2]()
                app.drain("recording_ready")
                args[3]()
                app.drain("recording_done")
                session = app._dictation_job
                entered, release = threading.Event(), threading.Event()
                capture = app.edit.capture
                observed = []

                def capture_result(fallback):
                    entered.set()
                    self.assertTrue(release.wait(5))
                    return capture(fallback)

                def escape():
                    if entered.wait(5):
                        if new_hold:
                            args[2]()
                        options["on_cancel"]()
                        observed.append(session.cancel.is_set())
                    release.set()

                app.edit.capture = capture_result
                observer = threading.Thread(target=escape)
                observer.start()
                app.drain("dictated")
                observer.join(5)
                self.assertFalse(observer.is_alive())
                self.assertEqual(observed, [True])
                app.edit.type_at_caret.assert_not_called()
                self.assertEqual(app.copied, [])
                self.assertFalse(app.toast_.visible)
                if new_hold:
                    app.drain("dictate_down")
                    self.assertIsNone(app._recorder)
                    self.assertEqual(len(devices.streams), 1)
                app.drain("call")

    def test_escape_at_last_native_validation_suppresses_insertion_and_copy(self):
        app, devices = self.make_app()
        self.start(app)
        app._dictate_up()
        app.drain("recording_done")
        session = app._dictation_job

        def insert(text, ctx, *, cancelled):
            app._native_escape()
            self.assertTrue(session.cancel.is_set())
            self.assertTrue(cancelled())
            return False

        app.edit.type_at_caret.side_effect = insert
        app.drain("dictated")
        app.edit.type_at_caret.assert_called_once()
        self.assertEqual(app.copied, [])
        self.assertFalse(app.toast_.visible)
        app.drain("call")

    def test_escape_observed_after_result_queued_drops_output_and_clears_busy_state(self):
        app, devices = self.make_app()
        self.start(app)
        app._dictate_up()
        app.drain("recording_done")
        app._dictation_job.thread.join(5)
        app._native_escape()
        app.drain("dictated")
        self.assertFalse(app.rewriting)
        self.assertIsNone(app._dictation_job)
        app.edit.type_at_caret.assert_not_called()
        self.assertEqual(app.copied, [])
        app.drain("call")
        self.start(app)
        self.assertTrue(app._holding)

    def test_delivery_identity_is_visible_while_busy_feedback_changes(self):
        app, devices = self.make_app()
        installed = []
        app.host.set_shortcut = lambda *args, **kwargs: installed.append((args, kwargs))
        app._apply_writer("dictate_hotkey", "<ctrl>+d")
        args, options = installed[-1]
        args[2]()
        app.drain("recording_ready")
        args[3]()
        app.drain("recording_done")
        session = app._dictation_job

        def feedback(on):
            if not on:
                args[2]()
                options["on_cancel"]()
                self.assertTrue(session.cancel.is_set())

        app.popup.set_rewriting = feedback
        app.drain("dictated")
        app.edit.type_at_caret.assert_not_called()
        self.assertEqual(app.copied, [])
        app.drain("dictate_down")
        self.assertIsNone(app._recorder)
        self.assertEqual(len(devices.streams), 1)
        app.drain("call")

    def test_escape_before_queued_down_rejects_startup(self):
        app, devices = self.make_app()
        installed = []
        app.host.set_shortcut = lambda *args, **kwargs: installed.append((args, kwargs))
        app._apply_writer("dictate_hotkey", "<ctrl>+d")
        args, options = installed[-1]
        args[2]()
        options["on_cancel"]()
        app.drain("dictate_down")
        self.assertIsNone(app._recorder)
        self.assertEqual(devices.streams, [])
        self.assertEqual(app.copied, [])
        app.drain("call")

    def test_escape_during_recording_to_transcription_handoff_cancels_the_session(self):
        app, devices = self.make_app()
        self.start(app)
        session = app._recorder
        arm = app._arm_summary_stop

        def handoff():
            app._arm_summary_stop = arm
            arm()
            self.assertIsNone(app._recorder)
            self.assertIsNone(app._dictation_job)
            app._native_escape()

        app._arm_summary_stop = handoff
        app._dictate_up()
        app.drain("recording_done")
        if app._dictation_job is not None:
            app.drain("dictated")
        self.assertTrue(session.cancel.is_set())
        self.assertIsNone(app._dictation_job)
        app.stt_sources[0].transcribe.assert_not_called()
        app.edit.type_at_caret.assert_not_called()
        self.assertEqual(app.copied, [])
        self.assertFalse(app.toast_.visible)
        self.assertIsNone(app._dictation_delivery)
        self.start(app)
        self.assertTrue(app._holding)

    def test_escape_at_transcription_publication_preserves_cancellation_and_cleans_handoff(self):
        for phase in ("before", "after"):
            with self.subTest(phase=phase):
                app, devices = self.make_app()
                self.start(app)
                session = app._recorder
                transcribe = app._transcribe

                def publish(owner, audio):
                    self.assertIs(app._dictation_delivery, session)
                    if phase == "before":
                        app._native_escape()
                    transcribe(owner, audio)
                    if phase == "after":
                        app._native_escape()

                app._transcribe = publish
                app._dictate_up()
                app.drain("recording_done")
                app.drain("dictated")
                self.assertTrue(session.cancel.is_set())
                self.assertIsNone(app._dictation_delivery)
                self.assertIsNone(app._dictation_job)
                app.edit.type_at_caret.assert_not_called()
                self.assertEqual(app.copied, [])
                self.assertFalse(app.toast_.visible)

    def test_escape_during_terminal_microphone_error_hides_only_that_sessions_hud(self):
        app, devices = self.make_app()
        app.mic_ok = False
        show = app.toast_.show

        def show_with_escape(message, *args, **kwargs):
            show(message, *args, **kwargs)
            if message == "The microphone could not finish the recording.":
                observer = threading.Thread(target=app._native_escape)
                observer.start()
                observer.join(5)
                self.assertFalse(observer.is_alive())

        app.toast_.show = show_with_escape
        app._dictate_down()
        session = app._recorder
        app.drain("recording_done")
        self.assertTrue(session.cancel.is_set())
        self.assertFalse(app.toast_.visible)
        self.assertIsNone(app._dictation_delivery)
        self.assertEqual(app.copied, [])
        app.stt_sources[0].transcribe.assert_not_called()
        app.edit.type_at_caret.assert_not_called()
        kind, cleanup = app.events.get(timeout=5)
        self.assertEqual(kind, "call")
        app.mic_ok = True
        self.start(app)
        current = app._recorder
        cleanup()
        self.assertTrue(app.toast_.visible)
        self.assertIs(app._recorder, current)
        self.assertFalse(current.cancel.is_set())

    def test_stale_native_binding_and_queued_escape_cleanup_preserve_a_new_session(self):
        app, devices = self.make_app()
        installed = []
        app.host.set_shortcut = lambda *args, **kwargs: installed.append((args, kwargs))
        app._apply_writer("dictate_hotkey", "<ctrl>+d")
        old_cancel = installed[-1][1]["on_cancel"]
        self.start(app)
        app._native_escape()
        kind, cleanup = app.events.get(timeout=5)
        self.assertEqual(kind, "call")
        app._cancel_dictation()
        app.drain("recording_done")
        app._apply_writer("dictate_hotkey", "<ctrl>+e")
        self.start(app)
        current = app._recorder
        old_cancel()
        cleanup()
        self.assertIs(app._recorder, current)
        self.assertFalse(current.cancel.is_set())
        self.assertFalse(current.stop.is_set())
        self.assertTrue(app.toast_.visible)

    def test_old_binding_activity_cannot_invalidate_the_new_hold(self):
        app, devices = self.make_app()
        installed = []
        app.host.set_shortcut = lambda *args, **kwargs: installed.append((args, kwargs))
        app._apply_writer("dictate_hotkey", "<ctrl>+d")
        old_activity = installed[-1][1]["on_activity"]
        app._apply_writer("dictate_hotkey", "<ctrl>+e")
        args, _ = installed[-1]
        args[2]()
        app.drain("recording_ready")
        old_activity()
        args[3]()
        app.drain("recording_done")
        app.drain("dictated")
        app.host.edit.type_at_caret.assert_called_once()
        self.assertFalse(app.toast_.visible)
        self.assertEqual(app.copied, [])

    def test_insertion_guard_retains_session_identity_after_provider_owner_is_cleared(self):
        for change in ("activity", "cancel", "generation", "binding", "request", "closed"):
            with self.subTest(change=change):
                app, devices = self.make_app()
                self.start(app)
                app._dictate_up()
                app.drain("recording_done")
                session = app._dictation_job

                def insert(text, ctx, *, cancelled):
                    self.assertIsNone(app._dictation_job)
                    self.assertFalse(cancelled())
                    if change in ("activity", "cancel"):
                        getattr(session, change).set()
                    elif change == "closed":
                        app._writer_closed = True
                    else:
                        attr = {"generation": "_dictate_generation", "binding": "_dictate_binding",
                                "request": "_rewrite_id"}[change]
                        setattr(app, attr, getattr(app, attr) + 1)
                    self.assertTrue(cancelled())
                    return False

                app.edit.type_at_caret.side_effect = insert
                app.drain("dictated")
                app.edit.type_at_caret.assert_called_once()
                self.assertEqual(app.copied, ["Hi there."] if change == "activity" else [])


if __name__ == "__main__":
    unittest.main()
