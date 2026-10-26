import sys
import unittest

if sys.platform != "darwin":
    raise unittest.SkipTest("macOS only")

from harness_discussion.mac import bar_parts, toast  # noqa: E402


class RoundButtonTest(unittest.TestCase):
    def test_completed_rewrite_close_dismisses_without_changing_the_edit(self):
        import AppKit
        from test_app_writer import _App
        AppKit.NSApplication.sharedApplication()
        app = _App()
        self.addCleanup(app._close_writer)
        hud = app.toast = toast.Toast()
        self.addCleanup(hud.hide)
        hidden = []
        hud.on_hide = lambda: (hidden.append(True), app._watch_escape())
        app.rewrite_selection()
        app.drain()
        close = [button for button in hud.actions if button.accessibilityLabel() == "Dismiss"]
        self.assertEqual(len(close), 1)
        self.assertEqual(hud.row.arrangedSubviews()[-1], close[0])
        self.assertTrue(app.esc.on)
        last_edit, pasted = app._last_edit, list(app.edit.pasted)
        close[0].performClick_(None)
        self.assertFalse(hud.visible)
        self.assertFalse(app.esc.on)
        self.assertEqual(hidden, [True])
        self.assertEqual(app._last_edit, last_edit)
        self.assertEqual(app.edit.pasted, pasted)

    def test_busy_close_keeps_the_cancel_label_and_callback(self):
        import AppKit
        AppKit.NSApplication.sharedApplication()
        hud = toast.Toast()
        self.addCleanup(hud.hide)
        cancelled = []
        hud.show("Listening", 200, 200, [("×", lambda: cancelled.append(True))], busy=True)
        self.assertEqual(len(hud.actions), 1)
        self.assertEqual(hud.actions[0].accessibilityLabel(), "Cancel")
        hud.actions[0].performClick_(None)
        self.assertFalse(hud.visible)
        self.assertEqual(cancelled, [True])

    def test_filled_icon_buttons_are_circles(self):
        for name, button, size in (("toast close", toast._close_button(lambda: None), 22),
                                   ("play", bar_parts._play_button(lambda: None), 30)):
            with self.subTest(name):
                fitting = button.fittingSize()
                self.assertEqual((fitting.width, fitting.height), (size, size))
                self.assertEqual(button.layer().cornerRadius(), size / 2)

    def test_play_button_announces_its_current_action(self):
        from harness_discussion.mac.popup import Popup
        none = lambda *a, **k: None  # noqa: E731
        popup = Popup(1.0, on_play=none, on_stop=none, on_pause=none, on_resume=none, on_lang=none, on_speed=none,
                      on_settings=none, on_source=none, sources=lambda: [], on_skip=none)
        for playing, paused, label in ((True, False, "Pause"), (True, True, "Play"), (False, False, "Play")):
            with self.subTest(playing=playing, paused=paused):
                popup.set_playing(playing, paused)
                self.assertEqual(popup.btn.accessibilityLabel(), label)


if __name__ == "__main__":
    unittest.main()
