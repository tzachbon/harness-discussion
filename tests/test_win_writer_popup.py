"""Windows Writer bar layouts and the actions retained in overflow."""
import gc
import ctypes
import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

from harness_discussion.win import popup as win_popup
from harness_discussion.writer import BUILTIN_MODES


class EscapeDouble:
    instances = []

    def __init__(self, on_escape, *, suppress, autostart):
        self.on_escape, self.suppress = on_escape, suppress
        self.active = autostart
        self.starts = self.stops = 0
        self.instances.append(self)

    def start(self):
        self.starts += 1
        self.active = True

    def stop(self):
        self.stops += 1
        self.active = False


class WriterBar(unittest.TestCase):
    def setUp(self):
        self.addCleanup(self.cleanup_tk)
        self.root = tk.Tk()
        self.root.withdraw()
        self.calls = []
        EscapeDouble.instances = []
        self.escape_patcher = patch.object(win_popup, "Escape", EscapeDouble, create=True)
        self.escape_patcher.start()
        self.addCleanup(self.escape_patcher.stop)
        callback = lambda *args: None
        self.bar = win_popup.Popup(self.root, 1, on_play=callback, on_stop=callback, on_pause=callback,
                                   on_resume=callback, on_lang=callback, on_speed=callback,
                                   on_settings=lambda: self.calls.append("settings"), on_source=callback,
                                   sources=lambda: [], on_skip=lambda direction: self.calls.append(("skip", direction)),
                                   on_escape=lambda: self.calls.append("escape"))
        self.addCleanup(self.bar.hide)

    def cleanup_tk(self):
        self.bar = None
        EscapeDouble.instances.clear()
        root, self.root = self.root, None
        root.destroy()
        del root
        gc.collect()

    def test_close_button_stays_available_with_the_escape_shortcut(self):
        self.assertIn(self.bar.close_btn, self.bar.win.pack_slaves())
        escape = EscapeDouble.instances[0]
        self.assertFalse(escape.active)
        self.bar.show("Selected text", 140, 140)
        self.root.update()
        self.assertTrue(escape.active)
        self.assertIn(self.bar.close_btn, self.bar.win.pack_slaves())
        self.assertEqual(self.bar.win.pack_slaves(), [self.bar.btn, self.bar.ai_btn, self.bar.source_btn,
                         self.bar.speed_btn, self.bar.more_btn, self.bar.close_btn])

    def test_popup_escape_is_active_only_for_the_visible_lifetime(self):
        escape = EscapeDouble.instances[0]
        self.assertFalse(escape.active)
        self.assertFalse(escape.suppress())
        self.bar.show("Selected text", 140, 140)
        self.root.update()
        self.assertTrue(self.bar.visible)
        self.assertGreater(self.bar.anchor()[2], 0)
        self.assertTrue(escape.active)
        self.assertTrue(escape.suppress())
        escape.on_escape()
        self.assertEqual(self.calls, ["escape"])
        self.bar.hide()
        self.assertFalse(self.bar.visible)
        self.assertFalse(escape.active)
        self.assertFalse(escape.suppress())

    def test_showing_the_bar_preserves_the_source_window_and_selection(self):
        field = tk.Entry(self.root)
        field.pack()
        field.insert(0, "Keep this selection")
        self.root.deiconify()
        field.focus_force()
        field.selection_range(0, "end")
        self.root.update()
        get_foreground = ctypes.windll.user32.GetForegroundWindow
        get_foreground.restype = ctypes.c_void_p
        foreground = get_foreground()
        self.bar.show("Keep this selection", 140, 140)
        self.root.update()
        self.assertEqual(get_foreground(), foreground)
        self.assertTrue(field.selection_present())

    def test_playback_controls_stay_inside_the_screen_when_the_bar_expands(self):
        area = SimpleNamespace(left=0, top=0, right=600, bottom=800)
        with patch.object(win_popup, "work_area", return_value=area):
            self.bar.show("Selected text", 590, 400)
            self.root.update()
            self.bar.set_playing(True)
            self.root.update()
            self.assertLessEqual(self.bar.win.winfo_x() + self.bar.win.winfo_width(), area.right)
            self.assertGreaterEqual(self.bar.win.winfo_x(), area.left)

    def test_a_wider_voice_label_stays_inside_the_screen_after_resizing(self):
        area = SimpleNamespace(left=0, top=0, right=600, bottom=800)
        with patch.object(win_popup, "work_area", return_value=area):
            self.bar.show("Selected text", 590, 400)
            self.root.update()
            width = self.bar.win.winfo_width()
            self.bar.set_source("A much longer voice source")
            self.root.update()
            self.assertGreater(self.bar.win.winfo_width(), width)
            self.assertLessEqual(self.bar.win.winfo_x() + self.bar.win.winfo_width(), area.right)
            self.assertGreaterEqual(self.bar.win.winfo_x(), area.left)

    def test_popup_escape_stops_when_the_window_is_destroyed(self):
        escape = EscapeDouble.instances[0]
        self.bar.show("Selected text", 140, 140)
        self.assertTrue(escape.active)
        self.bar.destroy()
        self.assertFalse(escape.active)

    def test_destroy_cancels_speech_animation_before_destroying_its_widgets(self):
        self.root.tk.eval('set writer_bg_errors {}; proc bgerror {msg} {lappend ::writer_bg_errors $msg}')
        self.bar.show("Selected text", 140, 140)
        self.root.update()
        self.bar.set_loading(True)
        self.bar.destroy()
        self.root.after(100, self.root.quit)
        self.root.mainloop()
        self.assertFalse(self.root.tk.call("set", "writer_bg_errors"))

    def test_writing_layout_needs_editability_and_rewriting_keeps_speech_loading_separate(self):
        callback = lambda text, name: self.calls.append((text, name))
        bar = win_popup.Popup(self.root, 1, on_play=lambda: None, on_stop=lambda: None,
                              on_pause=lambda: None, on_resume=lambda: None, on_lang=lambda tag: None,
                              on_speed=lambda speed: None, on_settings=lambda: None, on_source=lambda name: None,
                              sources=lambda: [], on_skip=lambda direction: None,
                              on_rewrite=callback, modes=lambda: ["Fix errors", "Email"],
                              on_escape=lambda: None)
        self.addCleanup(bar.hide)
        bar.show("Read only", 140, 140)
        with patch.object(bar.overflow, "post"):
            bar.more_btn.command()
        self.assertEqual(bar.overflow.entrycget(bar.overflow.index("Rewrite"), "state"), "normal")
        bar.show("Editable", 140, 140, editable=True)
        with patch.object(bar.overflow, "post"):
            bar.more_btn.command()
        self.assertEqual(bar.overflow.entrycget(bar.overflow.index("Rewrite"), "state"), "normal")
        bar.mode_menu.invoke("Email")
        self.assertEqual(self.calls, [("Editable", "Email")])
        bar.set_rewriting(True)
        self.assertEqual(bar.rewrite_btn.parts[1]["text"], "✎ Rewriting…")
        bar.rewrite_btn.command()
        self.assertEqual(self.calls, [("Editable", "Email")])
        bar.set_loading(True)
        self.assertTrue(bar.loading)
        self.assertIn(bar.spinner, bar.win.pack_slaves())
        bar.set_rewriting(False)
        self.assertTrue(bar.loading)
        self.assertEqual(bar.rewrite_btn.parts[1]["text"], "✎ Email")
        bar.set_loading(False)
        self.assertFalse(bar.loading)
        self.assertNotIn(bar.spinner, bar.win.pack_slaves())
        self.assertEqual(bar.btn.parts[1]["text"], win_popup.PLAY)
        bar.set_editable(False)
        bar.mode_menu.invoke("Email")
        self.assertEqual(self.calls, [("Editable", "Email"), ("Editable", "Email")])
        bar.set_writer(False)
        bar.show("Writer off", 140, 140, editable=True)
        with patch.object(bar.overflow, "post"):
            bar.more_btn.command()
        self.assertEqual(bar.overflow.entrycget(bar.overflow.index("Rewrite"), "state"), "disabled")

    def test_shared_writer_constructor_passes_current_text_and_selected_name(self):
        callback = lambda *args: None
        bar = win_popup.Popup(self.root, 1, on_play=callback, on_stop=callback, on_pause=callback,
                              on_resume=callback, on_lang=callback, on_speed=callback,
                              on_settings=callback, on_source=callback, sources=lambda: [], on_skip=callback,
                              on_rewrite=lambda text, name: self.calls.append((text, name)),
                              modes=lambda: ["Fix errors", "Email"], on_escape=lambda: None)
        self.addCleanup(bar.hide)
        bar.show("Current selection", 140, 140, editable=True)
        self.root.update()
        with patch.object(bar.mode_menu, "post"):
            bar.show_modes()
        self.addCleanup(bar.mode_menu.unpost)
        bar.mode_menu.invoke("Email")
        self.assertEqual(self.calls, [("Current selection", "Email")])
        self.assertEqual(bar.rewrite_btn.parts[1]["text"], "✎ Email")
        bar.set_last_mode("Message")
        self.assertEqual(bar.rewrite_btn.parts[1]["text"], "✎ Message")

    def test_unknown_editability_keeps_explicit_overflow_rewrite_available(self):
        self.bar.configure_writer(self.calls.append, lambda: BUILTIN_MODES, lambda: "Fix errors")
        self.bar.show("Unknown editability", 140, 140, editable=False)
        self.assertNotIn(self.bar.rewrite_btn, self.bar.win.pack_slaves())
        with patch.object(self.bar.overflow, "post"):
            self.bar.more_btn.command()
        self.assertEqual(self.bar.overflow.entrycget(self.bar.overflow.index("Rewrite"), "state"), "normal")
        self.bar.mode_menu.invoke("Email")
        self.assertEqual(self.calls, ["Email"])
        self.bar.set_rewriting(True)
        self.bar.mode_menu.invoke("Email")
        self.assertEqual(self.calls, ["Email"])
        self.bar.set_rewriting(False)
        self.bar.set_writer(False)
        self.bar.mode_menu.invoke("Email")
        self.assertEqual(self.calls, ["Email"])

    def test_reading_layout_exposes_voice_and_speed_like_the_mac_bar(self):
        self.bar.show("Selected text", 140, 140)
        self.root.update()
        self.assertEqual(self.bar.win.pack_slaves(),
                         [self.bar.btn, self.bar.ai_btn, self.bar.source_btn,
                          self.bar.speed_btn, self.bar.more_btn, self.bar.close_btn])

    def test_writing_and_playing_layouts_restore_the_selected_mode(self):
        self.bar.configure_writer(self.calls.append, lambda: BUILTIN_MODES, lambda: "Message")
        self.bar.set_mode(True)
        self.bar.show("Selected text", 140, 140)
        self.root.update()
        left = self.bar.win.winfo_x()
        self.assertEqual(self.bar.win.pack_slaves(),
                         [self.bar.rewrite_btn, self.bar.mode_btn, self.bar.divider, self.bar.btn,
                          self.bar.ai_btn, self.bar.source_btn, self.bar.speed_btn,
                          self.bar.more_btn, self.bar.close_btn])
        self.bar.rewrite_btn.command()
        self.assertEqual(self.calls, ["Message"])
        self.bar.set_playing(True)
        self.root.update()
        self.bar.back_btn.command()
        self.bar.next_btn.command()
        self.assertEqual(self.calls, ["Message", ("skip", -1), ("skip", 1)])
        self.assertEqual(self.bar.win.pack_slaves(),
                         [self.bar.btn, self.bar.back_btn, self.bar.next_btn, self.bar.stop_btn,
                          self.bar.source_btn, self.bar.speed_btn, self.bar.more_btn, self.bar.close_btn])
        self.assertEqual(self.bar.win.winfo_x(), left)
        self.bar.set_playing(False)
        self.assertIn(self.bar.rewrite_btn, self.bar.win.pack_slaves())
        self.bar.set_writer(False)
        self.assertEqual(self.bar.win.pack_slaves(), [self.bar.btn, self.bar.ai_btn, self.bar.source_btn,
                         self.bar.speed_btn, self.bar.more_btn, self.bar.close_btn])

    def test_overflow_keeps_voice_language_speed_summary_rewrite_and_settings(self):
        self.bar.configure_writer(self.calls.append, lambda: BUILTIN_MODES, lambda: "Fix errors")
        self.bar.set_mode(True)
        self.bar.sources = lambda: [("Codex", None)]
        self.bar.on_source = lambda name: self.calls.append(("source", name))
        self.bar.on_lang = lambda tag: self.calls.append(("language", tag))
        self.bar.on_speed = lambda speed: self.calls.append(("speed", speed))
        self.bar.on_summarize = lambda text, tag: self.calls.append(("summary", text))
        self.bar.show("Selected text", 140, 140)
        with patch.object(self.bar.overflow, "post"):
            self.bar.more_btn.command()
        menu = self.bar.overflow
        self.addCleanup(menu.unpost)
        labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)
                  if menu.type(i) != "separator"]
        self.assertEqual(labels, ["Voice", "Language", "Speed", "Summarize", "Rewrite", "Settings…"])
        self.bar.sources_menu.invoke(0)
        self.bar.menu.invoke(1)
        self.bar.speed_menu.invoke(0)
        menu.invoke("Summarize")
        self.bar.mode_menu.invoke("Email")
        menu.invoke("Settings…")
        self.assertEqual(self.calls, [("source", "Codex"), ("language", "he-IL"), ("speed", 0.5),
                                     ("summary", "Selected text"), "Email", "settings"])


if __name__ == "__main__":
    unittest.main()
