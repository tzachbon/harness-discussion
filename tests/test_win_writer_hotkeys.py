"""Windows HotKey: the read shortcut must not regress, and hold-to-dictate callbacks (plan T7)."""
import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

from unittest import mock

from pynput.keyboard import Key, KeyCode

from harness_discussion.win.trigger import HotKey


def make(spec, hold, on_activity=None):
    """A HotKey over an inert mocked keyboard.Listener, with its captured callbacks."""
    downs, ups = [], []
    with mock.patch("harness_discussion.win.trigger.keyboard.Listener") as listener, \
            mock.patch("threading.Thread"), \
            mock.patch("harness_discussion.win.trigger.ctypes.windll.user32.GetAsyncKeyState", return_value=0):
        options = {"on_activity": on_activity} if on_activity is not None else {}
        hotkey = HotKey(spec, lambda: downs.append(1), on_release=(lambda: ups.append(1)) if hold else None, **options)
        press, release = listener.call_args.kwargs["on_press"], listener.call_args.kwargs["on_release"]
        inert = listener.return_value
    return hotkey, downs, ups, press, release, inert


class ReadShortcut(unittest.TestCase):
    """The default tap behavior: fire once every key of the shortcut is up, so a borrowed Ctrl+C survives."""

    def test_fires_once_every_key_is_up_and_nothing_while_they_are_down(self):
        hotkey, downs, ups, press, release, _ = make("<ctrl>+<alt>+r", hold=False)
        press(Key.ctrl_l)
        press(Key.alt_l)
        press(KeyCode(vk=0x52, char="ר"))  # R on a Hebrew layout: the virtual key is what counts
        self.assertEqual(downs, [])  # not while the keys are held
        for key in (KeyCode(vk=0x52), Key.alt_l, Key.ctrl_l):
            release(key)
        self.assertEqual(downs, [1])

    def test_an_extra_modifier_is_not_the_chord(self):
        hotkey, downs, ups, press, release, _ = make("<ctrl>+<alt>+r", hold=False)
        for key in (Key.ctrl_l, Key.shift, Key.alt_l, KeyCode(vk=0x52)):
            press(key)
        for key in (KeyCode(vk=0x52), Key.alt_l, Key.shift, Key.ctrl_l):
            release(key)
        self.assertEqual(downs, [])  # Ctrl+Alt+Shift+R isn't Ctrl+Alt+R

    def test_stop_stops_the_listener(self):
        hotkey, downs, ups, press, release, inert = make("<ctrl>+<alt>+r", hold=False)
        hotkey.stop()
        inert.stop.assert_called_once()


