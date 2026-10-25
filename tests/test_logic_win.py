"""Windows-only logic: clipboard formats, the mouse hook, SAPI rate, and the sign-in task."""
import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button

from harness_discussion import settings
from unittest import mock

from harness_discussion.win import clipboard, selection
from harness_discussion.win import input as keys
from harness_discussion.win.shortcut_row import KEYS
from harness_discussion.win.trigger import HotKey, SelectionTrigger, key_name, vk
from harness_discussion.win.voice import sapi_rate


class Clipboard(unittest.TestCase):
    def test_safe_to_borrow(self):
        self.assertTrue(clipboard.safe_to_borrow(set()))
        self.assertTrue(clipboard.safe_to_borrow({13, 1, 7, 16, 49353}))  # text + HTML Format
        self.assertTrue(clipboard.safe_to_borrow({2, 8, 17}))  # bitmap backed by a DIB
        self.assertFalse(clipboard.safe_to_borrow({2}))  # bare HBITMAP
        self.assertFalse(clipboard.safe_to_borrow({14, 13}))  # enhanced metafile


class ListNumbers(unittest.TestCase):
    def read(self, uia, fallback=True):
        with mock.patch.object(selection, "_via_uia", return_value=uia), \
                mock.patch.object(keys, "chord") as self.copy, \
                mock.patch.object(clipboard, "snapshot_with_sequence") as self.snapshot:
            return selection.read(fallback)

    def test_list_text_is_preserved_without_borrowing_for_html_enrichment(self):
        self.assertEqual(self.read("Please:\nA\nB"), "Please:\nA\nB")
        self.copy.assert_not_called()
        self.snapshot.assert_not_called()

    def test_both_saved_settings_preserve_existing_numbers_and_never_copy(self):
        for enabled in (False, True):
            for text in ("Please:\n1. A", "Please:\nA\nB", ""):
                self.assertEqual(self.read(text, fallback=enabled), text)
                self.copy.assert_not_called()
                self.snapshot.assert_not_called()


class Trigger(unittest.TestCase):
    def test_drag_and_double_click(self):
        hits = []
        t = SelectionTrigger(lambda x, y: hits.append((x, y)), lambda x, y: None)
        click = lambda x, y: (t._click(x, y, Button.left, True), t._click(x, y, Button.left, False))
        t._click(0, 0, Button.left, True)
        t._click(100, 0, Button.left, False)  # drag
        click(500, 500)  # single click: nothing
        click(500, 500)  # second click: double-click
        t._click(0, 0, Button.right, True)  # other buttons do not select text
        self.assertEqual(hits, [(100, 0), (500, 500)])

    def test_every_mouse_button_down_notifies_but_only_left_drives_selection(self):
        selected, pressed = mock.Mock(), mock.Mock()
        with mock.patch("harness_discussion.win.trigger.mouse.Listener"):
            watcher = SelectionTrigger(selected, pressed)
        watcher._clicks = mock.Mock()
        for button in (Button.left, Button.right, Button.middle):
            watcher._click(10, 20, button, True)
        self.assertEqual(pressed.call_count, 3)
        watcher._clicks.down.assert_called_once_with(10, 20)
        for button in (Button.right, Button.middle):
            watcher._click(10, 20, button, False)
        watcher._clicks.up.assert_not_called()
        selected.assert_not_called()


