"""The grey strip under a line while Check as you type checks it."""
import threading
import unittest

from harness_discussion import live
from tests import test_app_live
from tests.test_app_live import threading_wait
from tests.test_app_writer import Source


class WhileItChecks(unittest.TestCase):
    setUp = test_app_live.WhatItDraws.setUp

    def test_a_grey_strip_marks_the_line_from_the_poll_that_starts_its_check_until_it_ends(self):
        release = threading.Event()

        class Held(Source):
            def rewrite(self, *args):
                release.wait(5)
                return super().rewrite(*args)

        self.app.writer_sources = [Held("Cursor", "We are here now.")]
        self.app.type("i thinks so, ok\nwe is here now", at=10)
        self.app._live_tick(now=11)
        self.app.pump()
        start = live.units("i thinks so, ok\n")
        self.assertEqual(self.app.marks_.checking, [(start * 8, 0, (start + 14) * 8, 18)])
        release.set()
        for _ in range(100):
            if self.app._live_checking is None:
                break
            threading_wait()
        self.assertTrue(self.app._live_wake.is_set())  # the poll loop redraws at once, not after a full interval
        self.app.tick(12)
        self.assertEqual(self.app.marks_.checking, [])
        self.assertIn(("we is", "We are"), [key[3:] for key, _ in self.app.marks_.shown[0]])


if __name__ == "__main__":
    unittest.main()