class HoldToDictate(unittest.TestCase):
    """Holds end on primary up or loss of a required modifier, then wait for a full reset."""

    def test_activity_excludes_normal_chord_and_includes_repeats_after_hold_up(self):
        activity = mock.Mock()
        hotkey, downs, ups, press, release, _ = make("<ctrl>+r", hold=True, on_activity=activity)
        for key in (Key.ctrl_l, Key.ctrl_r, KeyCode(vk=0x52), KeyCode(vk=0x52)):
            press(key)
        for key in (Key.ctrl_l, KeyCode(vk=0x52), Key.ctrl_r):
            release(key)
        activity.assert_not_called()
        self.assertEqual((downs, ups), ([1], [1]))
        press(KeyCode(vk=0x58))
        press(KeyCode(vk=0x58))
        self.assertEqual(activity.call_count, 2)
        press(Key.shift)
        self.assertEqual(activity.call_count, 3)
        release(Key.shift)
        self.assertEqual(activity.call_count, 4)
        hotkey.stop()
        press(KeyCode(vk=0x58))
        release(Key.shift)
        self.assertEqual(activity.call_count, 4)

    def test_activity_callback_is_ignored_for_tap_bindings(self):
        activity = mock.Mock()
        hotkey, downs, ups, press, release, _ = make("r", hold=False, on_activity=activity)
        press(KeyCode(vk=0x58))
        release(KeyCode(vk=0x58))
        activity.assert_not_called()
        hotkey.stop()

    def test_unrelated_repeat_notifies_before_tracker_early_returns(self):
        activity = mock.Mock()
        hotkey, downs, ups, press, release, _ = make("<ctrl>+r", hold=True, on_activity=activity)
        press(Key.ctrl_l)
        press(KeyCode(vk=0x52))
        press(KeyCode(vk=0x58))
        press(KeyCode(vk=0x58))
        self.assertEqual(activity.call_count, 2)
        release(KeyCode(vk=0x52))
        press(KeyCode(vk=0x58))
        self.assertEqual(activity.call_count, 3)
        self.assertEqual((downs, ups), ([1], [1]))
        hotkey.stop()

    def test_unrelated_key_cannot_activate_a_rejected_chord(self):
        _, downs, ups, press, release, _ = make("<ctrl>+r", hold=True)
        for key in (Key.ctrl_l, Key.shift_l, KeyCode(vk=0x52)):
            press(key)
        release(Key.shift_l)
        press(KeyCode(vk=0x58))
        self.assertEqual((downs, ups), ([], []))

    def test_waits_for_both_physical_control_keys_to_release(self):
        hotkey, downs, ups, press, release, _ = make("<ctrl>+<alt>+r", hold=True)
        for key in (Key.ctrl_l, Key.ctrl_r, Key.alt_l, KeyCode(vk=0x52)):
            press(key)
        self.assertEqual(downs, [1])
        release(Key.ctrl_l)
        self.assertEqual(ups, [])
        release(Key.alt_l)
        self.assertEqual(ups, [1])

    def test_down_fires_on_the_exact_chord_and_up_fires_on_primary_release(self):
        hotkey, downs, ups, press, release, _ = make("<ctrl>+<alt>+r", hold=True)
        press(Key.ctrl_l)
        press(Key.alt_l)
        self.assertEqual(downs, [])  # a partial chord hasn't matched yet
        press(KeyCode(vk=0x52, char="ר"))
        self.assertEqual(downs, [1])  # the down fires while the chord is still held
        self.assertEqual(ups, [])
        release(KeyCode(vk=0x52))
        self.assertEqual(ups, [1])
        release(Key.alt_l)
        release(Key.ctrl_l)
        self.assertEqual(downs, [1])
        self.assertEqual(ups, [1])

    def test_auto_repeat_and_a_partial_release_never_fire_a_second_down(self):
        hotkey, downs, ups, press, release, _ = make("<ctrl>+<alt>+r", hold=True)
        for key in (Key.ctrl_l, Key.alt_l, KeyCode(vk=0x52, char="ר")):
            press(key)
        self.assertEqual(downs, [1])
        for _ in range(3):
            press(KeyCode(vk=0x52, char="ר"))  # Windows auto-repeat re-sends the same key-down
        release(KeyCode(vk=0x52))  # partially released...
        press(KeyCode(vk=0x52, char="ר"))  # ...and pressed again while Ctrl+Alt are still held
        self.assertEqual(downs, [1])
        self.assertEqual(ups, [1])
        for key in (KeyCode(vk=0x52), Key.alt_l, Key.ctrl_l):
            release(key)
        self.assertEqual(downs, [1])
        self.assertEqual(ups, [1])

    def test_poll_recovers_lost_primary_up_without_new_keyboard_events(self):
        hotkey, downs, ups, press, release, _ = make("<ctrl>+r", hold=True)
        with mock.patch("harness_discussion.held_shortcut.time.monotonic", return_value=1):
            press(Key.ctrl_l)
            press(KeyCode(vk=0x52))
        with mock.patch("harness_discussion.held_shortcut.time.monotonic", return_value=1.06), \
                mock.patch("harness_discussion.win.trigger.ctypes.windll.user32.GetAsyncKeyState",
                           side_effect=lambda code: 0x8000 if code == 0xA2 else 0):
            hotkey._hold.reconcile(hotkey._key_down)
        self.assertEqual((downs, ups), ([1], [1]))

    def test_rebind_while_key_is_down_ignores_repeat_until_reset(self):
        old, old_downs, old_ups, old_press, old_release, _ = make("r", hold=True)
        old_press(KeyCode(vk=0x52))
        old.stop()
        with mock.patch("harness_discussion.win.trigger.keyboard.Listener"), mock.patch("threading.Thread"), \
                mock.patch("harness_discussion.win.trigger.ctypes.windll.user32.GetAsyncKeyState",
                           side_effect=lambda code: 0x8000 if code == 0x52 else 0):
            downs, ups = [], []
            new = HotKey("r", lambda: downs.append(1), lambda: ups.append(1))
        self.addCleanup(new.stop)
        old_press(KeyCode(vk=0x52))
        new._press(KeyCode(vk=0x52))
        self.assertEqual((old_downs, old_ups, downs), ([1], [1], []))
        new._release(KeyCode(vk=0x52))
        new._press(KeyCode(vk=0x52))
        self.assertEqual(downs, [1])

    def test_an_extra_modifier_is_not_the_chord_and_fires_nothing(self):
        hotkey, downs, ups, press, release, _ = make("<ctrl>+<alt>+r", hold=True)
        for key in (Key.ctrl_l, Key.shift, Key.alt_l, KeyCode(vk=0x52, char="r")):
            press(key)
        self.assertEqual(downs, [])  # Ctrl+Alt+Shift+R isn't Ctrl+Alt+R
        for key in (Key.shift, KeyCode(vk=0x52), Key.alt_l, Key.ctrl_l):
            release(key)
        self.assertEqual((downs, ups), ([], []))  # no down ever fired, so no up either

    def test_rearms_after_a_full_cycle(self):
        hotkey, downs, ups, press, release, _ = make("<ctrl>+<alt>+r", hold=True)
        for cycle in range(2):
            for key in (Key.ctrl_l, Key.alt_l, KeyCode(vk=0x52, char="r")):
                press(key)
            for key in (KeyCode(vk=0x52), Key.alt_l, Key.ctrl_l):
                release(key)
        self.assertEqual(downs, [1, 1])  # each hold fires its own down
        self.assertEqual(ups, [1, 1])  # and its own up

    def test_stop_stops_the_listener(self):
        hotkey, downs, ups, press, release, inert = make("<ctrl>+<alt>+r", hold=True)
        hotkey.stop()
        inert.stop.assert_called_once()

    def test_unrelated_key_does_not_delay_release_or_full_reset(self):
        hotkey, downs, ups, press, release, _ = make("<ctrl>+r", hold=True)
        for key in (Key.ctrl_l, KeyCode(vk=0x52), KeyCode(vk=0x58)):
            press(key)
        release(KeyCode(vk=0x52))
        self.assertEqual(ups, [1])
        release(Key.ctrl_l)
        press(Key.ctrl_l)
        press(KeyCode(vk=0x52))
        self.assertEqual(downs, [1, 1])

    def test_modifier_only_tolerates_extras_and_waits_for_full_reset(self):
        hotkey, downs, ups, press, release, _ = make("<ctrl>+<alt>", hold=True)
        for key in (Key.shift, Key.ctrl_l, Key.alt_l):
            press(key)
        self.assertEqual(downs, [1])
        release(Key.alt_l)
        press(Key.alt_l)
        self.assertEqual((downs, ups), ([1], [1]))

    def test_events_after_stop_cannot_start_or_end_another_hold(self):
        hotkey, downs, ups, press, release, _ = make("r", hold=True)
        press(KeyCode(vk=0x52))
        hotkey.stop()
        before = (list(downs), list(ups))
        release(KeyCode(vk=0x52))
        press(KeyCode(vk=0x52))
        self.assertEqual((downs, ups), before)


if __name__ == "__main__":
    unittest.main()
