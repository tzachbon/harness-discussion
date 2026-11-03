import sys
import unittest

if sys.platform != "win32":
    raise unittest.SkipTest("Windows only")

from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from harness_discussion.core import App
from harness_discussion import settings, updates


class UpdateApp(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "update-123" / updates.ASSET
        self.path.parent.mkdir()
        self.path.write_bytes(b"abc")
        self.app = App.__new__(App)
        a = self.app
        a.host, a.chain = Mock(), Mock(busy=False)
        a.chain.engines = []
        a.chain.order.return_value = [SimpleNamespace(name="Codex")]
        a.popup = Mock()
        a.cfg = {**settings.DEFAULTS, "auto_update": True}
        a.events, a.settings_win, a.stt = queue.Queue(), None, None
        a._draining_stt = []
        a._read_options = None
        a._read_id = 0
        a._closing, a._update_busy, a._update_manual = False, False, False
        a._pending_update, a._update_text = None, "Ready to check."
        a._init_summaries()
        self.frozen = patch.object(sys, "frozen", True, create=True)
        self.frozen.start()

    def tearDown(self):
        self.frozen.stop()
        self.temp.cleanup()

    def result(self):
        self.app._handle("update_result", self.path, "Update downloaded.")

    def test_ready_update_waits_for_speech_then_flushes_before_launch_and_quits(self):
        a = self.app
        a.chain.busy = True
        order = []
        with patch.object(updates, "install", side_effect=lambda path: order.append("install")) as install, patch.object(
                settings, "save", side_effect=lambda cfg: order.append("save")):
            self.result()
            install.assert_not_called()
            a.chain.busy = False
            a._try_update()
            self.assertEqual(order, ["save", "install"])
            install.assert_called_once_with(self.path)
            a.host.quit.assert_called_once()
            self.assertTrue(a._closing)

    def test_update_waits_between_sentences_even_when_engine_is_idle(self):
        a = self.app
        a._read_options = (None, None, 1.0, "en-US")
        a._pending_next = True  # paused at a sentence boundary
        a.chain.busy = False
        with patch.object(updates, "install") as install, patch.object(settings, "save"):
            self.result()
            install.assert_not_called()
            a._read_options = None  # selection finished or was explicitly stopped
            a._try_update()
            install.assert_called_once()

    def test_update_waits_for_a_recording_or_rewrite(self):
        a = self.app
        with patch.object(updates, "install") as install, patch.object(settings, "save"):
            a._recorder = object()
            self.result()
            a.rewriting, a._recorder = True, None
            a._try_update()
            install.assert_not_called()
            a.rewriting = False
            a._try_update()
            install.assert_called_once()

    def test_busy_and_draining_servers_prevent_install_and_failed_launch_releases_admission(self):
        a = self.app
        server = Mock(busy=threading.Lock())
        a.stt = server
        server.busy.acquire()
        with patch.object(updates, "install", side_effect=OSError("cannot launch")) as install, patch.object(settings, "save"):
            self.result()
            install.assert_not_called()
            server.busy.release()
            old1, old2 = Mock(), Mock()
            a._draining_stt = [old1, old2]
            a._try_update()
            install.assert_not_called()
            a._handle("stt_closed", old2)
            a._try_update()
            install.assert_not_called()
            a._handle("stt_closed", old1)
            a._try_update()
            install.assert_called_once()
            self.assertFalse(server.busy.locked())
            a.host.quit.assert_not_called()
            self.assertIsNone(a._pending_update)
            self.assertIn("cannot launch", a._update_text)

    def test_disabled_auto_discards_download_but_manual_still_installs(self):
        a = self.app
        a.cfg["auto_update"] = False
        with patch.object(updates, "install") as install, patch.object(settings, "save"):
            self.result()
            install.assert_not_called()
            self.assertFalse(self.path.exists())
            self.path.parent.mkdir()
            self.path.write_bytes(b"abc")
            a._update_manual = True
            self.result()
            install.assert_called_once()

    def test_checks_use_a_worker_and_do_not_overlap_and_source_never_checks(self):
        a = self.app
        main_thread = threading.get_ident()
        threads = []
        with patch.object(updates, "check", side_effect=lambda: threads.append(threading.get_ident())) as check:
            a.check_update()
            a.check_update()
            event = a.events.get(timeout=2)
            self.assertEqual(event, ("update_result", None, "You are up to date."))
            a._handle(*event)
            check.assert_called_once()
            self.assertNotEqual(threads[0], main_thread)
            with patch.object(sys, "frozen", False):
                a.check_update()
                check.assert_called_once()

    def test_auto_timer_is_daily_and_respects_opt_in(self):
        a = self.app
        with patch.object(a, "check_update") as check:
            a._auto_update()
            check.assert_called_once_with(manual=False)
            a.host.after.assert_called_with(24 * 60 * 60 * 1000, a._auto_update)
            a.cfg["auto_update"] = False
            a._auto_update()
            check.assert_called_once()

    def test_disabling_stt_retains_a_draining_request_even_after_reenable(self):
        a = self.app
        draining, finish = threading.Event(), threading.Event()
        old = Mock(busy=threading.Lock())
        def close():
            draining.set()
            if not finish.wait(2):
                raise AssertionError("test did not finish transcription")
        old.server_close.side_effect = close
        a.stt = old
        try:
            a._run_stt(False)
            self.assertTrue(draining.wait(1))
            self.assertIsNone(a.stt)
            new = Mock(busy=threading.Lock())
            with patch("harness_discussion.core.stt_server.start", return_value=new):
                a._run_stt(True)
            with patch.object(updates, "install") as install, patch.object(settings, "save"):
                self.result()
                install.assert_not_called()
                self.assertEqual(a._draining_stt, [old])
                finish.set()
                a._handle(*a.events.get(timeout=1))
                self.assertEqual(a._draining_stt, [])
                # Avoid starting a second drain worker while this test exits.
                new.server_close.side_effect = lambda: None
                a._try_update()
                install.assert_called_once()
        finally:
            finish.set()

    def test_failed_check_is_retryable_without_settings_window_and_worker_result_cancels_auto(self):
        a = self.app
        with patch.object(updates, "check", side_effect=TimeoutError("offline")) as check:
            a.check_update()
            a._handle(*a.events.get(timeout=1))
            self.assertIn("offline", a.update_state()[0])
            self.assertFalse(a.update_state()[1])
            a.check_update()
            a._handle(*a.events.get(timeout=1))
            self.assertEqual(check.call_count, 2)
        checked, finish = threading.Event(), threading.Event()
        def download(asset):
            checked.set()
            if not finish.wait(2):
                raise AssertionError("test did not finish download")
            return self.path
        with patch.object(updates, "check", return_value={"version": "0.10.0"}), patch.object(
                updates, "download", side_effect=download), patch.object(updates, "install") as install:
            try:
                a.check_update(manual=False)
                self.assertTrue(checked.wait(1))
                a.cfg["auto_update"] = False
                finish.set()
                a._handle(*a.events.get(timeout=1))  # progress
                a._handle(*a.events.get(timeout=1))  # downloaded artifact
                install.assert_not_called()
                self.assertFalse(self.path.exists())
                self.assertFalse(a.update_state()[1])
            finally:
                finish.set()

    def test_rate_limit_clears_busy_and_a_second_click_stays_offline(self):
        a = self.app
        cache = Path(self.temp.name) / "rate-limit-cache"
        cache.mkdir()
        (cache / "rate-limit-until").write_text(f"{int(time.time()) + 3600}\n", encoding="utf-8")
        updates._rate_limit_until = None
        try:
            with patch.object(updates, "CACHE", cache), patch.object(updates, "urlopen") as opened, \
                    patch.object(updates, "download") as download:
                a.check_update()
                a._handle(*a.events.get(timeout=2))
                self.assertEqual(
                    a.update_state()[0],
                    "Could not update because GitHub is limiting requests from this network, so try again later.")
                self.assertFalse(a.update_state()[1])
                self.assertIsNone(a._pending_update)
                a.check_update()
                a._handle(*a.events.get(timeout=2))
                self.assertFalse(a.update_state()[1])
                self.assertIsNone(a._pending_update)
                opened.assert_not_called()
                download.assert_not_called()
        finally:
            updates._rate_limit_until = None

    def test_update_quit_stops_queue_pump_before_tk_is_used_again(self):
        a = self.app
        a.events.put(("update_result", self.path, "Update ready."))
        a.events.put(("settings",))
        with patch.object(updates, "install"), patch.object(settings, "save"):
            a.pump()
        a.host.quit.assert_called_once()
        a.host.after.assert_not_called()
        self.assertEqual(a.events.get_nowait(), ("settings",))


if __name__ == "__main__":
    unittest.main()
