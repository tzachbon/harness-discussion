import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

import json
import gc
import tempfile
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from PIL import Image
from harness_discussion import settings
from harness_discussion.win import settings_ui


class CaptionSettings(unittest.TestCase):
    def test_defaults_and_caption_preferences_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            self.assertEqual(settings.load(path), settings.DEFAULTS)

            saved = {**settings.DEFAULTS, "caption_font_size": 16, "caption_background": True,
                     "caption_background_opacity": 0.35}
            settings.save(saved, str(path))

            self.assertEqual(settings.load(path), saved)

    def test_caption_preferences_validate_types_and_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"

            for font_size, expected in ((7, 8), (28, 24), (True, 10), (12.0, 10), ("12", 10),
                                        (10 ** 400, 24)):
                path.write_text(json.dumps({"caption_font_size": font_size}), encoding="utf-8")
                self.assertEqual(settings.load(path)["caption_font_size"], expected, repr(font_size))

            for opacity, expected in ((-0.2, 0.0), (1.5, 1.0), (True, 0.6), ("0.4", 0.6),
                                      (float("nan"), 0.6), (float("inf"), 0.6), (10 ** 400, 0.6)):
                path.write_text(json.dumps({"caption_background_opacity": opacity}), encoding="utf-8")
                self.assertEqual(settings.load(path)["caption_background_opacity"], expected, repr(opacity))

            for background in ("false", 1, None):
                path.write_text(json.dumps({"caption_background": background}), encoding="utf-8")
                self.assertFalse(settings.load(path)["caption_background"], repr(background))


