"""Shortcut keys on macOS: what a key press records, how Settings names it, and how it fires."""
import collections
import sys
import unittest
from unittest import mock

if sys.platform != "darwin":
    raise unittest.SkipTest("macOS only")

import AppKit  # noqa: E402

from harness_discussion import hotkeys  # noqa: E402
from harness_discussion.mac import trigger, window_keys  # noqa: E402


def key_event(code, flags=0, kind=AppKit.NSEventTypeKeyDown):
    return mock.Mock(type=lambda: kind, keyCode=lambda: code, modifierFlags=lambda: flags, isARepeat=lambda: False,
                     timestamp=lambda: 0)


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

    def test_macos_switches_windows_first_only_with_stage_manager_and_its_shortcut_on(self):
        cmd, alt = frozenset({"cmd"}), frozenset({"alt"})
        backtick = {(cmd, "key_50"), (cmd | {"shift"}, "key_50")}
        option_b = {(alt, "b"), (alt | {"shift"}, "b")}
        bound = {"enabled": True, "value": {"parameters": (98, 11, AppKit.NSEventModifierFlagOption)}}
        for stage, entry, expected in ((True, None, backtick), (True, bound, option_b),
                                       (True, {**bound, "enabled": False}, set()), (False, None, set()),
                                       (True, {"enabled": True}, set())):
            prefs = {"GloballyEnabled": stage, "AppleSymbolicHotKeys": {} if entry is None else {"27": entry}}
            with self.subTest(stage=stage, entry=entry), \
                    mock.patch.object(window_keys.CF, "CFPreferencesAppSynchronize"), \
                    mock.patch.object(window_keys.CF, "CFPreferencesCopyAppValue", lambda key, _domain: prefs[key]):
                self.assertEqual(window_keys.window_switch_keys(), expected)

    def test_a_keyboard_selection_fires_once_the_modifiers_come_up(self):
        shift, cmd, flags = AppKit.NSEventModifierFlagShift, AppKit.NSEventModifierFlagCommand, AppKit.NSEventTypeFlagsChanged
        t = object.__new__(trigger.SelectionTrigger)  # skips installing the system monitors
        t.on_selection, t._keyed = mock.Mock(), False
        with mock.patch.object(trigger.threading, "Thread") as thread:
            for event in (key_event(0, shift, flags), key_event(124, shift), key_event(124, shift),
                          key_event(0, 0, flags)):
                t._key(event)  # ⇧→ twice, then ⇧ up
            self.assertEqual(thread.call_count, 1)
            for event in (key_event(0, shift, flags), key_event(0, shift), key_event(0, 0, flags),  # typing "A"
                          key_event(124, shift), key_event(0, shift), key_event(0, 0, flags)):  # ⇧→ then typing
                t._key(event)
            self.assertEqual(thread.call_count, 1)
            for event in (key_event(0, cmd), key_event(0, 0, flags)):  # ⌘A
                t._key(event)
            self.assertEqual(thread.call_count, 2)

    def test_a_keyboard_selection_shows_the_bar_at_the_text_or_else_the_pointer(self):
        t = object.__new__(trigger.SelectionTrigger)
        t.on_selection = mock.Mock()
        from harness_discussion.mac import selection
        with mock.patch.object(trigger.time, "sleep"), mock.patch("PyObjCTools.AppHelper.callAfter", lambda fn: fn()), \
                mock.patch.object(trigger, "top_left", return_value=(5, 6)):
            for bounds, point in (((30, 40), (30, 40)), (None, (5, 6))):
                with mock.patch.object(selection, "bounds", return_value=bounds):
                    t._at_selection()
                t.on_selection.assert_called_with(*point)

    def test_without_text_bounds_the_bar_goes_to_the_field_then_the_window(self):
        from harness_discussion.mac import selection
        rect = mock.Mock(origin=mock.Mock(x=30, y=40), size=mock.Mock(width=0, height=0))  # an empty rect
        attrs = {"app": {"AXFocusedUIElement": "field", "AXFocusedWindow": "window"},
                 "field": {"AXSelectedTextRange": "span", "AXPosition": "field position"},
                 "window": {"AXPosition": "window position"}}
        points = {"field position": mock.Mock(x=7, y=8), "window position": mock.Mock(x=1, y=2)}
        value = lambda v, kind, _: ((True, rect) if v == "rect" else (v in points, points.get(v)))
        with mock.patch.object(selection, "_frontmost_pid", return_value=1), \
                mock.patch.object(selection, "AXUIElementCreateApplication", return_value="app"), \
                mock.patch.object(selection, "_attr", lambda e, name: attrs.get(e, {}).get(name)), \
                mock.patch.object(selection, "AXUIElementCopyParameterizedAttributeValue",
                                  return_value=(selection.kAXErrorSuccess, "rect")), \
                mock.patch.object(selection, "AXValueGetValue", value):
            self.assertEqual(selection.bounds(), (7, 8))
            del attrs["field"]["AXPosition"]
            self.assertEqual(selection.bounds(), (1, 2))

    def hotkey(self, spec, on_release=None):
        hk = object.__new__(trigger.HotKey)  # skips installing the system monitors
        hk.keys, hk.on_hotkey, hk.on_release, hk._held = hotkeys.parse(spec), mock.Mock(), on_release, None
        hk._tap = hk._spoiled = False
        hk._swallowed_at = collections.deque()
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
