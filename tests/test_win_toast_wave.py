import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

import tkinter as tk

from harness_discussion.win.toast import WAVE_BARS, Toast


class ToastWaveform(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.toast = Toast(self.root)

    def tearDown(self):
        self.toast.destroy()
        self.root.destroy()

    def heights(self):
        return [round(y2 - y1, 2) for _, y1, _, y2 in map(self.toast.meter.coords, self.toast._wave)]

    def test_bars_follow_measured_levels_and_stop_after_recording(self):
        toast = self.toast
        toast.show("Listening", 100, 100, busy=True)
        toast.set_input_level(0.0)
        self.assertTrue(toast.meter.winfo_manager())
        self.assertFalse(toast.spinner.winfo_manager())
        calm = self.heights()
        self.assertEqual(len(set(calm)), 1)  # silence: one flat row

        toast.set_input_level(0.15)  # retain the recorder's sensitivity for quiet input
        self.assertGreater(self.heights()[-1], calm[-1])

        for _ in range(4):
            toast.set_input_level(0.9)
        loud = self.heights()
        self.assertGreater(loud[-1], calm[-1])
        self.assertEqual(loud[0], calm[0])  # older history still silent
        self.assertLessEqual(loud[-1] + toast._wave_bar, toast._wave_height + 0.01)  # round caps stay in the canvas

        toast.set_input_level(0.0)
        released = self.heights()
        self.assertLess(released[-1], loud[-1])
        self.assertGreater(released[-1], calm[-1])  # release eases out, no snap

        toast.show("Transcribing", 100, 100, busy=True)
        self.assertFalse(toast.meter.winfo_manager())
        self.assertTrue(toast.spinner.winfo_manager())
        self.assertEqual(toast._levels, [0.0] * WAVE_BARS)

        toast.hide()
        self.assertIsNone(toast._activity_job)
        self.assertEqual(self.root.tk.call("after", "info"), "")  # nothing scheduled after recording


if __name__ == "__main__":
    unittest.main()