class SignInTask(unittest.TestCase):
    def test_startup_round_trip(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        service, tasks = MagicMock(), {}
        folder = service.GetFolder.return_value
        folder.GetTask.side_effect = lambda name: tasks[name]
        folder.RegisterTaskDefinition.side_effect = lambda name, *args: tasks.update({name: SimpleNamespace(Enabled=True)})
        folder.DeleteTask.side_effect = lambda name, flags: tasks.pop(name)
        with patch.object(settings, "_scheduler", return_value=service):
            name = "harness-discussion-unittest"
            try:
                settings.set_startup(True, name)
                self.assertTrue(settings.startup_enabled(name))
                tasks[name].Enabled = False
                self.assertFalse(settings.startup_enabled(name))
                settings.set_startup(False, name)
                self.assertFalse(settings.startup_enabled(name))
                settings.set_startup(False, name)  # already off: no error
            finally:
                settings.set_startup(False, name)


    def test_legacy_task_names(self):
        self.assertEqual(settings.TASK, "Harness Discussion")
        self.assertEqual(settings.LEGACY_TASKS, ("Codex Speak", "Select to TTS"))  # newest first

    def test_installed_build_replaces_the_old_task(self):
        from unittest.mock import call, patch
        for legacy in settings.LEGACY_TASKS:
            replaced = [call(True), call(False, legacy)]
            for old_enabled, expected in ((True, replaced), (False, replaced[1:])):  # a disabled one stays off
                with self.subTest(legacy=legacy, old_enabled=old_enabled), \
                        patch.object(settings.sys, "frozen", True, create=True), \
                        patch.object(settings, "_task_enabled", side_effect=lambda name: old_enabled if name == legacy else None), \
                        patch.object(settings, "startup_enabled", return_value=False), \
                        patch.object(settings, "set_startup") as set_startup, \
                        patch.object(settings.winreg, "OpenKey", side_effect=OSError):
                    settings.refresh_startup()
                self.assertEqual(set_startup.call_args_list, expected)

    def test_both_legacy_tasks_are_removed_and_the_first_found_decides(self):
        from unittest.mock import call, patch
        both = [call(False, name) for name in settings.LEGACY_TASKS]
        for states, expected in (((True, False), [call(True), *both]), ((False, True), both),
                                 ((None, True), [call(True), both[1]])):
            found = dict(zip(settings.LEGACY_TASKS, states))
            with self.subTest(states=states), patch.object(settings.sys, "frozen", True, create=True), \
                    patch.object(settings, "_task_enabled", side_effect=lambda name: found.get(name)), \
                    patch.object(settings, "set_startup") as set_startup, \
                    patch.object(settings.winreg, "OpenKey", side_effect=OSError):
                settings.refresh_startup()
            self.assertEqual(set_startup.call_args_list, expected)

    def test_disabled_select_to_tts_task_cannot_override_an_enabled_codex_speak_task(self):
        from unittest.mock import call, patch
        found = {"Codex Speak": True, "Select to TTS": False}
        with patch.object(settings.sys, "frozen", True, create=True), \
                patch.object(settings, "_task_enabled", side_effect=lambda name: found.get(name)), \
                patch.object(settings, "set_startup") as set_startup, \
                patch.object(settings.winreg, "OpenKey", side_effect=OSError):
            settings.refresh_startup()
        self.assertEqual(set_startup.call_args_list,
                         [call(True), call(False, "Codex Speak"), call(False, "Select to TTS")])

    def test_current_task_state_wins_over_legacy_tasks(self):
        from unittest.mock import call, patch
        for current in (True, False):
            with self.subTest(current=current), patch.object(settings.sys, "frozen", True, create=True), \
                    patch.object(settings, "_task_enabled", side_effect=lambda name: current if name == settings.TASK else not current), \
                    patch.object(settings, "set_startup") as set_startup, \
                    patch.object(settings.winreg, "OpenKey", side_effect=OSError):
                settings.refresh_startup()
            self.assertEqual(set_startup.call_args_list,
                             [call(True)] * current + [call(False, name) for name in settings.LEGACY_TASKS])

    def test_source_run_leaves_the_old_task_alone(self):
        from unittest.mock import patch
        with patch.object(settings.sys, "frozen", False, create=True), \
                patch.object(settings, "startup_enabled", return_value=True), \
                patch.object(settings, "_task_enabled", return_value=True) as old_task, \
                patch.object(settings, "set_startup") as set_startup, \
                patch.object(settings.winreg, "OpenKey", side_effect=OSError):
            settings.refresh_startup()
        set_startup.assert_not_called()
        old_task.assert_not_called()

    def test_installed_refresh_keeps_a_disabled_current_task_off(self):
        from unittest.mock import call, patch
        for legacy in settings.LEGACY_TASKS:
            for old_enabled in (None, False, True):
                for legacy_run in (False, True):
                    with self.subTest(legacy=legacy, old_enabled=old_enabled, legacy_run=legacy_run), \
                            patch.object(settings.sys, "frozen", True, create=True), \
                            patch.object(settings, "_task_enabled", side_effect=lambda name: old_enabled if name == legacy else False if name == settings.TASK else None), \
                            patch.object(settings, "set_startup") as set_startup, \
                            patch.object(settings, "_drop_run_value") as drop_run, \
                            patch.object(settings.winreg, "QueryValueEx", return_value=("SelectToTTS.exe", 1)), \
                            patch.object(settings.winreg, "OpenKey", side_effect=None if legacy_run else OSError):
                        settings.refresh_startup()
                    self.assertEqual(set_startup.call_args_list, [] if old_enabled is None else [call(False, legacy)])
                    self.assertEqual(drop_run.call_count, int(legacy_run))

    def test_disabled_legacy_task_overrides_an_older_run_value(self):
        from unittest.mock import patch
        for legacy in settings.LEGACY_TASKS:
            with self.subTest(legacy=legacy), patch.object(settings.sys, "frozen", True, create=True), \
                    patch.object(settings, "_task_enabled", side_effect=lambda name: False if name == legacy else None), \
                    patch.object(settings, "set_startup") as set_startup, \
                    patch.object(settings, "_drop_run_value") as drop_run, \
                    patch.object(settings.winreg, "QueryValueEx", return_value=("SelectToTTS.exe", 1)), \
                    patch.object(settings.winreg, "OpenKey"):
                settings.refresh_startup()
            set_startup.assert_called_once_with(False, legacy)
            drop_run.assert_called_once()

    def test_installed_disable_removes_every_disabled_task(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        service = MagicMock()
        tasks = {name: SimpleNamespace(Enabled=False) for name in (settings.TASK, *settings.LEGACY_TASKS)}
        folder = service.GetFolder.return_value
        folder.GetTask.side_effect = lambda name: tasks[name]
        folder.DeleteTask.side_effect = lambda name, flags: tasks.pop(name)
        with patch.object(settings.sys, "frozen", True, create=True), \
                patch.object(settings, "_scheduler", return_value=service), \
                patch.object(settings, "_drop_run_value"):
            settings.set_startup(False)
        self.assertEqual(tasks, {})

    def test_source_run_leaves_the_legacy_run_value_alone(self):
        from unittest.mock import patch
        with patch.object(settings.sys, "frozen", False, create=True), \
                patch.object(settings, "set_startup") as set_startup, \
                patch.object(settings.winreg, "OpenKey"), \
                patch.object(settings.winreg, "QueryValueEx", return_value=("SelectToTTS.exe", 1)) as query:
            settings.refresh_startup()
        query.assert_called_once()
        set_startup.assert_not_called()



class SapiRate(unittest.TestCase):
    def test_sapi_rate(self):
        self.assertEqual([sapi_rate(s) for s in (0.5, 1.0, 1.5, 2.0, 3.0)], [-6, 0, 4, 6, 10])



if __name__ == "__main__":
    unittest.main()


class ReadShortcut(unittest.TestCase):
    def make(self, spec):
        hits = []
        with mock.patch("harness_discussion.win.trigger.keyboard.Listener"):
            return HotKey(spec, lambda: hits.append(1)), hits

    def test_fires_after_release_by_virtual_key_under_any_layout(self):
        hotkey, hits = self.make("<ctrl>+<alt>+r")
        hotkey._press(Key.ctrl_l)
        hotkey._press(Key.alt_l)
        hotkey._press(KeyCode(vk=0x52, char="ר"))  # R on a Hebrew layout
        self.assertEqual(hits, [])  # not while the keys are down
        for key in (KeyCode(vk=0x52), Key.alt_l, Key.ctrl_l):
            hotkey._release(key)
        self.assertEqual(hits, [1])

    def test_extra_modifier_and_function_keys(self):
        hotkey, hits = self.make("<ctrl>+<alt>+r")
        for key in (Key.ctrl_l, Key.shift, Key.alt_l, KeyCode(vk=0x52)):
            hotkey._press(key)
        for key in (KeyCode(vk=0x52), Key.alt_l, Key.shift, Key.ctrl_l):
            hotkey._release(key)
        self.assertEqual(hits, [])
        hotkey, hits = self.make("<shift>+<f5>")
        hotkey._press(Key.shift_r)
        hotkey._press(Key.f5)
        hotkey._release(Key.f5)
        hotkey._release(Key.shift_r)
        self.assertEqual(hits, [1])

    def test_settings_record_the_virtual_key(self):
        self.assertEqual((KEYS[0x52], KEYS[0x30], KEYS[0x74]), ("r", "0", "f5"))

    def test_any_key_fires_alone_or_by_its_code(self):
        for spec, keys in (("r", [KeyCode(vk=0x52)]), ("<key_32>", [Key.space]),
                           ("<ctrl>+<key_192>", [Key.ctrl_l, KeyCode(vk=0xC0, char="`")])):
            hotkey, hits = self.make(spec)
            for key in keys:
                hotkey._press(key)
            for key in reversed(keys):
                hotkey._release(key)
            self.assertEqual(hits, [1], spec)

    def test_keys_stored_by_code_are_named_by_the_layout(self):
        self.assertEqual((vk("r"), vk("key_192")), (0x52, 192))
        self.assertNotIn(key_name(0x20), ("", "Key 32"))
        self.assertNotEqual(key_name(0x25), key_name(0x64))  # Left arrow and Num 4 share a scan code
        self.assertEqual(key_name(0x13), "Pause")
