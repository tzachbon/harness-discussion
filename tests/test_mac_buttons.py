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


if __name__ == "__main__":
    unittest.main()
