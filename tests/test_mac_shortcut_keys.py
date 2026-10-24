"""Shortcut keys on macOS: what a key press records, how Settings names it, and how it fires."""
import sys
import unittest
from unittest import mock

if sys.platform != "darwin":
    raise unittest.SkipTest("macOS only")

import AppKit  # noqa: E402

from harness_discussion import hotkeys  # noqa: E402
from harness_discussion.mac import trigger  # noqa: E402


def key_event(code, flags=0, kind=AppKit.NSEventTypeKeyDown):
    return mock.Mock(type=lambda: kind, keyCode=lambda: code, modifierFlags=lambda: flags, isARepeat=lambda: False)


class ShortcutKeyTest(unittest.TestCase):
    def test_any_key_makes_a_shortcut_with_or_without_modifiers(self):
        cmd, shift = AppKit.NSEventModifierFlagCommand, AppKit.NSEventModifierFlagShift
        self.assertEqual(trigger.event_spec(key_event(50, cmd)), "<cmd>+<key_50>")  # ⌘`
        self.assertEqual(trigger.event_spec(key_event(50, cmd | shift)), "<shift>+<cmd>+<key_50>")
        self.assertEqual(trigger.event_spec(key_event(0)), "a")
        self.assertEqual(trigger.event_spec(key_event(8, cmd)), "<cmd>+c")
        self.assertEqual(trigger.event_spec(key_event(10)), "<key_10>")  # the ISO key above Tab
        self.assertEqual(trigger.event_spec(key_event(122)), "<f1>")

    def test_keys_that_type_nothing_have_fixed_names(self):
        self.assertEqual([trigger.key_name(c) for c in (49, 123, 105)], ["Space", "←", "F13"])

    def test_other_keys_show_what_the_layout_types(self):
        for typed, code, name in (("ü", 33, "Ü"), ("", 93, "Key 93"), ("\x10", 94, "Key 94"), ("1", 83, "Keypad 1")):
            with mock.patch.object(trigger, "_typed", return_value=typed):
                self.assertEqual(trigger.key_name(code), name)
        self.assertTrue(trigger.key_name(50))  # the real layout lookup answers
        self.assertEqual(hotkeys.label("<cmd>+<key_50>"), "⌘" + trigger.key_name(50))

    def hotkey(self, spec, on_release=None):
        hk = object.__new__(trigger.HotKey)  # skips installing the system monitors
        hk.keys, hk.on_hotkey, hk.on_release, hk._held = hotkeys.parse(spec), mock.Mock(), on_release, None
        hk._tap = hk._spoiled = False
        return hk

    def test_modifiers_alone_fire_on_release_unless_a_key_joined(self):
        ctrl, alt, shift = (AppKit.NSEventModifierFlagControl, AppKit.NSEventModifierFlagOption,
                            AppKit.NSEventModifierFlagShift)
        flags = AppKit.NSEventTypeFlagsChanged
        hk = self.hotkey("<ctrl>+<alt>")
        for f in (ctrl, ctrl | alt, alt, 0):
            hk._event(key_event(0, f, flags))
        hk.on_hotkey.assert_called_once()
        hk.on_hotkey.reset_mock()
        hk._event(key_event(0, ctrl | alt, flags))
        hk._event(key_event(15, ctrl | alt))  # ⌃⌥R
        hk._event(key_event(0, 0, flags))
        for f in (ctrl | alt, ctrl | alt | shift, ctrl | alt, ctrl, 0):  # Shift joined, so no tap
            hk._event(key_event(0, f, flags))
        hk.on_hotkey.assert_not_called()
        one = self.hotkey("<ctrl>")
        one._event(key_event(0))  # typing "a" first
        for f in (ctrl, 0):
            one._event(key_event(0, f, flags))
        one.on_hotkey.assert_called_once()

    def test_a_key_alone_fires(self):
        hk = self.hotkey("a")
        hk._event(key_event(0))
        hk.on_hotkey.assert_called_once()


if __name__ == "__main__":
    unittest.main()
