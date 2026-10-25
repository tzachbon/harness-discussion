"""The Mac tour's guide hook: the automatic guide waits while the tour is pending."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

if sys.platform != "darwin":
    raise unittest.SkipTest("macOS only")

from harness_discussion.mac import permission  # noqa: E402


def watcher(tour_pending):
    """A Watcher over a stub app whose tour is or is not pending."""
    app = SimpleNamespace(tour_pending=tour_pending, cfg={},
                          host=SimpleNamespace(after=lambda ms, fn: None,
                                               set_permission_missing=lambda missing: None))
    return permission.Watcher(app)


class GuideHookTest(unittest.TestCase):
    def test_the_guide_waits_while_the_tour_is_pending(self):
        w = watcher(lambda: True)
        with patch.object(permission, "trusted", return_value=False), \
             patch("harness_discussion.mac.guide.GuideWindow") as guide:
            w.tick()
        guide.assert_not_called()

    def test_the_guide_shows_when_no_tour_is_pending(self):
        w = watcher(lambda: False)
        with patch.object(permission, "trusted", return_value=False), \
             patch("harness_discussion.mac.guide.GuideWindow") as guide:
            w.tick()
        guide.assert_called_once()
        self.assertIs(w.guide, guide.return_value)