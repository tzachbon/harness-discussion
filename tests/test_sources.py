"""The ordered voice sources: the saved list, the chain order, and how core applies them."""
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from harness_discussion import codex_rt, core, settings, sources
from harness_discussion.audio import NoAudioError
from harness_discussion.engines import Chain

from test_core import CoreTest, Engine, FakeHost

ALL = settings.DEFAULT_SOURCES


class Load(unittest.TestCase):
    def load(self, saved):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                json.dump(saved, f)
            return settings.load(path)

    def test_defaults_put_cursor_first_and_drop_the_engine_key(self):
        self.assertEqual(ALL[:2], ["Cursor", "Codex"])
        self.assertEqual(settings.DEFAULTS["sources"], [[n, True] for n in ALL])
        self.assertEqual(settings.DEFAULTS["voices"], {})
        self.assertNotIn("engine", settings.DEFAULTS)

    def test_a_legacy_engine_goes_first_and_the_rest_are_disabled(self):
        s = self.load({"engine": "Codex"})
        self.assertEqual(s["sources"], [["Codex", True]] + [[n, False] for n in ALL if n != "Codex"])
        self.assertNotIn("engine", s)

    def test_a_legacy_auto_keeps_the_default_order(self):
        self.assertEqual(self.load({"engine": None})["sources"], [[n, True] for n in ALL])
        self.assertEqual(self.load({"engine": "Bogus"})["sources"], [[n, True] for n in ALL])

    def test_unknown_and_duplicate_names_are_dropped_and_missing_ones_appended(self):
        s = self.load({"sources": [["Codex", False], ["Bogus", True], ["Codex", True], "junk",
                                   [["Cursor"], True], ["Cursor", "yes"]]})
        self.assertEqual(s["sources"], [["Codex", False]] + [[n, True] for n in ALL if n != "Codex"])
        for junk in (None, "Codex", {"Codex": True}):
            self.assertEqual(self.load({"sources": junk})["sources"], [[n, True] for n in ALL])

    def test_saved_sources_ignore_a_legacy_engine(self):
        saved = [[n, i == 0] for i, n in enumerate(reversed(ALL))]
        self.assertEqual(self.load({"sources": saved, "engine": "Codex"})["sources"], saved)

    def test_voices_keep_only_text(self):
        self.assertEqual(self.load({"voices": {"Cursor": "eve", "Codex": 3}})["voices"], {"Cursor": "eve"})
        self.assertEqual(self.load({"voices": ["eve"]})["voices"], {})

    def test_local_only_turns_off_every_online_service_and_keeps_other_settings(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            settings.save({"speed": 1.5, "prefetch": True}, path)
            settings.local_only(path)
            s = settings.load(path)
        on = {n for n, enabled in s["sources"] if enabled}
        self.assertEqual(on, {"Kokoro", "Windows", "macOS"} & set(ALL))
        self.assertEqual([n for n, enabled in s["writer_sources"] if enabled], ["Local"])
        self.assertFalse(any(enabled for _, enabled in s["stt_sources"]))
        self.assertFalse(s["prefetch"])
        self.assertEqual(s["speed"], 1.5)

    def test_local_only_never_leaves_an_empty_order_that_falls_back_to_online_voices(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            settings.save({"sources": [[n, n == "Cursor"] for n in ALL], "stt_server": True}, path)
            settings.local_only(path)
            s = settings.load(path)
        self.assertEqual({n for n, enabled in s["sources"] if enabled}, {"Kokoro", "Windows", "macOS"} & set(ALL))
        self.assertFalse(s["stt_server"])  # the speech-to-text server sends audio to OpenAI


class Fake:
    speed = 1.0

    def __init__(self, name, err=None, max_speed=None):
        self.name, self.err, self.calls = name, err, 0
        if max_speed:
            self.max_speed = max_speed

    def speak(self, text, tag, on_done, on_audio):
        self.calls += 1
        on_done(self.err)

    def stop(self):
        pass


class Order(unittest.TestCase):
    def names(self, chain):
        return [e.name for e in chain.order()]

    def test_order_follows_enabled_and_skips_disabled(self):
        chain = Chain([Fake("Cursor"), Fake("Codex"), Fake("Edge")])
        self.assertEqual(self.names(chain), ["Cursor", "Codex", "Edge"])
        chain.enabled = ["Edge", "Cursor"]
        self.assertEqual(self.names(chain), ["Edge", "Cursor"])
        chain.only = "Codex"  # a pin or a Test button still wins
        self.assertEqual(self.names(chain), ["Codex"])

    def test_the_speed_window_skips_codex_and_keeps_cursor(self):
        engines = [Fake("Cursor", max_speed=2.0), Fake("Codex", max_speed=1.0), Fake("Slow")]
        engines[2].min_speed = 1.75
        for e in engines:
            e.speed = 1.5
        chain = Chain(engines)
        self.assertEqual(self.names(chain), ["Cursor"])
        chain.enabled = ["Codex"]  # nothing enabled fits: the enabled ones still read
        self.assertEqual(self.names(chain), ["Codex"])

    def test_with_every_source_disabled_the_read_uses_all(self):
        chain = Chain([Fake("Cursor"), Fake("Codex")])
        chain.enabled = []
        self.assertEqual(self.names(chain), ["Cursor", "Codex"])

    def test_a_source_known_not_ready_is_skipped_for_continuity_and_prepare(self):
        class Prep(Fake):
            def prepare(self, tag, text=None):
                self.prepared = text or "connect"
        cursor, codex = Prep("Cursor"), Prep("Codex")
        chain = Chain([cursor, codex, Fake("Edge")])
        self.assertEqual((self.names(chain)[0], chain.continuous()), ("Cursor", False))  # unknown: kept
        chain.statuses = {"Cursor": "ready"}
        self.assertEqual(self.names(chain)[0], "Cursor")
        chain.statuses = {"Cursor": "signed_out", "Codex": "ready"}
        self.assertEqual((self.names(chain), chain.continuous()), (["Codex", "Edge"], True))
        chain.prepare(None)
        self.assertEqual((getattr(cursor, "prepared", None), codex.prepared), (None, "connect"))
        chain.only = "Cursor"  # a Test button still reaches it
        self.assertEqual(self.names(chain), ["Cursor"])
        chain.only, chain.enabled = None, ["Cursor"]  # nothing ready: the enabled ones still read
        self.assertEqual(self.names(chain), ["Cursor"])

    def test_on_engine_fires_once_per_attempt_with_the_name(self):
        chain, started = Chain([Fake("Cursor", NoAudioError("x")), Fake("Codex")]), []
        chain.on_engine = started.append
        chain.speak("hi", None, lambda err: None)
        self.assertEqual(started, ["Cursor", "Codex"])


class CodexStatus(unittest.TestCase):
    def test_missing_only_without_the_cli(self):
        engine = codex_rt.CodexEngine()
        self.addCleanup(engine.close)
        with patch.object(codex_rt, "codex_exe", side_effect=FileNotFoundError):
            self.assertEqual(engine.status(), "missing")
        with patch.object(codex_rt, "codex_exe", return_value="/bin/codex"):
            self.assertEqual(engine.status(), "ready")


class Voiced(Engine):
    voices, voice = (("ara", "Ara"), ("eve", "Eve")), None


class CoreSources(unittest.TestCase):
    make = CoreTest.make

    def test_sources_set_the_order_and_clear_a_pin(self):
        app = self.make(Engine("Cursor"), Engine("Codex"))
        app.chain.only = "Cursor"
        app.change("sources", [["Codex", True], ["Cursor", False]])
        self.assertEqual((app.chain.enabled, app.chain.only), (["Codex"], None))

    def test_sources_wait_for_the_selection_to_end(self):
        cursor, codex = Engine("Cursor"), Engine("Codex")
        app = self.make(cursor, codex)
        app.change("sources", [["Cursor", True], ["Codex", True]])
        app.play("One. Two.", None)
        app.change("sources", [["Codex", True], ["Cursor", True]])
        self.assertEqual(app.chain.enabled, ["Cursor", "Codex"])  # captured for the whole selection
        app.stop()
        self.assertEqual(app.chain.enabled, ["Codex", "Cursor"])

    def test_change_mirrors_sources_to_an_open_settings_window(self):
        app = self.make(Engine("Cursor"))
        seen = []
        app.settings_win = type("Win", (), {"alive": True, "set_sources": lambda self, v: seen.append(v)})()
        app.change("sources", [["Cursor", True]])
        self.assertEqual(seen, [[["Cursor", True]]])

    def test_voices_are_checked_against_the_engine(self):
        cursor, local = Voiced("Cursor"), Engine("macOS")
        app = self.make(cursor, local)
        app.change("voices", {"Cursor": "eve", "macOS": "x"})
        self.assertEqual(cursor.voice, "eve")
        self.assertFalse(hasattr(local, "voice"))
        app.change("voices", {"Cursor": "bogus"})
        self.assertIsNone(cursor.voice)

    def test_play_only_reads_through_that_source_then_unpins(self):
        cursor, codex = Engine("Cursor"), Engine("Codex")
        app = self.make(cursor, codex)
        app.play("This is Codex.", None, only="Codex")
        self.assertEqual((cursor.spoken, codex.spoken), ([], ["This is Codex."]))
        codex.done[0](None)
        app.pump()
        self.assertIsNone(app.chain.only)

    def test_engine_events_and_the_status_cache(self):
        cursor = Engine("Cursor", fail=True)
        cursor.status = lambda: "signed_out"
        app = self.make(cursor, Engine("Codex"))
        refreshed = []
        app.settings_win = type("Win", (), {"alive": True, "refresh_sources": lambda self: refreshed.append(1)})()
        self.assertEqual(app.statuses, {})
        app.play("Hello.", None)
        self.assertEqual([app.events.get_nowait() for _ in range(2)], [("engine", "Cursor"), ("engine", "Codex")])
        app.refresh_statuses()
        for _ in range(200):
            if not app.events.empty():
                break
            threading.Event().wait(0.01)
        app.pump()
        self.assertEqual(app.statuses, {"Cursor": "signed_out", "Codex": "ready"})
        self.assertIs(app.chain.statuses, app.statuses)
        self.assertEqual(refreshed, [1])

    def wait_for_statuses(self, app):
        for _ in range(200):
            if ("statuses",) in list(app.events.queue):
                return app.pump()
            threading.Event().wait(0.01)
        self.fail("no status check finished")

    def test_a_shown_selection_checks_the_statuses_again(self):
        cursor = Engine("Cursor")
        cursor.status = lambda: "ready"
        app = self.make(cursor, Engine("Codex"))
        app._handle("show", "Hello.", 1, 1)
        self.wait_for_statuses(app)
        self.assertEqual(app.chain.statuses["Cursor"], "ready")

    def test_one_status_check_at_a_time(self):
        release, calls = threading.Event(), []
        cursor = Engine("Cursor")
        cursor.status = lambda: (calls.append(1), release.wait(5), "expired")[-1]
        app = self.make(cursor)
        app.refresh_statuses()
        app.refresh_statuses()  # skipped: the first one is still running
        release.set()
        self.wait_for_statuses(app)
        app.refresh_statuses()  # the first one finished, so this one runs
        self.wait_for_statuses(app)
        self.assertEqual((len(calls), app.statuses), (2, {"Cursor": "expired"}))


def wait_for_event(app):
    for _ in range(200):
        if not app.events.empty():
            break
        threading.Event().wait(0.01)
    app.pump()


class Bar(unittest.TestCase):
    """The floating bar's source button and the first-run welcome."""
    make = CoreTest.make

    def test_label_shows_the_engine_while_reading_then_the_first_ready_source(self):
        app = self.make(Engine("Cursor", fail=True), Engine("Codex"))
        self.assertEqual(app.popup.source, "Cursor")  # no status yet: the first enabled source
        app.play("Hello.", None)
        app.pump()
        self.assertEqual(app.popup.source, "Codex")  # Cursor had no audio, Codex speaks
        app.chain._active.done[0](None)
        app.pump()
        self.assertEqual(app.popup.source, "Cursor")

    def test_a_source_not_ready_is_skipped_by_the_label_and_the_menu(self):
        cursor, edge = Engine("Cursor"), Engine("Edge")
        cursor.status = lambda: "signed_out"
        app = self.make(cursor, Engine("Codex"), edge)
        app.change("sources", [["Cursor", True], ["Codex", False], ["Edge", True]])
        app.refresh_statuses()
        wait_for_event(app)
        self.assertEqual(app.popup.source, "Edge")
        self.assertEqual(app.popup.callbacks["sources"](), [("Edge", None), ("Cursor", "Sign in…")])

    def test_with_no_enabled_source_ready_the_menu_lists_what_a_read_would_use(self):
        cursor, codex = Engine("Cursor"), Engine("Codex")
        cursor.status = lambda: "signed_out"
        app = self.make(cursor, codex, Engine("Edge"))
        app.change("sources", [["Cursor", True], ["Codex", False], ["Edge", False]])
        app.refresh_statuses()
        wait_for_event(app)
        self.assertEqual((app.popup.callbacks["sources"](), app.popup.source), ([("Cursor", "Sign in…")], "Cursor"))

    def test_picking_a_source_that_needs_a_sign_in_runs_it_and_checks_again(self):
        cursor = Engine("Cursor")
        cursor.status = lambda: "signed_out"
        app = self.make(cursor, Engine("Codex"))
        app.refresh_statuses()
        wait_for_event(app)
        order = [list(s) for s in app.cfg["sources"]]
        with patch("webbrowser.open") as browser:
            app.popup.callbacks["on_source"]("Cursor")
        browser.assert_called_once_with("cursor://")
        self.assertEqual(app.cfg["sources"], order)  # a fix does not reorder
        rechecks = [ms for ms, fn in app.host.timers if fn == app.refresh_statuses]
        self.assertEqual(rechecks, [s * 1000 for s in sources.RECHECK_S])

    def test_picking_a_source_moves_it_first_saves_and_mirrors_settings(self):
        app = self.make(Engine("Cursor"), Engine("Codex"), Engine("Edge"))
        app.change("sources", [["Cursor", True], ["Codex", False], ["Edge", True]])
        seen = []
        app.settings_win = type("Win", (), {"alive": True, "set_sources": lambda self, v: seen.append(v)})()
        settings.save.reset_mock()
        app.popup.callbacks["on_source"]("Edge")
        moved = [["Edge", True], ["Cursor", True], ["Codex", False]]
        self.assertEqual((app.cfg["sources"], seen, app.popup.source), (moved, [moved], "Edge"))
        settings.save.assert_called_once()

    def test_picking_a_source_mid_read_reads_the_rest_with_it(self):
        cursor, macos = Engine("Cursor"), Engine("macOS")
        app = self.make(cursor, macos)
        app.popup.set_playing(True)
        app.play("One. Two. Three.", "he-IL")
        app.pump()
        self.assertEqual(cursor.spoken, ["One."])
        app.popup.callbacks["on_source"]("macOS")
        app.pump()
        self.assertEqual(macos.spoken, ["One."])  # the unfinished piece starts over
        macos.done[0](None)
        app.pump()
        self.assertEqual((cursor.spoken, macos.spoken), (["One."], ["One.", "Two. Three."]))
        self.assertEqual((app.popup.playing, app.popup.source, app.chain.enabled), (True, "macOS", ["macOS", "Cursor"]))
        self.assertEqual(app._read_options[0], "he-IL")

    def test_picking_a_source_mid_piece_starts_from_the_sentence_on_screen(self):
        cursor, macos = Engine("Cursor"), Engine("macOS")
        app = self.make(cursor, macos)
        app.popup.set_playing(True)
        app.play("One. Two. Three.", None)
        app.pump()
        cursor.done[0](None)
        app.pump()
        self.assertEqual((cursor.spoken, app.popup.caption), (["One.", "Two. Three."], "Two."))
        app.host.timers[-1][1]()  # the caption steps to the next sentence
        app.popup.callbacks["on_source"]("macOS")
        app.pump()
        self.assertEqual(macos.spoken, ["Three."])

    def test_picking_a_source_while_paused_keeps_the_read_paused(self):
        cursor, macos = Engine("Cursor"), Engine("macOS")
        app = self.make(cursor, macos)
        app.popup.set_playing(True)
        app.play("One. Two.", None)
        app.pump()
        app.popup.set_playing(True, paused=True)
        app.pause()
        app.popup.callbacks["on_source"]("macOS")
        app.pump()
        self.assertEqual((macos.spoken, macos.paused.is_set()), (["One."], True))
        self.assertEqual((app.popup.playing, app.popup.paused), (True, True))

    def test_picking_a_source_while_idle_does_not_start_a_read(self):
        cursor, macos = Engine("Cursor"), Engine("macOS")
        app = self.make(cursor, macos)
        app.popup.callbacks["on_source"]("macOS")
        self.assertEqual((macos.spoken, app.popup.playing), ([], False))

    def test_picking_a_turned_off_source_turns_it_on(self):
        app = self.make(Engine("Cursor"), Engine("Codex"))
        app.change("sources", [["Cursor", False], ["Codex", False]])
        app.popup.callbacks["on_source"]("Codex")
        self.assertEqual(app.cfg["sources"], [["Codex", True], ["Cursor", False]])

    def test_first_run_opens_settings_with_the_welcome_and_a_second_start_does_not(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, "settings.json")
        def write(cfg):
            with open(path, "w") as f:
                json.dump(cfg, f)
        with patch.object(settings, "PATH", path), patch.object(settings, "save", side_effect=write):
            first = core.App(FakeHost([Engine("Codex")]))
            first.pump()
            self.assertEqual(first.host.opened, [True])
            first.popup.callbacks["on_settings"]()
            first.pump()
            self.assertEqual(first.host.opened, [True, False])  # opened from the bar: no welcome
            second = core.App(FakeHost([Engine("Codex")]))
            second.pump()
            self.assertEqual(second.host.opened, [])

    def test_settings_written_by_the_installer_still_get_the_first_run_welcome(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, "settings.json")
        load, save = settings.load, settings.save  # never the real settings file
        with patch.object(settings, "PATH", path), patch.object(settings, "load", lambda p=path: load(p)),                 patch.object(settings, "save", lambda s, p=path: save(s, p)):
            settings.local_only(path)
            settings.local_only(path)  # setup run again before the app first starts
            first = core.App(FakeHost([Engine("Windows")]))
            first.pump()
            self.assertEqual(first.host.opened, [True])
            self.assertEqual(first.cfg["sources"], load(path)["sources"])  # the privacy choice is kept
            second = core.App(FakeHost([Engine("Windows")]))
            second.pump()
            self.assertEqual(second.host.opened, [])


del CoreTest  # imported for its helpers; its tests run in test_core


if __name__ == "__main__":
    unittest.main()
