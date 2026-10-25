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

    def show(self, message, x, y, actions=(), busy=False, above=None):
        self.visible = True
        self.at = (x, y, above)
        self.shown.append((message, dict(actions), busy))

    def hide(self):
        self.visible = False
        self.on_hide()

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
            copy_text=self.copied.append, set_shortcut=lambda *a: None,
            pick_mode=lambda names, current, pick: self.picks.append((names, current, pick)))
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

    def drain(self):
        kind, *args = self.events.get(timeout=5)
        {"rewrite": self._rewrite_done, "dictated": self._dictated}[kind](*args)


class FakeRecorder:
    def __init__(self, audio=b"RIFFwav"):
        self.audio, self.started = audio, False

    def start(self):
        self.started = True

    def stop(self):
        return self.audio


class DictationTest(unittest.TestCase):
    def setUp(self):
        self.app = _App()
        self.app.host.edit.type_at_caret = lambda text, ctx: self.app.host.edit.pasted.append(text) or self.app.host.edit.ctxs.append(ctx) or True
        self.app.stt_sources = [SimpleNamespace(name="Codex", status=lambda: "ready",
                                                transcribe=lambda audio, language="": "i thinks so")]
        patcher = unittest.mock.patch.object(app_writer.mic, "Recorder", FakeRecorder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def hold(self, seconds):
        self.app._dictate_down()
        self.assertEqual(self.app.toast_.shown[-1][0], "Listening · Codex")
        self.app._recorded_at -= seconds
        self.app._dictate_up()

    def test_hold_transcribes_rewrites_and_types_at_the_caret(self):
        self.hold(2)
        self.app.drain()
        self.assertEqual(self.app.host.edit.pasted, ["Hi there."])
        self.assertEqual(self.app.host.edit.ctxs, [CTX])  # typed only into the field captured at key down
        self.assertEqual(self.app.writer_sources[0].texts, [writer.wrap("i thinks so")])
        self.assertEqual(self.app.toast_.shown[-1][0], "Dictated · Clean dictation · Cursor")

    def test_dictation_uses_its_custom_prompt_independently_of_the_writing_default(self):
        self.app.cfg.update(default_mode="Email", dictate_mode="My dictation",
                            modes=[{"name": "My dictation", "instructions": "Turn spoken steps into a numbered list."}])
        self.hold(2)
        self.app.drain()
        mode = writer.find_mode("My dictation", writer.all_modes(self.app.cfg["modes"]))
        self.assertEqual(self.app.writer_sources[0].prompts, [writer.system_prompt(mode)])
        self.assertEqual(self.app.toast_.shown[-1][0], "Dictated · My dictation · Cursor")
        self.assertEqual(self.app.cfg["default_mode"], "Email")

    def test_missing_dictation_mode_uses_clean_dictation_instead_of_the_writing_default(self):
        self.app.cfg.update(default_mode="Email", dictate_mode="Deleted")
        self.hold(2)
        self.app.drain()
        self.assertEqual(self.app.toast_.shown[-1][0], "Dictated · Clean dictation · Cursor")

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
        self.assertEqual(self.app.toast_.shown[-1][0], "Transcribing · Clean dictation")
        self.app.toast_.action("Cancel")()
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
        self.assertEqual(self.app.toast_.shown[-1][0], "Dictated · Codex")
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

    def test_key_down_prewarms_the_first_writer_only_when_polish_is_on(self):
        calls = []
        self.app.writer_sources[0].prewarm = calls.append
        self.app.cfg["writer_models"] = {"Cursor": {"model": "fast"}}
        with unittest.mock.patch.object(app_writer.threading, "Thread",
                                        lambda target, args, daemon: SimpleNamespace(start=lambda: target(*args))):
            self.app._dictate_down()
            self.app._dictate_up()
            self.app.cfg["dictate_polish"] = False
            self.app._dictate_down()
        self.assertEqual(calls, ["fast"])

    def test_a_tap_cancels(self):
        self.hold(0.1)
        self.assertTrue(self.app.events.empty())
        self.assertFalse(self.app.toast_.visible)

    def test_no_microphone_permission_offers_settings(self):
        self.app.mic_ok = False
        self.app._dictate_down()
        self.assertEqual(sorted(self.app.toast_.shown[-1][1]), ["Open Settings"])
        self.assertIsNone(self.app._recorder)

    def test_a_window_change_offers_copy_instead_of_typing(self):
        self.app._dictate_down()
        self.app._recorded_at -= 2
        self.app.host.edit.ctx = Context("", 9, 9, True, False)
        self.app._dictate_up()
        self.app.drain()
        self.assertEqual(self.app.host.edit.pasted, [])
        self.assertEqual(self.app.toast_.shown[-1][0], "The window changed. Copy the text?")


class FlowTest(unittest.TestCase):
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
