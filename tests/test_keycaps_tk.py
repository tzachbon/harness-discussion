"""Windows keycaps: one chip per key, plain text for states that aren't a shortcut."""
import sys
import tkinter as tk
import unittest
from unittest.mock import patch

if sys.platform == "darwin":  # Tk would start its own NSApplication beside the AppKit tests
    raise unittest.SkipTest("Windows widget")

from harness_discussion import hotkeys
from harness_discussion.win.keycaps import Keys

class KeycapsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError:
            raise unittest.SkipTest("no display for Tk")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.keys = Keys(tk.Frame(self.root, bg="#ffffff"), "TkDefaultFont", "#1b1b1b", "#ebebeb", "#5f5f5f")
        self.addCleanup(self.keys.master.destroy)
        platform = patch.object(hotkeys.sys, "platform", "win32")
        platform.start()
        self.addCleanup(platform.stop)

    def caps(self):
        return [c.itemcget(c.find_all()[-1], "text") for c in self.keys.winfo_children() if isinstance(c, tk.Canvas)]

    def plain(self):
        return [c["text"] for c in self.keys.winfo_children() if isinstance(c, tk.Label)]

    def test_one_cap_per_key(self):
        self.keys.show("<ctrl>+<alt>+r")
        self.assertEqual((self.caps(), self.plain(), self.keys.text), (["Ctrl", "Alt", "R"], [], "Ctrl+Alt+R"))

    def test_held_modifiers_show_caps_then_the_ellipsis(self):
        self.keys.show("<ctrl>+<shift>", "…")
        self.assertEqual((self.caps(), self.plain(), self.keys.text), (["Ctrl", "Shift"], ["…"], "Ctrl+Shift…"))

    def test_off_and_messages_are_plain(self):
        self.keys.show("<ctrl>+<alt>+r")
        self.keys.show("")
        self.assertEqual((self.caps(), self.plain(), self.keys.text), ([], ["Off"], "Off"))
        self.keys.say("Press keys…")
        self.assertEqual((self.caps(), self.plain(), self.keys.text), ([], ["Press keys…"], "Press keys…"))

    def test_a_cap_is_at_least_square(self):
        self.keys.show("r")
        cap = self.keys.winfo_children()[0]
        self.assertGreaterEqual(int(cap["width"]), int(cap["height"]))

if __name__ == "__main__":
    unittest.main()
