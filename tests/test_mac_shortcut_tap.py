"""Which keyboard hook the Mac host binds each shortcut on."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

from harness_discussion.held_shortcut import HeldShortcut
from tests import test_mac_hold_native as hold


class ShortcutHook(unittest.TestCase):
    setUp, run_mode = hold.NativeMacHold.setUp, hold.NativeMacHold.run_mode

    def test_host_binds_keyed_press_shortcuts_on_the_event_tap_and_modifier_taps_on_monitors(self):
        boundaries = {f"harness_discussion.mac.{name}": Mock()
                      for name in ("edit", "selection", "ui", "esc", "popup", "review", "settings_ui", "toast", "voice")}
        boundaries.update({"Foundation": types.SimpleNamespace(NSObject=object),
                           "harness_discussion.mac.trigger": self.trigger})
        spec = importlib.util.spec_from_file_location(
            "harness_discussion.mac._press_test_host",
            Path(__file__).parents[1] / "src/harness_discussion/mac/host.py")
        native_host = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, boundaries):
            spec.loader.exec_module(native_host)
        host = object.__new__(native_host.MacHost)
        host.shortcuts = {}
        fired = Mock()
        with patch.object(native_host, "HotKey") as hook:
            host.set_shortcut("rewrite", "<cmd>+<key_50>", fired)  # ⌘`, which macOS also binds
            hook.assert_called_once_with("<cmd>+<key_50>", fired, None, press=True)
            hook.reset_mock()
            host.set_shortcut("rewrite", "<ctrl>+<alt>", fired)
            hook.assert_called_once_with("<ctrl>+<alt>", fired, None)


class PressRearm(unittest.TestCase):
    def test_a_press_shortcut_fires_again_when_its_key_is_tapped_with_the_modifiers_held(self):
        keys, fired = {55: "cmd", 50: 50}, []
        for press, expected in ((True, ["down", "down"]), (False, ["down"])):
            with self.subTest(press=press):
                fired.clear()
                hold = HeldShortcut({"cmd"}, 50, keys, lambda: fired.append("down"), lambda: None, press=press)
                hold.event(55, "cmd", True)
                for down in (True, False, True):
                    hold.event(50, 50, down)
                self.assertEqual(fired, expected)  # a hold still needs every key up before it fires again


if __name__ == "__main__":
    unittest.main()
