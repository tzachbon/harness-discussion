"""The shared core on a fake host: the read loop, fallback, and the macOS update check."""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_discussion import app_skip, core, settings, updates
from harness_discussion.audio import NoAudioError


class Engine:
    speed = 1.0

    def __init__(self, name, fail=False):
        self.name, self.fail, self.paused = name, fail, threading.Event()
        self.spoken, self.done = [], []

    def speak(self, text, tag, on_done, on_audio, on_caption=None):
        self.spoken.append(text)
        self.on_caption = on_caption
        if self.fail:
            return on_done(NoAudioError("offline"))
        on_audio()
        self.done.append(on_done)

    def stop(self):
        self.paused.clear()

    def pause(self):
        self.paused.set()

    def resume(self):
        self.paused.clear()

    close = stop


class Popup:
    text, playing, paused, menu_open, visible = "", False, False, False, False

    def __init__(self, speed, **callbacks):
        self.callbacks, self.caption, self.source = callbacks, "", None

    def show(self, text, x, y):
        self.text, self.visible = text, True

    def hide(self):
        self.visible = False

    def contains(self, x, y):
        return False

    def lang_tag(self):
        return None

    def schedule_hide(self):
        pass

    def set_loading(self, loading):
        pass

    def set_playing(self, playing, paused=False):
        self.playing, self.paused = playing, playing and paused

    def set_caption(self, text):
        self.caption = text

    def set_caption_paused(self, paused):
        pass

    def configure_captions(self, *appearance):
        pass

    def set_speed(self, speed):
        pass

    def set_source(self, name):
        self.source = name

    def play(self):
        if not self.playing:
            self.set_playing(True)
            self.callbacks["on_play"](self.text, None)

    def close(self):
        self.set_playing(False)
        self.hide()
        self.callbacks["on_stop"]()


class FakeHost:
    def __init__(self, engines):
        self._engines, self.timers, self.notes, self.quits = engines, [], [], 0
        self.app, self.opened = None, []

    def engines(self):
        return self._engines

    def make_popup(self, speed, **callbacks):
        return Popup(speed, **callbacks)

    def open_settings(self, welcome=False):
        self.opened.append(welcome)

    def after(self, ms, fn):
        self.timers.append((ms, fn))

    def notify(self, message):
        self.notes.append(message)

    def start_trigger(self, on_selection, on_press):
        self.triggered = (on_selection, on_press)

    def stop_trigger(self):
        pass

    def set_hotkey(self, spec, on_hotkey):
        self.hotkey = (spec, on_hotkey)

    def pointer(self):
        return 10, 20

    def read_selection(self, clipboard_fallback):
        return "Selected."

    def quit(self):
        self.quits += 1


