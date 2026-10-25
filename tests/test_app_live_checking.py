"""The grey strip under a line while Check as you type checks it."""
import threading
import time
import unittest

from harness_discussion import live
from tests import test_app_live
from tests.test_app_live import threading_wait
from tests.test_app_writer import Source


class Held(Source):
    """A source whose check waits until `release` is set."""

    def __init__(self, name, reply, release):
        super().__init__(name, reply)
        self.release = release

    def rewrite(self, *args):
        self.release.wait(5)
        return super().rewrite(*args)


class WhileItChecks(unittest.TestCase):
    setUp = test_app_live.WhatItDraws.setUp

    def test_a_grey_strip_marks_the_line_from_the_poll_that_starts_its_check_until_it_ends(self):
        release = threading.Event()
        self.app.writer_sources = [Held("Cursor", "We are here now.", release)]
        self.app.type("i thinks so, ok\nwe is here now", at=10)
        self.app._live_tick(now=11)
        self.app.pump()
        start = live.units("i thinks so, ok\n")
        self.assertEqual(self.app.marks_.checking, [(start * 8, 0, (start + 14) * 8, 18)])
        self.app._live_wake.clear()
        release.set()
        for _ in range(100):
            if self.app._live_checking is None:
                break
            threading_wait()
        self.assertTrue(self.app._live_wake.is_set())  # the poll loop redraws at once, not after a full interval
        self.app.tick(12)
        self.assertEqual(self.app.marks_.checking, [])
        self.assertIn(("we is", "We are"), [key[3:] for key, _ in self.app.marks_.shown[0]])

    def test_only_the_line_being_checked_gets_the_strip_not_an_identical_one(self):
        self.app.writer_sources = [Held("Cursor", "We are here.", threading.Event())]
        self.app.type("we is here\nok\nwe is here", at=10)
        self.app._live_tick(now=11)
        self.app.pump()
        start = live.units("we is here\nok\n")
        self.assertEqual(self.app.marks_.checking, [(start * 8, 0, (start + 10) * 8, 18)])
        self.app.writer_sources[0].release.set()

    def test_the_poll_wakes_when_a_typed_line_is_due_not_a_full_interval_later(self):
        waits = []

        class Wake:
            def wait(_, timeout):
                waits.append(timeout)
                self.app._live_stop.set()

            def clear(_):
                pass

        self.app._live_stop.clear()
        self.app._live_wake, self.app._live_tick = Wake(), lambda: None
        self.app._live_typed, self.app._live_changed_at = ("app",), time.monotonic() - 0.4
        self.app._live_loop()
        self.assertLess(waits[0], live.PAUSE_S - 0.35)
        self.assertGreater(waits[0], 0)


if __name__ == "__main__":
    unittest.main()
