"""Accessibility permission on macOS: stale grants, rebinding shortcuts, and the reset command."""
import sys
import unittest
from unittest import mock

if sys.platform != "darwin":
    raise unittest.SkipTest("macOS only")

from harness_discussion.mac import permission, startup  # noqa: E402


class PermissionTest(unittest.TestCase):
    def test_stale_only_when_another_build_was_granted(self):
        self.assertTrue(permission.is_stale("0.1.0+abc", "0.1.1+def"))
        self.assertFalse(permission.is_stale("0.1.1+def", "0.1.1+def"))
        self.assertFalse(permission.is_stale(None, "0.1.1+def"))  # never granted
        self.assertFalse(permission.is_stale("0.1.0+abc", None))  # source run has no build key

    def test_a_grant_turns_on_every_shortcut(self):
        app = mock.Mock(cfg={"hotkey": "<cmd>+<shift>+r", "rewrite_hotkey": "<cmd>+<alt>+e",
                             "dictate_hotkey": "<cmd>+<alt>+d", "ax_granted_build": "b"})
        with mock.patch.object(permission, "current_build_id", return_value="b"):
            permission.Watcher(app)._changed(True)
        self.assertEqual([c.args for c in app.apply.call_args_list],
                         [("hotkey", "<cmd>+<shift>+r"), ("rewrite_hotkey", "<cmd>+<alt>+e"),
                          ("dictate_hotkey", "<cmd>+<alt>+d")])

    def test_a_dead_tap_shortcut_binds_again(self):
        app = mock.Mock(cfg={"hotkey": "<cmd>+<key_50>", "rewrite_hotkey": "<cmd>+<alt>+e",
                             "dictate_hotkey": "<cmd>+<alt>+d"})
        app.host.hotkey = mock.Mock(active=False)
        app.host.shortcuts = {"rewrite": mock.Mock(active=True), "dictate": mock.Mock(active=False)}
        permission.Watcher(app)._heal()
        self.assertEqual([c.args for c in app.apply.call_args_list],
                         [("hotkey", "<cmd>+<key_50>"), ("dictate_hotkey", "<cmd>+<alt>+d")])

    def test_reset_uses_fixed_arguments(self):
        with mock.patch.object(permission.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertTrue(permission.reset_grant())
        self.assertEqual(run.call_args.args[0], ["/usr/bin/tccutil", "reset", "Accessibility", startup.LABEL])


if __name__ == "__main__":
    unittest.main()
