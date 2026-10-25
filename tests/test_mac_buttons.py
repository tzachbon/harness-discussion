import sys
import unittest

if sys.platform != "darwin":
    raise unittest.SkipTest("macOS only")

from harness_discussion.mac import bar_parts, toast  # noqa: E402


class RoundButtonTest(unittest.TestCase):
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
