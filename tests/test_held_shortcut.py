"""Deterministic hold lifecycle tests, without OS hooks or physical input."""
import unittest
import threading
from unittest.mock import Mock, patch

from harness_discussion.held_shortcut import HeldShortcut


class HoldTracker(unittest.TestCase):
    def make(self, mods=frozenset({"ctrl"}), primary=82):
        self.actions = []
        self.keys = {162: "ctrl", 163: "ctrl", 164: "alt", 160: "shift", 82: 82}
        self.hold = HeldShortcut(mods, primary, self.keys,
                                 lambda: self.actions.append("down"), lambda: self.actions.append("up"))
        return self.hold

    def press(self, *codes):
        for code in codes:
            self.hold.event(code, self.keys.get(code, code), True)

    def release(self, *codes):
        for code in codes:
            self.hold.event(code, self.keys.get(code, code), False)

    def test_primary_up_ignores_other_keys_and_partial_repress_and_repeat(self):
        self.make()
        self.press(162, 82, 88, 82)
        self.release(82)
        self.press(82)
        self.assertEqual(self.actions, ["down", "up"])
        self.release(162, 82)
        self.press(162, 82)
        self.assertEqual(self.actions, ["down", "up", "down"])

    def test_required_modifier_uses_both_physical_sides(self):
        self.make()
        self.press(162, 163, 82)
        self.release(162)
        self.assertEqual(self.actions, ["down"])
        self.release(163)
        self.assertEqual(self.actions, ["down", "up"])

    def test_modifier_only_allows_extras_but_key_chord_requires_exact_modifiers(self):
        self.make()
        self.press(160, 162, 82)
        self.assertEqual(self.actions, [])
        self.release(160)  # releases cannot start a chord
        self.assertEqual(self.actions, [])
        self.make(frozenset({"ctrl", "alt"}), None)
        self.press(160, 162, 164)
        self.release(160)
        self.assertEqual(self.actions, ["down"])
        self.release(164)
        self.press(164)
        self.assertEqual(self.actions, ["down", "up"])

    def test_poll_heals_missing_primary_and_modifier_ups_without_new_starts(self):
        for physical in ({162}, {82}, set()):
            with self.subTest(physical=physical):
                self.make()
                self.press(162, 82)
                self.hold.reconcile(lambda code: code in physical)
                self.assertEqual(self.actions, ["down", "up"])
                self.hold.reconcile(lambda code: True)
                self.assertEqual(self.actions, ["down", "up"])

    def test_unrelated_key_cannot_activate_a_previously_rejected_chord(self):
        self.make()
        self.press(162, 160, 82)
        self.release(160)
        self.press(88)
        self.assertEqual(self.actions, [])

    def test_poll_never_promotes_stale_or_unobserved_downs(self):
        self.make()
        self.press(162)
        self.hold.reconcile(lambda code: True)
        self.assertEqual(self.actions, [])
        self.hold.seed({162: "ctrl", 82: 82})
        self.press(82)
        self.assertEqual(self.actions, [])
        self.release(82, 162)
        self.press(162, 82)
        self.assertEqual(self.actions, ["down"])

    def test_interrupt_releases_once_and_requires_physical_reset(self):
        self.make()
        self.press(162, 82)
        self.hold.interrupt()
        self.hold.interrupt()
        self.release(162, 82)
        self.press(162, 82)
        self.assertEqual(self.actions, ["down", "up"])
        self.hold.reconcile(lambda code: False)
        self.press(162, 82)
        self.assertEqual(self.actions, ["down", "up", "down"])

    def test_stop_releases_once_and_rejects_late_events_and_poll(self):
        self.make()
        self.press(162, 82)
        self.hold.stop()
        self.hold.stop()
        self.release(162, 82)
        self.press(162, 82)
        self.hold.reconcile(lambda code: False)
        self.assertEqual(self.actions, ["down", "up"])

    def test_windows_settle_window_does_not_read_async_state_in_hook(self):
        self.make()
        self.hold.settle = .05
        with patch("harness_discussion.held_shortcut.time.monotonic", return_value=10):
            self.press(162, 82)
            query = Mock(return_value=False)
            self.hold.reconcile(query)
            query.assert_not_called()
        with patch("harness_discussion.held_shortcut.time.monotonic", return_value=10.051):
            self.hold.reconcile(query)
        self.assertEqual(self.actions, ["down", "up"])

    def test_poller_checks_at_50ms_without_another_event(self):
        self.make()
        self.press(162, 82)
        self.hold._stopped = Mock()
        self.hold._stopped.wait.side_effect = [False, True]
        with patch("harness_discussion.held_shortcut.threading.Thread") as thread:
            self.hold.start_polling(lambda code: False)
            thread.call_args.kwargs["target"]()
        self.assertEqual(self.actions, ["down", "up"])
        self.assertEqual(self.hold._stopped.wait.call_args.args, (.05,))

    def test_stop_serializes_with_inflight_callback(self):
        hold = self.make(frozenset(), 82)
        entered, resume, stopping, stopped = (threading.Event() for _ in range(4))

        def callback():
            self.actions.append("down")
            entered.set()
            resume.wait(2)

        def stop():
            stopping.set()
            hold.stop()
            stopped.set()

        hold.on_down = callback
        event_thread = threading.Thread(target=lambda: self.press(82))
        stop_thread = threading.Thread(target=stop)
        event_thread.start()
        try:
            self.assertTrue(entered.wait(1))
            stop_thread.start()
            self.assertTrue(stopping.wait(1))
            self.assertFalse(stopped.is_set())
        finally:
            resume.set()
            event_thread.join(2)
            if stop_thread.ident is not None:
                stop_thread.join(2)
        self.assertTrue(stopped.is_set())
        self.press(82)
        self.release(82)
        self.assertEqual(self.actions, ["down", "up"])


if __name__ == "__main__":
    unittest.main()