class CoreTest(unittest.TestCase):
    def make(self, *engines):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "settings.json"
        path.write_text(json.dumps(settings.DEFAULTS), encoding="utf-8")
        for p in (patch.object(settings, "PATH", str(path)),
                  patch.object(settings, "load",  # the fake engines, not this platform's sources
                               return_value={**settings.DEFAULTS, "sources": [[e.name, True] for e in engines]}),
                  patch.object(settings, "save")):
            p.start()
            self.addCleanup(p.stop)
        self.host = FakeHost(list(engines))
        return core.App(self.host)

    def test_two_sentences_play_in_order_and_pause_holds_the_boundary(self):
        engine = Engine("macOS")
        app = self.make(engine)
        app.play("First one. Second one.", None)
        app.pump()
        self.assertEqual(engine.spoken, ["First one."])
        self.assertEqual(app.popup.caption, "First one.")
        app.popup.paused = True
        app.pause()
        engine.done[0](None)
        app.pump()
        self.assertEqual(engine.spoken, ["First one."])  # waits at the boundary while paused
        app.popup.paused = False
        app.resume()
        self.assertEqual(engine.spoken, ["First one.", "Second one."])
        engine.done[1](None)
        app.pump()
        self.assertIsNone(app._read_options)
        self.assertEqual(self.host.notes, [])

    def test_skip_moves_between_sentences_and_stops_past_the_last(self):
        engine = Engine("macOS")
        app = self.make(engine)
        app.skip(1)  # nothing reading: nothing happens
        self.assertEqual(engine.spoken, [])
        app.play("One. Two. Three.", None)
        app.pump()
        app.skip(1)
        app.pump()
        self.assertEqual(engine.spoken[-1], "Two.")
        self.assertTrue(app.popup.playing)
        app.skip(-1)
        app.pump()
        self.assertEqual(engine.spoken[-1], "One.")
        app.skip(-1)  # at the first sentence: reads it again
        app.pump()
        self.assertEqual(engine.spoken[-1], "One.")
        app.skip(1)
        app.skip(1)
        app.pump()
        self.assertEqual(engine.spoken[-1], "Three.")
        app.skip(1)
        self.assertIsNone(app._read_options)

    def test_skip_follows_the_caption_and_keeps_its_place_on_an_unknown_one(self):
        app = self.make(Engine("macOS"))
        app.play("One. Two. Three.", None)
        app._track("Two.")
        self.assertEqual(app._sentence, 1)
        app._track("Reworded by Codex")
        self.assertEqual(app._sentence, 1)
        app._track("One.")  # never moves back on a caption
        self.assertEqual(app._sentence, 1)

    def test_skip_tracks_the_second_of_two_identical_sentences(self):
        engine = Engine("macOS")
        app = self.make(engine)
        app.play("Go. Go. Stop.", None)
        app._show_caption("Go.", step=True)
        app._show_caption("Go.", step=True)
        self.assertEqual(app._sentence, 1)
        app.skip(1)
        self.assertEqual(engine.spoken[-1], "Stop.")

    def test_hotkey_stops_a_paused_read(self):
        app = self.make(Engine("macOS"))
        self.host.hotkey[1]()
        app.pump()
        self.wait_event(app)
        app.pump()
        app.popup.paused = True
        self.host.hotkey[1]()
        app.pump()
        self.assertFalse(app.popup.playing)
        self.assertIsNone(app._read_options)

    def test_engine_progress_tells_repeated_sentences_apart(self):
        engine = Engine("macOS")
        app = self.make(engine)
        app.play("Go. Stop. Go. End.", None, start=1)  # units: "Stop.", then "Go. End."
        app._follow_progress(app._read_id, 0, 0)
        self.assertEqual(app._sentence, 1)
        app._unit_index = 1
        app._follow_progress(app._read_id, 1, 1)  # inside the second "Go."
        self.assertEqual(app._sentence, 2)
        app._track("Go.")  # text matching no longer moves it
        self.assertEqual(app._sentence, 2)

    def test_engine_progress_counts_a_split_long_sentence_once(self):
        app = self.make(Engine("macOS"))
        long = "word " * 1500 + "end."  # longer than a piece, so it arrives as fragments
        app.play("Start. " + long + " Last one. Final.", None)
        last = len(app._units) - 1
        app._unit_index = last
        app._follow_progress(app._read_id, last, app._units[last].index("Last one."))
        self.assertEqual(app._sentence, 2)

    def test_engine_progress_keeps_a_numeric_tail_of_a_split_sentence(self):
        app = self.make(Engine("macOS"))
        long = "Step " + "x" * (core.PIECE_CHARS - 6) + " 2."  # its last fragment starts with the number
        app.play("Start. " + long + " Next. Final.", None)
        last = len(app._units) - 1
        app._unit_index = last
        app._follow_progress(app._read_id, last, app._units[last].index("Next."))
        self.assertEqual(app._sentences[app._sentence], "Next.")

    def test_engine_progress_after_an_opening_of_several_sentences(self):
        app = self.make(Engine("macOS"))
        app.play("One. Two. Three. Four. Five. Six.", None)  # units: "One. Two.", then the rest
        self.assertEqual(app._units[0], "One. Two.")
        app._unit_index = 1
        app._follow_progress(app._read_id, 1, app._units[1].index("Four."))
        self.assertEqual(app._sentence, 3)

    def test_codex_captions_move_past_a_repeated_sentence(self):
        app = self.make(Engine("macOS"))
        app.play("Go. Go. Stop.", None)
        app._caption(app._read_id, 0, "Go.")
        app._caption(app._read_id, 0, "Go. Go.")
        self.assertEqual(app._sentence, 1)

    def test_codex_captions_track_a_numbered_item(self):
        app = self.make(Engine("macOS"))
        app.play("One. 2. Two. End.", None)
        app._caption(app._read_id, 0, "One. 2. Two.")
        self.assertEqual(app._sentences[app._sentence], "Two.")

    def test_offset_finds_repeated_sentences_in_order(self):
        text = "Yes. No. Yes. End"
        units = core.sentences.split(text)
        self.assertEqual([text[app_skip.offset(text, units, i):] for i in range(5)],
                         ["Yes. No. Yes. End", "No. Yes. End", "Yes. End", "End", ""])

    def wait_event(self, app):
        for _ in range(200):
            if not app.events.empty():
                return
            threading.Event().wait(0.01)

    def test_hotkey_reads_the_selection_and_again_stops_it(self):
        engine = Engine("macOS")
        app = self.make(engine)
        self.assertEqual(self.host.hotkey[0], settings.DEFAULTS["hotkey"])
        self.host.hotkey[1]()
        app.pump()
        self.wait_event(app)
        app.pump()
        self.assertTrue(app.popup.visible and app.popup.playing)
        self.assertEqual(engine.spoken, ["Selected."])
        self.host.hotkey[1]()
        app.pump()
        self.assertFalse(app.popup.playing)
        self.assertIsNone(app._read_options)

    def test_a_second_press_cancels_a_selection_read_in_flight(self):
        engine = Engine("macOS")
        app = self.make(engine)
        app._hotkey()
        app._hotkey()  # before the worker posts the selection
        self.wait_event(app)
        app.pump()
        self.assertFalse(app.popup.visible)
        self.assertEqual(engine.spoken, [])

    def test_stopping_cancels_a_selection_read_in_flight(self):
        engine = Engine("macOS")
        app = self.make(engine)
        app._hotkey()
        app.stop()
        self.wait_event(app)
        app.pump()
        self.assertFalse(app.popup.visible)
        self.assertEqual(engine.spoken, [])

    def test_a_source_change_keeps_the_whole_selection_for_skip(self):
        first, second = Engine("macOS"), Engine("Cursor")
        app = self.make(first, second)
        app.play("One. Two. Three.", None)
        app._track("One.")
        app._track("Two.")
        app._popup_source("Cursor")
        self.assertEqual(app._sentences, ["One.", "Two.", "Three."])
        self.assertEqual((first.spoken + second.spoken)[-1], "Two.")
        app.skip(-1)
        self.assertEqual((first.spoken + second.spoken)[-1], "One.")

    def test_picking_a_source_slower_than_the_speed_lowers_the_speed(self):
        codex = Engine("Codex")
        codex.max_speed = 1.0
        app = self.make(Engine("Kokoro"), codex)
        app.change("speed", 1.25)
        app._popup_source("Codex")
        self.assertEqual(app.cfg["speed"], 1.0)
        self.assertEqual(app.popup.source, "Codex")
        app.change("speed", 1.25)
        app._popup_source("Kokoro")
        app.play("One. Two.", None)
        app._popup_source("Codex")  # mid-read: the read starts over on Codex
        self.assertEqual(app.cfg["speed"], 1.0)
        self.assertTrue(codex.spoken)

    def test_hotkey_setting_rearms_the_host(self):
        app = self.make(Engine("macOS"))
        app.change("hotkey", "")
        self.assertEqual(self.host.hotkey[0], "")

    def test_stop_ignores_late_callbacks(self):
        engine = Engine("macOS")
        app = self.make(engine)
        app.play("First one. Second one.", None)
        app.stop()
        engine.done[0](None)
        app.pump()
        self.assertEqual(engine.spoken, ["First one."])

    def test_no_audio_falls_back_to_the_next_engine(self):
        codex, voice = Engine("Codex", fail=True), Engine("macOS")
        app = self.make(codex, voice)
        app.play("Hello there.", None)
        self.assertEqual((codex.spoken, voice.spoken), (["Hello there."], ["Hello there."]))

    def captions(self, app, *transcripts):
        """Feeds Codex transcript updates and runs the caption delay timers they schedule."""
        for text in transcripts:
            app.chain._active.on_caption(text)
        app.pump()
        timers = [t for t in self.host.timers if t[0] == int(core.CAPTION_DELAY_S * 1000)]
        self.host.timers = [t for t in self.host.timers if t not in timers]
        for _, fn in timers:
            fn()

    def test_codex_reads_the_whole_selection_in_one_session(self):
        engine = Engine("Codex")
        app = self.make(engine)
        app.play("First one. Second one.", None)
        app.pump()
        self.assertEqual(engine.spoken, ["First one. Second one."])
        self.assertEqual(app.popup.caption, "")  # the transcript sets it, not the selection
        self.captions(app, "First one. Sec")
        self.assertEqual(app.popup.caption, "Sec")
        self.captions(app, "First one. Second one.")
        self.assertEqual(app.popup.caption, "Second one.")

    def test_codex_captions_from_an_old_read_are_ignored(self):
        engine = Engine("Codex")
        app = self.make(engine)
        app.play("First one. Second one.", None)
        app.stop()
        self.captions(app, "First one.")
        self.assertEqual(app.popup.caption, "")

    def test_codex_captions_are_held_while_paused(self):
        engine = Engine("Codex")
        app = self.make(engine)
        app.play("First one. Second one.", None)
        self.captions(app, "First one.")
        app.popup.paused = True
        app.pause()
        self.captions(app, "First one. Sec")
        self.assertEqual(app.popup.caption, "First one.")
        app.popup.paused = False
        app.resume()
        self.assertEqual(app.popup.caption, "Sec")

    def test_codex_failover_reads_the_whole_selection_and_captions_its_first_sentence(self):
        codex, voice = Engine("Codex", fail=True), Engine("macOS")
        app = self.make(codex, voice)
        app.play("First one. Second one.", None)
        app.pump()
        self.assertEqual(voice.spoken, ["First one. Second one."])
        self.assertEqual(app.popup.caption, "First one.")

    def test_start_arms_the_trigger_and_the_update_timer(self):
        app = self.make(Engine("Codex"))
        app.start()
        self.assertTrue(hasattr(self.host, "triggered"))
        self.assertEqual(self.host.timers[0][0], 30_000)

    def test_quit_stops_through_the_host(self):
        app = self.make(Engine("Codex"))
        app._handle("quit")
        self.assertEqual(self.host.quits, 1)

    def test_macos_update_check_only_reports_the_version(self):
        app = self.make(Engine("Codex"))
        asset = {"version": "9.0.0", "name": "HarnessDiscussion.dmg"}
        with patch.object(sys, "platform", "darwin"), patch.object(sys, "frozen", True, create=True), \
                patch.object(updates, "check", return_value=asset), \
                patch.object(updates, "download") as download, patch.object(updates, "install") as install:
            app.check_update()
            for _ in range(100):
                if not app.events.empty():
                    break
                threading.Event().wait(0.01)
            app.pump()
        download.assert_not_called()
        install.assert_not_called()
        self.assertEqual(app.available_version(), "9.0.0")
        self.assertIsNone(app._pending_update)
        self.assertEqual(app.update_state(), ("v9.0.0 is available.", False))

    def test_macos_download_installs_the_found_release_and_quits(self):
        app = self.make(Engine("Codex"))
        app.update_asset = {"version": "9.0.0", "name": "HarnessDiscussion.dmg"}
        path = Path("update-1") / "HarnessDiscussion.dmg"
        with patch.object(sys, "platform", "darwin"), patch.object(sys, "frozen", True, create=True), \
                patch.object(updates, "mac_target"), patch.object(updates, "download", return_value=path), \
                patch.object(updates, "install") as install, patch.object(settings, "save"):
            self.assertTrue(app.install_update())
            for _ in range(100):
                if not app.events.empty():
                    break
                threading.Event().wait(0.01)
            app.pump()
        install.assert_called_once_with(path)
        self.assertEqual(self.host.quits, 1)

    def test_macos_download_falls_back_when_the_app_cannot_replace_itself(self):
        app = self.make(Engine("Codex"))
        app.update_asset = {"version": "9.0.0", "name": "HarnessDiscussion.dmg"}
        with patch.object(updates, "mac_target", side_effect=ValueError("release page")), \
                patch.object(updates, "download") as download:
            self.assertFalse(app.install_update())
        download.assert_not_called()
        self.assertEqual(app.update_state(), ("release page", False))


class Causes(unittest.TestCase):
    def test_the_log_names_every_cause_and_http_code_but_no_message(self):
        import urllib.error
        try:
            try:
                raise urllib.error.HTTPError("https://x", 401, "secret text", {}, None)
            except urllib.error.HTTPError as e:
                raise NoAudioError("secret text") from e
        except NoAudioError as e:
            line = core._causes(e)
        self.assertEqual(line, "NoAudioError from HTTPError 401")


if __name__ == "__main__":
    unittest.main()