class CaptionSettingsUi(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = None

    def tearDown(self):
        if self.window and self.window.alive:
            self.window.close()
        root = self.root
        root.destroy()
        self.window = self.root = None
        root = None
        gc.collect()  # Tk cycles must release their interpreter on its owning thread.

    def make_window(self, cfg, on_change, on_test=lambda _text: None, **kw):
        with patch.object(settings_ui.settings, "startup_enabled", return_value=False), \
                patch.object(settings_ui.SettingsWindow, "focus", lambda self: self._refresh_stt()), \
                patch.object(settings_ui, "stt_connection", return_value=("", "")):
            self.window = settings_ui.SettingsWindow(self.root, cfg, Image.new("RGBA", (32, 32)),
                                                    on_change, on_test, **kw)
            return self.window

    def test_background_controls_opacity_and_keeps_its_choice(self):
        cfg = dict(settings.DEFAULTS)
        self.make_window(cfg, lambda *args: None)
        try:
            self.assertTrue(self.window.caption_opacity_scale.instate(["disabled"]))
            chosen = self.window.caption_opacity.get()
            self.window.caption_background_check.invoke()
            self.assertFalse(self.window.caption_opacity_scale.instate(["disabled"]))
            self.window.caption_background_check.invoke()
            self.assertTrue(self.window.caption_opacity_scale.instate(["disabled"]))
            self.assertEqual(self.window.caption_opacity.get(), chosen)
        finally:
            self.window.close()

    def test_the_sidebar_shows_one_page_and_marks_voices_while_a_source_needs_a_sign_in(self):
        self.make_window(dict(settings.DEFAULTS), lambda *args: None, statuses=lambda: {"Cursor": "signed_out"})
        self.assertEqual([bool(p.winfo_manager()) for p in self.window.pages],
                         [i == 0 for i in range(len(settings_ui.PAGES))])
        self.assertEqual(str(self.window.nav[0]["image"]), str(self.window._tiles["Voices"][1]))
        speech_page = settings_ui.PAGES.index("Speech to Text")
        self.window.nav[speech_page].invoke()
        self.assertEqual([bool(p.winfo_manager()) for p in self.window.pages],
                         [i == speech_page for i in range(len(settings_ui.PAGES))])
        self.assertEqual(self.window.title["text"], "Speech to Text")
        self.window.statuses = lambda: {"Cursor": "ready"}
        self.window.refresh_sources()
        self.assertEqual(str(self.window.nav[0]["image"]), str(self.window._tiles["Voices"][0]))

    def test_the_summarize_page_shows_each_state_and_its_button(self):
        state, clicks = ["missing", None], []
        app = SimpleNamespace(summarize_state=lambda: tuple(state), summarize_action=lambda: clicks.append(1))
        w = self.make_window(dict(settings.DEFAULTS), lambda *args: None, summarizer=app)
        w.nav[3].invoke()
        self.assertEqual(w.title["text"], "Summarize")
        shown = lambda: (w.summary_status["text"], w.summary_button["text"], bool(w.summary_bar.winfo_manager()))
        self.assertEqual(shown(), ("Not downloaded", "Download", False))
        state[:] = ["missing", 0.42]
        w.refresh_summarize()
        self.assertEqual(shown(), ("Downloading 42% of 2.5 GB", "Cancel", True))
        state[:] = ["ready", None]
        w.refresh_summarize()
        self.assertEqual(shown(), ("Ready", "Remove", False))
        w.summary_button.invoke()
        self.assertEqual(clicks, [1])
        state[:] = ["unsupported", None]
        w.refresh_summarize()
        self.assertFalse(w.summary_button.winfo_manager())

    def test_settings_scroll_to_fit_small_work_area(self):
        area = SimpleNamespace(left=0, top=0, right=1920, bottom=480)
        with patch.object(settings_ui, "work_area", return_value=area):
            self.make_window(dict(settings.DEFAULTS), lambda *args: None)
        self.window.win.deiconify()
        self.root.update()

        self.assertLessEqual(self.window.win.winfo_height(), area.bottom - area.top - 48)
        self.assertEqual(self.window._scrollbar.winfo_manager(), "pack")
        self.assertLess(self.window._scroll_canvas.winfo_height(), self.window.content.winfo_reqheight())
        self.assertTrue(self.window.close_button.winfo_viewable())

        self.window.show_page(settings_ui.PAGES.index("Speech to Text"))
        self.window._scroll_canvas.yview_moveto(1)
        self.root.update_idletasks()
        self.assertTrue(self.window.stt_status.winfo_viewable())
        content_bottom = self.window.stt_status.winfo_rooty() + self.window.stt_status.winfo_height()
        viewport_bottom = self.window._scroll_canvas.winfo_rooty() + self.window._scroll_canvas.winfo_height()
        self.assertLessEqual(content_bottom, viewport_bottom)

    def test_fractional_opacity_change_rounds_applies_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            cfg = dict(settings.DEFAULTS)

            def on_change(key, value, save=True):
                cfg[key] = value
                if save:
                    settings.save(cfg, str(path))

            self.make_window(dict(cfg), on_change)
            self.window.caption_background_check.invoke()
            self.window.caption_opacity.set(73.4)
            self.assertEqual(self.window.caption_opacity_label["text"], "73%")
            self.assertEqual(cfg["caption_background_opacity"], 0.73)
            self.window.close()
            self.assertEqual(settings.load(path)["caption_background_opacity"], 0.73)

    def test_close_and_test_voice_flush_pending_speed_and_caption_settings(self):
        for flush_with_test_voice in (False, True):
            with self.subTest(flush_with_test_voice=flush_with_test_voice), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "settings.json"
                cfg = dict(settings.DEFAULTS)
                test_voices = []

                def on_change(key, value, save=True):
                    cfg[key] = value
                    if save:
                        settings.save(cfg, str(path))

                self.make_window(dict(cfg), on_change, test_voices.append)
                try:
                    self.window.speed.set(1.35)
                    self.window._speed_changed("1.35")
                    self.window.caption_font_size.delete(0, "end")
                    self.window.caption_font_size.insert(0, "18")
                    self.window.caption_background_check.invoke()
                    self.window.caption_opacity.set(73)

                    if flush_with_test_voice:
                        self.window._test()
                    else:
                        self.window.close()

                    saved = settings.load(path)
                    self.assertEqual(saved["speed"], 1.35)
                    self.assertEqual(saved["caption_font_size"], 18)
                    self.assertTrue(saved["caption_background"])
                    self.assertEqual(saved["caption_background_opacity"], 0.73)
                    self.assertEqual(bool(test_voices), flush_with_test_voice)
                finally:
                    if self.window.alive:
                        self.window.close()

    def sources_window(self, statuses=None):
        changes, played = [], []
        engines = [SimpleNamespace(name="Cursor", voices=(("eve", "Eve"), ("ara", "Ara")), default_voice="ara"),
                   SimpleNamespace(name="Codex"), SimpleNamespace(name="Edge"), SimpleNamespace(name="Windows")]
        self.make_window(dict(settings.DEFAULTS), lambda key, value, save=True: changes.append((key, value)),
                         engines=engines, statuses=lambda: statuses or {}, on_play=played.append)
        return self.window.source_rows, changes, played

    def test_each_status_renders_its_text(self):
        rows, _, _ = self.sources_window({"Cursor": "signed_out", "Codex": "missing", "Edge": "ready"})
        self.assertEqual([rows[n][1]["text"] for n in ("Cursor", "Codex", "Edge", "Windows")],
                         ["Not signed in", "Not installed", "Ready", "Checking…"])
        self.window.statuses = lambda: {"Cursor": "expired"}
        self.window.refresh_sources()
        self.assertEqual(rows["Cursor"][1]["text"], "Sign-in expired")
        self.assertEqual((rows["Codex"][3][2]["text"], rows["Edge"][2]), ("Default voice", None))

    def test_up_down_and_enable_save_the_sources(self):
        rows, changes, _ = self.sources_window()
        self.assertTrue(rows["Cursor"][3][4].instate(["disabled"]))  # already first
        rows["Codex"][3][4].invoke()
        self.assertEqual(changes[-1], ("sources", [["Codex", True], ["Cursor", True], ["Kokoro", True], ["Edge", True],
                                                   ["Windows", True]]))
        self.assertEqual(int(rows["Codex"][3][0].grid_info()["row"]), 0)
        rows["Codex"][3][5].invoke()
        self.assertEqual([n for n, _ in changes[-1][1]], ["Cursor", "Codex", "Kokoro", "Edge", "Windows"])
        rows["Edge"][3][0].invoke()
        self.assertEqual(changes[-1], ("sources", [["Cursor", True], ["Codex", True], ["Kokoro", True], ["Edge", False],
                                                   ["Windows", True]]))

    def test_cursor_voice_saves_under_its_name_and_test_reads_one_source(self):
        rows, changes, played = self.sources_window()
        voice = rows["Cursor"][2]
        self.assertEqual(voice.get(), "Ara")  # Cursor's default before a choice
        self.window.win.deiconify()
        self.root.update()
        voice.current(0)
        voice.event_generate("<<ComboboxSelected>>")
        self.assertEqual(changes[-1], ("voices", {"Cursor": "eve"}))
        self.window.cfg["voices"] = {"Cursor": "gone"}  # a voice Cursor no longer offers
        self.window.refresh_sources()
        self.assertEqual(voice.get(), "Ara")
        rows["Edge"][3][3].invoke()
        self.assertEqual(played, ["Edge"])

    def test_the_welcome_header_shows_above_sources_only_on_first_run(self):
        self.make_window(dict(settings.DEFAULTS), lambda *args: None)
        self.assertIsNone(self.window.welcome)
        self.window.close()
        self.make_window(dict(settings.DEFAULTS), lambda *args: None, welcome=True)
        children = self.window.sources_frame.master.pack_slaves()
        self.assertEqual(self.window.welcome["text"], "Pick the voices Harness Discussion uses.")
        self.assertEqual(children.index(self.window.welcome) + 1, children.index(self.window.sources_frame))


if __name__ == "__main__":
    unittest.main()
