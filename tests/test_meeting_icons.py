"""Meeting images and per-show toast timing on the available platform."""
import sys
import unittest
from unittest.mock import patch

from harness_discussion import icons


class MeetingIcons(unittest.TestCase):
    def test_recording_image(self):
        for size in (16, 32, 64):
            image = icons.recording_image(size)
            self.assertEqual(image.size, (size, size))
            self.assertIn((232, 17, 35, 255), image.get_flattened_data())

    def test_meetings_page_image(self):
        image = icons.page_image("Meetings")
        self.assertEqual(image.size, (20, 20))
        self.assertIn((22, 163, 74, 255), image.get_flattened_data())
        self.assertIn((255, 255, 255, 255), image.get_flattened_data())


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class WindowsToastTimeout(unittest.TestCase):
    def test_timeout_is_per_show_and_rescheduled_after_hover(self):
        import tkinter as tk
        from harness_discussion.win.toast import Toast

        root = tk.Tk()
        root.withdraw()
        toast = Toast(root)
        try:
            with patch.object(toast.win, "after", wraps=toast.win.after) as after:
                toast.show("Meeting", 100, 100, hide_s=12)
                self.assertEqual(after.call_args.args[0], 12000)
                toast._enter(None)
                toast._leave(None)
                self.assertEqual(after.call_args.args[0], 12000)
                toast.show("Default", 100, 100)
                self.assertEqual(after.call_args.args[0], 6000)
                toast.show("Busy", 100, 100, busy=True, hide_s=12)
                self.assertIsNone(toast._hide_job)
        finally:
            toast.destroy()
            root.destroy()


if __name__ == "__main__":
    unittest.main()
