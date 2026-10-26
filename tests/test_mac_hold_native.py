"""Threaded fake CF owner loop and Quartz boundaries, without native input."""
import importlib.util
import queue
import threading
from contextlib import nullcontext
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class NativeMacHold(unittest.TestCase):
    def setUp(self):
        appkit = types.ModuleType("AppKit")
        for name, value in {"NSEventMaskLeftMouseDown": 1, "NSEventMaskLeftMouseUp": 2,
                            "NSEventMaskRightMouseDown": 1 << 3, "NSEventMaskOtherMouseDown": 1 << 25,
                            "NSEventTypeLeftMouseDown": 1, "NSEventTypeLeftMouseUp": 2,
                            "NSEventTypeRightMouseDown": 3, "NSEventTypeOtherMouseDown": 25,
                            "NSEventMaskKeyDown": 1 << 10, "NSEventMaskKeyUp": 1 << 11,
                            "NSEventMaskFlagsChanged": 1 << 12, "NSEventTypeKeyDown": 10,
                            "NSEventTypeKeyUp": 11, "NSEventTypeFlagsChanged": 12,
                            "NSEventModifierFlagControl": 1 << 18, "NSEventModifierFlagOption": 1 << 19,
                            "NSEventModifierFlagShift": 1 << 17, "NSEventModifierFlagCommand": 1 << 20}.items():
            setattr(appkit, name, value)
        appkit.NSEvent = Mock()
        self.q = Mock(kCGEventSourceStateCombinedSessionState=0,
                      kCGEventKeyDown=10, kCGEventKeyUp=11, kCGEventFlagsChanged=12,
                      kCGEventLeftMouseDown=1, kCGEventRightMouseDown=3, kCGEventOtherMouseDown=25,
                      kCGEventTapDisabledByTimeout=-2, kCGEventTapDisabledByUserInput=-1,
                      kCGSessionEventTap=1, kCGHeadInsertEventTap=0, kCGEventTapOptionDefault=0,
                      kCGKeyboardEventKeycode=9, kCGKeyboardEventAutorepeat=8)
        self.q.CGEventGetIntegerValueField.side_effect = lambda event, field: event.code if field == 9 else event.repeat
        self.q.CGEventGetFlags.side_effect = lambda event: event.flags
        self.cf = Mock(kCFRunLoopDefaultMode="default", kCFRunLoopRunTimedOut=3,
                       kCFRunLoopRunHandledSource=4)
        self.cf.CFMachPortIsValid.return_value = True
        self.q.CGEventTapIsEnabled.return_value = True
        self.jobs = queue.Queue()
        self.ready = threading.Event()
        self.removed = threading.Event()
        self.cf.CFRunLoopAddSource.side_effect = lambda *args: self.ready.set()
        self.cf.CFRunLoopRemoveSource.side_effect = lambda *args: self.removed.set()
        self.cf.CFRunLoopRunInMode.side_effect = self.run_mode
        self.cf.CFRunLoopWakeUp.side_effect = lambda *args: self.jobs.put((lambda: None, threading.Event(), []))
        self.permission = types.ModuleType("harness_discussion.mac.permission")
        self.permission.trusted = Mock(return_value=True)
        self.physical = set()
        self.q.CGEventSourceKeyState.side_effect = lambda source, code: code in self.physical
        self.modules = patch.dict(sys.modules, {"AppKit": appkit, "Quartz": self.q,
                                               "harness_discussion.mac.permission": self.permission,
                                               "CoreFoundation": self.cf,
                                               "objc": types.SimpleNamespace(autorelease_pool=nullcontext)})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location(
            "harness_discussion.mac._hold_test_trigger",
            Path(__file__).parents[1] / "src/harness_discussion/mac/trigger.py")
        self.trigger = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.trigger)
        self.actions = []

    def run_mode(self, mode, interval, once):
        self.assertEqual((mode, interval, once), ("default", .05, False))
        try:
            callback, done, errors = self.jobs.get(timeout=2)
        except queue.Empty:
            return 3
        try:
            callback()
        except BaseException as error:
            errors.append(error)
        finally:
            done.set()
        return 3

    def worker(self, callback):
        done, errors = threading.Event(), []
        self.jobs.put((callback, done, errors))
        self.assertTrue(done.wait(2), "native owner did not process command")
        if errors:
            raise errors[0]

    def tick(self):
        self.worker(lambda: None)
        self.worker(lambda: None)  # previous iteration's health check has completed

    def make(self, spec="<ctrl>+r", on_activity=None, on_cancel=None):
        options = {"on_activity": on_activity} if on_activity is not None else {}
        if on_cancel is not None:
            options["on_cancel"] = on_cancel
        self.hk = self.trigger.HotKey(spec, lambda: self.actions.append("down"), lambda: self.actions.append("up"), **options)
        hold = self.hk._hold
        def cleanup():
            hold.stop()
            if hasattr(hold, "_thread"):
                hold._thread.join(2)
                self.assertFalse(hold._thread.is_alive())
        self.addCleanup(cleanup)
        if hasattr(hold, "_thread"):
            self.assertTrue(self.ready.wait(2))
        return hold

    def event(self, kind, code=0, flags=0, repeat=0):
        event = types.SimpleNamespace(code=code, flags=flags, repeat=repeat)
        if kind in (10, 12):
            name, side = self.trigger.MOD_CODES.get(code, (None, 0))
            sides = sum(mask for mod, mask in self.trigger.MOD_CODES.values() if mod == name)
            down = (bool(flags & side) if flags & sides else
                    bool(flags & self.trigger.FLAGS.get(name, 0)) and code not in self.physical)
            if kind == 10 or down:
                self.physical.add(code)
            else:
                self.physical.discard(code)
        elif kind == 11:
            self.physical.discard(code)
        result = []
        def deliver():
            result.append(self.hk._hold._event(None, kind, event, None))
            self.assertIn(result[0], (event, None))  # passed through unchanged, or swallowed
        if self.hk._hold._closed:
            deliver()
        else:
            self.worker(deliver)
        return result[0] is None

    def chord(self):
        self.event(12, 59, (1 << 18) | 1)
        self.event(10, 15, (1 << 18) | 1)

    def test_escape_cancels_on_owner_before_activity_during_and_after_hold_and_ignores_stop(self):
        callbacks = []
        hold = self.make(
            on_activity=lambda: callbacks.append(("activity", threading.current_thread())),
            on_cancel=lambda: callbacks.append(("cancel", threading.current_thread())))
        self.chord()
        self.event(10, 53, (1 << 18) | 1)
        self.assertEqual(callbacks, [("cancel", hold._thread), ("activity", hold._thread)])
        self.event(11, 53, (1 << 18) | 1)
        self.event(11, 15, (1 << 18) | 1)
        self.event(12, 59)
        self.assertEqual(self.actions, ["down", "up"])
        self.event(10, 53, repeat=1)
        self.assertEqual(callbacks, [("cancel", hold._thread), ("activity", hold._thread)] * 2)
        self.event(11, 53)
        self.event(10, 7)
        self.assertEqual(callbacks[-1], ("activity", hold._thread))
        self.hk.stop()
        before = list(callbacks)
        self.event(10, 53)
        self.event(11, 53)
        self.assertEqual(callbacks, before)

    def test_host_forwards_optional_observer_callbacks_and_stops_replaced_binding(self):
        boundaries = {f"harness_discussion.mac.{name}": Mock()
                      for name in ("edit", "selection", "ui", "esc", "popup", "review", "settings_ui", "toast", "voice")}
        boundaries.update({"Foundation": types.SimpleNamespace(NSObject=object),
                           "harness_discussion.mac.trigger": self.trigger})
        spec = importlib.util.spec_from_file_location(
            "harness_discussion.mac._hold_test_host",
            Path(__file__).parents[1] / "src/harness_discussion/mac/host.py")
        native_host = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, boundaries):
            spec.loader.exec_module(native_host)
        host = object.__new__(native_host.MacHost)
        host.shortcuts = {}
        down, up, activity, cancel = Mock(), Mock(), Mock(), Mock()
        for options in ({}, {"on_activity": activity}, {"on_cancel": cancel},
                        {"on_activity": activity, "on_cancel": cancel}):
            with self.subTest(options=tuple(options)), patch.object(native_host, "HotKey") as hook:
                old = host.shortcuts.get("dictate")
                host.set_shortcut("dictate", "<ctrl>+r", down, up, **options)
                hook.assert_called_once_with("<ctrl>+r", down, up, **options)
                if old is not None:
                    old.stop.assert_called_once_with()
                for callback in hook.call_args.kwargs.values():
                    callback()
        self.assertEqual((activity.call_count, cancel.call_count), (2, 2))
        last = host.shortcuts["dictate"]
        host.set_shortcut("dictate", "", None)
        last.stop.assert_called_once_with()
        self.assertEqual(host.shortcuts, {})

    def test_other_mouse_down_notifies_without_advancing_left_selection_heuristics(self):
        native = self.trigger.AppKit.NSEvent
        native.doubleClickInterval.return_value = .5
        selected, pressed = Mock(), Mock()
        with patch.object(self.trigger, "top_left", return_value=(10, 20)):
            watcher = self.trigger.SelectionTrigger(selected, pressed)
            watcher.start()
            mask = native.addGlobalMonitorForEventsMatchingMask_handler_.call_args_list[0].args[0]
            self.assertEqual(mask, 1 | 2 | (1 << 3) | (1 << 25))
            watcher._clicks = Mock()
            for kind in (1, 3, 25):
                watcher._event(Mock(type=lambda: kind))
            for kind in (4, 26):  # non-left ups do not advance selection either
                watcher._event(Mock(type=lambda: kind))
            self.assertEqual(pressed.call_count, 3)
            watcher._clicks.down.assert_called_once_with(10, 20)
            watcher._clicks.up.assert_not_called()
            selected.assert_not_called()
            watcher._event(Mock(type=lambda: 2, window=lambda: None))
            selected.assert_called_once_with(10, 20)
            watcher.stop()

    def test_combined_tap_and_primary_release(self):
        hold = self.make()
        self.assertTrue(self.hk.active)
        args = self.q.CGEventTapCreate.call_args.args
        self.assertEqual(args[:4], (1, 0, 0, sum(1 << n for n in (10, 11, 12, 1, 3, 25))))
        self.assertEqual(args[4], hold._callback)
        self.trigger.AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_.assert_not_called()
        self.chord()
        self.event(1)
        self.assertEqual(self.actions, ["down"])
        self.event(11, 15, (1 << 18) | 1)
        self.assertEqual(self.actions, ["down", "up"])

    def test_lifetime_health_after_full_release_blocks_paste(self):
        for failure in ("permission", "disabled", "poll"):
            with self.subTest(failure=failure):
                retained = threading.Event()
                hold = self.make(on_activity=retained.set)
                self.chord()
                self.event(11, 15, (1 << 18) | 1)
                self.event(12, 59)
                self.assertFalse(retained.is_set())
                if failure == "permission":
                    self.permission.trusted.return_value = False
                elif failure == "disabled":
                    self.q.CGEventTapIsEnabled.return_value = False
                else:
                    self.q.CGEventSourceKeyState.side_effect = OSError("physical poll failed")
                self.tick()  # main surrogate only waits, no UI dispatch occurs
                self.assertTrue(retained.is_set())
                paste = Mock()
                if not retained.is_set():
                    paste()
                paste.assert_not_called()
                self.permission.trusted.return_value = True
                self.q.CGEventTapIsEnabled.return_value = True
                self.q.CGEventSourceKeyState.side_effect = lambda source, code: code in self.physical
                self.tick()
                self.assertTrue(retained.is_set())
                hold.stop()
                hold._thread.join(2)

    def test_native_edges_and_all_mouse_buttons_while_main_waits(self):
        threads, order = [], []
        def activity():
            threads.append(threading.get_ident())
            order.append("activity")
        hold = self.make(on_activity=activity)
        hold.tracker.on_down = lambda: (threads.append(threading.get_ident()), order.append("down"))
        hold.tracker.on_up = lambda: (threads.append(threading.get_ident()), order.append("up"))
        self.chord()
        for kind in (1, 3, 25):
            self.event(kind)
        self.event(11, 15, (1 << 18) | 1)
        self.assertEqual(order, ["down", "activity", "activity", "activity", "up"])
        self.assertEqual(set(threads), {hold._thread.ident})
        self.assertNotEqual(hold._thread.ident, threading.get_ident())
        self.trigger.AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_.assert_not_called()

    def test_disabled_notification_recovery_requires_full_reset(self):
        order = []
        hold = self.make(on_activity=lambda: order.append("activity"))
        hold.tracker.on_up = lambda: (order.append("up"), self.actions.append("up"))
        self.chord()
        self.q.CGEventTapIsEnabled.return_value = False
        self.event(-2)
        self.assertEqual(order[:2], ["activity", "up"])
        self.q.CGEventTapEnable.assert_called_with(hold._tap, True)
        self.q.CGEventTapIsEnabled.return_value = True
        self.event(10, 15, (1 << 18) | 1, repeat=1)
        self.tick()
        self.assertEqual(self.actions, ["down", "up"])
        self.event(11, 15, (1 << 18) | 1)
        self.event(12, 59)
        self.tick()
        self.chord()
        self.assertEqual(self.actions, ["down", "up", "down"])

    def test_stop_invalidates_and_late_callback_is_unchanged(self):
        hold = self.make()
        self.chord()
        tap, callback = hold._tap, hold._callback
        stop = Mock(wraps=hold.tracker.stop)
        hold.tracker.stop = stop
        self.hk.stop()
        self.hk.stop()
        hold._thread.join(2)
        event = object()
        self.assertIs(callback(None, 10, event, None), event)
        self.assertFalse(self.hk.active)
        self.assertEqual(self.actions, ["down", "up"])
        stop.assert_called_once_with()
        self.cf.CFMachPortInvalidate.assert_called_once_with(tap)
        self.assertTrue(self.removed.is_set())

    def test_denied_and_partial_setup_fail_closed(self):
        for failure in ("permission", "create", "source", "invalid", "exception"):
            with self.subTest(failure=failure):
                self.permission.trusted.return_value = failure != "permission"
                self.q.CGEventTapCreate.return_value = None if failure == "create" else object()
                self.q.CGEventTapCreate.side_effect = OSError("denied") if failure == "exception" else None
                self.cf.CFMachPortCreateRunLoopSource.return_value = None if failure == "source" else object()
                self.cf.CFMachPortIsValid.return_value = failure != "invalid"
                activity = Mock()
                hold = self.make(on_activity=activity)
                self.assertFalse(self.hk.active)
                activity.assert_called()
                self.assertTrue(hold._closed)

    def test_unexpected_loop_return_and_add_failure_close_owner(self):
        for failure in ("return", "add", "exception", "invalid"):
            with self.subTest(failure=failure):
                activity = threading.Event()
                self.ready.clear()
                self.cf.CFMachPortIsValid.return_value = True
                self.cf.CFRunLoopAddSource.side_effect = lambda *args: self.ready.set()
                self.cf.CFRunLoopRunInMode.side_effect = self.run_mode
                hold = self.make(on_activity=activity.set)
                if failure == "return":
                    self.cf.CFRunLoopRunInMode.side_effect = lambda *args: 1
                elif failure == "exception":
                    self.cf.CFRunLoopRunInMode.side_effect = OSError("loop failure")
                elif failure == "invalid":
                    self.cf.CFMachPortIsValid.return_value = False
                else:
                    # Test partial source installation on a second binding.
                    hold.stop()
                    hold._thread.join(2)
                    self.cf.CFRunLoopAddSource.side_effect = OSError("add failure")
                    with patch.object(self.ready, "wait", return_value=True):
                        hold = self.make(on_activity=activity.set)
                self.jobs.put((lambda: None, threading.Event(), []))
                self.assertTrue(activity.wait(2))
                hold._thread.join(2)
                self.assertFalse(hold._thread.is_alive())
                self.assertFalse(self.hk.active)

    def test_stop_before_owner_ready(self):
        with patch.object(self.trigger.threading, "Thread") as thread:
            thread.return_value.is_alive.return_value = False
            with patch.object(self.ready, "wait", return_value=True):
                hold = self.make()
            tap = hold._tap
            hold.stop()
            hold._run()
        self.cf.CFMachPortInvalidate.assert_called_once_with(tap)
        self.cf.CFRunLoopAddSource.assert_not_called()
        self.assertIsNone(hold._callback)

    def test_physical_poll_exception_notifies_before_interrupt(self):
        order = []
        hold = self.make(on_activity=lambda: order.append("activity"))
        hold.tracker.on_up = lambda: order.append("up")
        self.chord()
        self.q.CGEventSourceKeyState.side_effect = OSError("poll failed")
        self.tick()
        self.assertEqual(order[:2], ["activity", "up"])
        self.assertTrue(hold.tracker._blocked)

    def test_callback_exception_returns_original_and_cleanup_survives_disable_error(self):
        hold = self.make(on_activity=Mock(side_effect=RuntimeError("activity callback")))
        interrupt = Mock(wraps=hold.tracker.interrupt)
        hold.tracker.interrupt = interrupt
        self.event(3)
        interrupt.assert_called_once_with()
        tap = hold._tap
        self.q.CGEventTapEnable.side_effect = OSError("disable failed")
        hold.stop()
        hold._thread.join(2)
        self.cf.CFMachPortInvalidate.assert_called_once_with(tap)
        self.assertTrue(self.removed.is_set())

    def test_source_and_thread_start_exceptions_invalidate_partial_tap(self):
        for failure in ("source", "thread"):
            with self.subTest(failure=failure):
                self.cf.CFMachPortInvalidate.reset_mock()
                self.cf.CFMachPortCreateRunLoopSource.side_effect = OSError("source failure") if failure == "source" else None
                with patch.object(self.trigger.threading, "Thread") as thread:
                    thread.return_value.start.side_effect = RuntimeError("thread start failed")
                    thread.return_value.is_alive.return_value = False
                    with patch.object(self.ready, "wait", return_value=True):
                        hold = self.make()
                self.assertTrue(hold._closed)
                self.cf.CFMachPortInvalidate.assert_called_once_with(hold._tap)

if __name__ == "__main__":
    unittest.main()
