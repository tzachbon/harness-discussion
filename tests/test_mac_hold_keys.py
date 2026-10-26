"""Which keys the native tap fires on, passes through and swallows, on the fakes test_mac_hold_native builds."""
import unittest
from unittest.mock import Mock

from tests import test_mac_hold_native as hold


class NativeMacKeys(unittest.TestCase):
    setUp, run_mode, worker, make, event, chord = (hold.NativeMacHold.setUp, hold.NativeMacHold.run_mode,
                                                   hold.NativeMacHold.worker, hold.NativeMacHold.make,
                                                   hold.NativeMacHold.event, hold.NativeMacHold.chord)

    def test_escape_cancel_does_not_require_an_activity_callback(self):
        cancel = Mock()
        self.make(on_cancel=cancel)
        self.event(10, 53)
        cancel.assert_called_once_with()
        self.event(11, 53)
        cancel.assert_called_once_with()

    def test_activity_excludes_normal_chord_and_includes_unrelated_repeats_after_up(self):
        activity = Mock()
        self.make(on_activity=activity)
        self.chord()
        self.event(10, 15, (1 << 18) | 1, repeat=1)
        self.event(12, 62, (1 << 18) | 1 | 0x2000)
        self.event(12, 59, (1 << 18) | 0x2000)
        self.event(11, 15, (1 << 18) | 0x2000)
        self.event(12, 62)
        activity.assert_not_called()
        self.assertEqual(self.actions, ["down", "up"])
        self.event(10, 7)
        self.event(10, 7, repeat=1)
        self.assertEqual(activity.call_count, 2)
        self.event(12, 56, (1 << 17) | 2)
        self.event(12, 56)
        self.assertEqual(activity.call_count, 4)
        self.hk.stop()
        self.event(10, 7, repeat=1)
        self.event(12, 56, (1 << 17) | 2)
        self.assertEqual(activity.call_count, 4)

    def test_unrelated_repeat_notifies_before_tracker_early_returns(self):
        activity = Mock()
        self.make(on_activity=activity)
        self.chord()
        self.event(10, 7, (1 << 18) | 1)
        self.event(10, 7, (1 << 18) | 1, repeat=1)
        self.assertEqual(activity.call_count, 2)
        self.event(11, 15, (1 << 18) | 1)
        self.event(10, 7, (1 << 18) | 1, repeat=1)
        self.assertEqual(activity.call_count, 3)
        self.assertEqual(self.actions, ["down", "up"])

    def test_both_control_sides_and_lost_primary_up(self):
        hold = self.make()
        self.chord()
        self.event(12, 62, (1 << 18) | 1 | 0x2000)
        self.event(12, 59, (1 << 18) | 0x2000)
        self.assertEqual(self.actions, ["down"])
        self.physical = {62}
        hold.tracker.reconcile(hold._key_down)
        self.assertEqual(self.actions, ["down", "up"])

    def test_unrelated_key_cannot_activate_a_rejected_chord(self):
        self.make()
        self.event(12, 59, (1 << 18) | 1)
        self.event(12, 56, (1 << 18) | (1 << 17) | 1 | 2)
        self.event(10, 15, (1 << 18) | (1 << 17) | 1 | 2)
        self.event(12, 56, (1 << 18) | 1)
        self.event(10, 7, (1 << 18) | 1)
        self.assertEqual(self.actions, [])

    def test_poll_heals_missing_modifier_up_while_primary_remains_down(self):
        hold = self.make()
        self.chord()
        self.physical = {15}
        hold.tracker.reconcile(hold._key_down)
        self.assertEqual(self.actions, ["down", "up"])

    def test_only_the_shortcut_key_is_swallowed_through_its_own_key_up(self):
        cmd, shift = 1 << 20, 1 << 17
        self.make("<cmd>+<key_50>")
        self.assertFalse(self.event(12, 55, cmd | 8))  # modifiers always reach the app
        self.assertTrue(self.event(10, 50, cmd | 8))
        self.assertTrue(self.event(10, 50, cmd | 8, repeat=1))
        self.assertTrue(self.event(11, 50, cmd | 8))
        self.assertFalse(self.event(10, 15, cmd | 8))  # ⌘R is someone else's
        self.assertFalse(self.event(11, 15, cmd | 8))
        self.assertFalse(self.event(12, 56, cmd | shift | 8 | 2))
        self.assertFalse(self.event(10, 50, cmd | shift | 8 | 2))  # ⇧⌘` is not this shortcut
        self.assertFalse(self.event(11, 50, cmd | shift | 8 | 2))
        self.assertFalse(self.event(12, 56, cmd | 8))
        self.assertTrue(self.event(10, 50, cmd | 8))
        self.assertFalse(self.event(12, 55))  # the key-up still belongs to the swallowed press
        self.assertTrue(self.event(11, 50))
        self.assertFalse(self.event(10, 50))
        self.assertEqual(self.actions, ["down", "up"])  # a hold rearms only once ⌘ comes up, yet ⌘` stays ours

    def test_a_key_that_reached_the_app_keeps_its_repeats_and_key_up(self):
        self.make()
        ctrl, shift = 1 << 18, 1 << 17
        self.assertFalse(self.event(12, 59, ctrl | 1))
        self.assertFalse(self.event(12, 56, ctrl | shift | 1 | 2))
        self.assertFalse(self.event(10, 15, ctrl | shift | 1 | 2))  # ⇧⌃R isn't ⌃R
        self.assertFalse(self.event(12, 56, ctrl | 1))
        self.assertFalse(self.event(10, 15, ctrl | 1, repeat=1))  # now ⌃R, but a repeat of a press the app has
        self.assertFalse(self.event(11, 15, ctrl | 1))
        self.assertEqual(self.actions, [])

    def test_a_missed_key_up_never_swallows_the_next_plain_press(self):
        hold = self.make()
        ctrl = 1 << 18
        self.assertFalse(self.event(12, 59, ctrl | 1))
        self.assertTrue(self.event(10, 15, ctrl | 1))
        self.physical = set()  # R's key-up never arrives
        hold.tracker.reconcile(hold._key_down)
        self.assertFalse(self.event(12, 59, 0))
        self.assertFalse(self.event(10, 15))
        self.assertFalse(self.event(11, 15))
        self.assertFalse(self.event(12, 59, ctrl | 1))
        self.assertTrue(self.event(10, 15, ctrl | 1))
        self.assertEqual(self.actions, ["down", "up", "down"])

    def test_a_swallowed_key_spoils_a_modifier_only_tap_of_its_chord(self):
        ctrl, alt = 1 << 18, 1 << 19
        fired = Mock()
        read = self.trigger.HotKey("<ctrl>+<alt>", fired)
        self.addCleanup(read.stop)
        self.make("<ctrl>+<alt>+d")
        clock = self.trigger.AppKit.NSEvent.eventWithCGEvent_.return_value.timestamp
        monitor = lambda at, code, flags: read._event(Mock(type=lambda: 12, keyCode=lambda: code,
                                                           modifierFlags=lambda: flags, timestamp=lambda: at))
        for queued in (False, True):  # Read's monitor sees the chord before the swallow, or only after it
            with self.subTest(queued=queued):
                self.actions.clear()
                base = 10.0 * queued
                if not queued:
                    monitor(base + 1, 59, ctrl)
                    monitor(base + 2, 58, ctrl | alt)
                self.assertFalse(self.event(12, 59, ctrl | 1))
                self.assertFalse(self.event(12, 58, ctrl | alt | 1 | 0x20))
                clock.return_value = base + 3
                self.assertTrue(self.event(10, 2, ctrl | alt | 1 | 0x20))  # Read's monitor never sees this D
                self.assertTrue(self.event(11, 2, ctrl | alt | 1 | 0x20))
                if queued:
                    monitor(base + 1, 59, ctrl)
                    monitor(base + 2, 58, ctrl | alt)
                monitor(base + 4, 58, ctrl)
                monitor(base + 5, 59, 0)
                self.assertFalse(self.event(12, 58, ctrl | 1))
                self.assertFalse(self.event(12, 59, 0))
                fired.assert_not_called()
                self.assertEqual(self.actions, ["down", "up"])
        monitor(19, 59, ctrl)  # a plain tap, processed after a later hold's swallow, still fires
        monitor(20, 58, ctrl | alt)
        clock.return_value = 23
        self.event(12, 59, ctrl | 1)
        self.event(12, 58, ctrl | alt | 1 | 0x20)
        self.assertTrue(self.event(10, 2, ctrl | alt | 1 | 0x20))
        monitor(21, 59, 0)
        fired.assert_called_once_with()

    def test_modifier_only_shortcut_swallows_nothing(self):
        self.make("<ctrl>+<alt>")
        ctrl, alt = 1 << 18, 1 << 19
        for kind, code, flags in ((12, 59, ctrl | 1), (12, 58, ctrl | alt | 1 | 0x20), (10, 15, ctrl | alt | 1 | 0x20),
                                  (11, 15, ctrl | alt | 1 | 0x20), (12, 58, ctrl | 1), (12, 59, 0)):
            self.assertFalse(self.event(kind, code, flags))

    def test_modifier_only_extras_repeat_and_required_modifier_release(self):
        self.make("<ctrl>+<alt>")
        shift, ctrl, alt = 1 << 17, 1 << 18, 1 << 19
        self.event(12, 56, shift | 2)
        self.event(12, 59, shift | ctrl | 2 | 1)
        self.event(12, 58, shift | ctrl | alt | 2 | 1 | 0x20)
        self.assertEqual(self.actions, ["down"])
        self.event(10, 0, shift | ctrl | alt | 2 | 1 | 0x20, repeat=1)
        self.event(12, 58, shift | ctrl | 2 | 1)
        self.event(12, 58, shift | ctrl | alt | 2 | 1 | 0x20)
        self.assertEqual(self.actions, ["down", "up"])

    def test_extra_modifier_disallows_key_chord_without_release_activation(self):
        self.make()
        ctrl, shift = 1 << 18, 1 << 17
        self.event(12, 59, ctrl | 1)
        self.event(12, 56, ctrl | shift | 1 | 2)
        self.event(10, 15, ctrl | shift | 1 | 2)
        self.event(12, 56, ctrl | 1)
        self.event(10, 15, ctrl | 1, repeat=1)
        self.event(11, 15, ctrl | 1)
        self.assertEqual(self.actions, [])

    def test_key_only_and_rebind_during_autorepeat(self):
        self.make("<key_50>")
        self.event(10, 50)
        self.event(10, 50, repeat=1)
        self.event(11, 50)
        self.assertEqual(self.actions, ["down", "up"])
        self.hk.stop()
        self.physical = {50}
        self.make("<key_50>")
        self.event(10, 50, repeat=1)
        self.assertEqual(self.actions, ["down", "up"])
        self.event(11, 50)
        self.event(10, 50)
        self.assertEqual(self.actions, ["down", "up", "down"])

    def test_modifier_sides_without_device_flags_do_not_query_async_state_in_event(self):
        self.make()
        self.q.CGEventSourceKeyState.reset_mock()
        self.event(12, 59, 1 << 18)
        self.event(12, 62, 1 << 18)
        self.event(10, 15, 1 << 18)
        self.event(12, 59, 1 << 18)
        self.assertEqual(self.actions, ["down"])
        self.event(12, 62)
        self.assertEqual(self.actions, ["down", "up"])

    def test_read_and_rewrite_taps_keep_nsevent_monitors(self):
        for spec, code in (("<ctrl>+r", 15), ("<ctrl>+e", 14)):
            with self.subTest(spec=spec):
                fired, activity, cancel = Mock(), Mock(), Mock()
                hk = self.trigger.HotKey(spec, fired, on_activity=activity, on_cancel=cancel)
                hk._event(Mock(type=lambda: 10, keyCode=lambda: code,
                               modifierFlags=lambda: 1 << 18, isARepeat=lambda: False))
                fired.assert_called_once_with()
                hk._event(Mock(type=lambda: 10, keyCode=lambda: 7,
                               modifierFlags=lambda: 1 << 18, isARepeat=lambda: True))
                hk._event(Mock(type=lambda: 10, keyCode=lambda: 53,
                               modifierFlags=lambda: 0, isARepeat=lambda: False))
                activity.assert_not_called()
                cancel.assert_not_called()
                hk.stop()
                hk._event(Mock(type=lambda: 10, keyCode=lambda: code,
                               modifierFlags=lambda: 1 << 18, isARepeat=lambda: False))
                fired.assert_called_once_with()
        self.q.CGEventTapCreate.assert_not_called()
        self.assertEqual(self.trigger.AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_.call_count, 2)

    def test_modifier_only_tap_fires_on_release_and_is_spoiled_by_typing(self):
        fired = Mock()
        hk = self.trigger.HotKey("<ctrl>", fired)
        for kind, code, flags in ((12, 59, 1 << 18), (12, 59, 0)):
            hk._event(Mock(type=lambda: kind, keyCode=lambda: code, modifierFlags=lambda: flags, timestamp=lambda: 0))
        fired.assert_called_once_with()
        fired.reset_mock()
        for kind, code, flags in ((12, 59, 1 << 18), (10, 15, 1 << 18), (12, 59, 0)):
            hk._event(Mock(type=lambda: kind, keyCode=lambda: code, modifierFlags=lambda: flags, timestamp=lambda: 0))
        fired.assert_not_called()
        hk.stop()


if __name__ == "__main__":
    unittest.main()
