"""Which keyboard hook the Mac host binds each shortcut on."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

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
            hook.assert_called_once_with("<cmd>+<key_50>", fired, native_host._ignore)
            hook.reset_mock()
            host.set_shortcut("rewrite", "<ctrl>+<alt>", fired)
            hook.assert_called_once_with("<ctrl>+<alt>", fired, None)


if __name__ == "__main__":
    unittest.main()
