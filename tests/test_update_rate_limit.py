import io
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import time
import unittest
import urllib.error
from email.message import EmailMessage
from unittest.mock import Mock, patch

from harness_discussion import updates
from harness_discussion.app_updates import Updates


class Response(io.BytesIO):
    def geturl(self):
        return self.url


def response(data, url):
    result = Response(data)
    result.url = url
    return result


def http_error(code, reason, headers=None, body=b""):
    hdrs = EmailMessage()
    for key, value in (headers or {}).items():
        hdrs[key] = value
    return urllib.error.HTTPError(updates.API, code, reason, hdrs, io.BytesIO(body))


MAC_LIMIT = ("Could not check for updates because GitHub is limiting requests from this network, "
             "so try again later.")
WIN_LIMIT = ("Could not update because GitHub is limiting requests from this network, "
             "so try again later.")


def release(tag="v0.10.0"):
    return {"tag_name": tag, "draft": False, "prerelease": False, "assets": [{
        "name": updates.ASSET, "state": "uploaded", "size": 3,
        "digest": "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        "browser_download_url": f"https://github.com/tzachbon/harness-discussion/releases/download/{tag}/{updates.ASSET}"}]}


class RateLimits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)
        self.cache_patch = patch.object(updates, "CACHE", self.cache)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        updates._rate_limit_until = None
        self.addCleanup(setattr, updates, "_rate_limit_until", None)

    def test_throttling_is_recognized_and_other_failures_are_not(self):
        now = 1_700_000_000
        cases = (
            ("quota", 403, "Forbidden", {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(now + 90)}, b"{}", True, None),
            ("retry-after", 403, "Forbidden", {"Retry-After": "15"}, b"{}", True, None),
            ("reason", 403, "rate limit exceeded", {}, b"", True, None),
            ("primary", 403, "Forbidden", {}, b'{"message":"API rate limit exceeded for 203.0.113.4."}', True, None),
            ("secondary", 403, "Forbidden", {}, b'{"message":"You have exceeded a secondary rate limit."}', True, None),
            ("case", 403, "Forbidden", {}, b'{"message":"Secondary Rate Limit hit"}', True, None),
            ("plain 429", 429, "Too Many Requests", {}, b"", True, None),
            ("permission", 403, "Forbidden",
             {"X-RateLimit-Remaining": "59", "X-RateLimit-Reset": str(now + 90)},
             b'{"message":"Resource not accessible by integration"}', False, "GitHub refused the request (HTTP 403)"),
            ("reset only", 403, "Forbidden", {"X-RateLimit-Reset": str(now + 90)}, b"not json", False,
             "GitHub refused the request (HTTP 403)"),
            ("server", 500, "Server Error", {}, b"secret body", False, "GitHub returned HTTP 500"),
        )
        for name, code, reason, headers, body, limited, message in cases:
            with self.subTest(name=name):
                updates._rate_limit_until = None
                path = self.cache / "rate-limit-until"
                path.unlink(missing_ok=True)
                err = http_error(code, reason, headers, body)
                with patch.object(time, "time", return_value=now), patch.object(updates, "urlopen", side_effect=err):
                    with self.assertRaises(ValueError) as caught:
                        updates.check()
                if limited:
                    self.assertIsInstance(caught.exception, updates.RateLimited)
                    self.assertTrue(path.is_file())
                else:
                    self.assertNotIsInstance(caught.exception, updates.RateLimited)
                    self.assertEqual(str(caught.exception), message)
                    self.assertNotIn("secret", str(caught.exception))
                    self.assertFalse(path.exists())
                    self.assertIsNone(updates._rate_limit_until)

    def test_deadlines_follow_the_server_clock(self):
        now = 1_700_000_000
        long = now + 48 * 60 * 60
        cases = {
            "reset": ({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(now + 500)}, now + 500),
            "retry": ({"Retry-After": "30"}, now + 30),
            "later reset": ({"Retry-After": "30", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(now + 500)}, now + 501),
            "later retry": ({"Retry-After": "800", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(now + 500)}, now + 801),
            "fallback": ({"Retry-After": "soon", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "nope"}, now + 3600),
            "expired": ({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(now - 5)}, now + 3600),
            "long": ({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(long)}, long),
        }
        for name, (headers, deadline) in cases.items():
            with self.subTest(name=name):
                updates._rate_limit_until = None
                path = self.cache / "rate-limit-until"
                path.unlink(missing_ok=True)
                err = http_error(403, "Forbidden", headers, b"{}")
                with patch.object(time, "time", return_value=now), patch.object(updates, "urlopen", side_effect=err):
                    with self.assertRaises(updates.RateLimited) as caught:
                        updates.check()
                self.assertEqual(caught.exception.deadline, deadline)
                self.assertEqual(int(path.read_text(encoding="utf-8").strip()), deadline)
                if name == "long":
                    self.assertGreater(deadline, now + 24 * 60 * 60)

    def test_cooldown_survives_restart_until_the_deadline(self):
        now = 1_700_000_000
        clock = {"now": now}
        limited = http_error(429, "rate limit exceeded", {"Retry-After": "100"})
        opened = patch.object(updates, "urlopen", side_effect=[
            limited, response(json.dumps(release()).encode(), updates.API)])
        with opened as calls, patch.object(time, "time", side_effect=lambda: clock["now"]), \
                patch.object(updates, "current_version", return_value="0.10.0"):
            with self.assertRaises(updates.RateLimited):
                updates.check()
            self.assertEqual(calls.call_count, 1)
            with self.assertRaises(updates.RateLimited):
                updates.check()
            updates._rate_limit_until = None
            with self.assertRaises(updates.RateLimited):
                updates.check()
            self.assertEqual(calls.call_count, 1)
            clock["now"] = now + 99
            with self.assertRaises(updates.RateLimited):
                updates.check()
            self.assertEqual(calls.call_count, 1)
            clock["now"] = now + 100
            self.assertIsNone(updates.check())
            self.assertEqual(calls.call_count, 2)

    def test_storage_failure_keeps_the_deadline_in_memory(self):
        now = 1_700_000_000

        def fail(*args, **kwargs):
            raise http_error(429, "Too Many Requests", {"Retry-After": "60"})

        with patch.object(time, "time", return_value=now), patch.object(updates, "urlopen", side_effect=fail) as opened, \
                patch.object(os, "replace", side_effect=OSError("denied")), \
                self.assertLogs("harness_discussion", level="INFO") as logs:
            with self.assertRaises(updates.RateLimited) as caught:
                updates.check()
            self.assertEqual(caught.exception.deadline, now + 60)
            self.assertIn("OSError", "\n".join(logs.output))
            self.assertFalse((self.cache / "rate-limit-until").exists())
            with self.assertRaises(updates.RateLimited):
                updates.check()
            opened.assert_called_once()


class RateLimitNotices(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)
        self.cache_patch = patch.object(updates, "CACHE", self.cache)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        updates._rate_limit_until = None
        self.addCleanup(setattr, updates, "_rate_limit_until", None)
        cooldown = self.cache / "rate-limit-until"
        if cooldown.exists():
            cooldown.unlink()
        self.frozen = patch.object(sys, "frozen", True, create=True)
        self.frozen.start()
        self.addCleanup(self.frozen.stop)
        self.app = Updates.__new__(Updates)
        app = self.app
        app.host, app.settings_win = Mock(), None
        app.cfg = {"auto_update": True}
        app.events = queue.Queue()
        app._closing = app._update_busy = app._update_manual = False
        app._pending_update = None
        app._update_text = ""
        app.update_asset = None

    def test_each_check_reports_the_limit_and_the_next_check_stays_offline(self):
        def fail(*args, **kwargs):
            raise http_error(403, "rate limit exceeded", {"Retry-After": "120"})

        notices = {"darwin": ("update_available", MAC_LIMIT), "win32": ("update_result", WIN_LIMIT)}
        with patch.object(updates, "urlopen", side_effect=fail) as opened, patch.object(updates, "download") as download:
            for platform, manual in (("darwin", True), ("darwin", False), ("win32", True), ("win32", False)):
                with self.subTest(platform=platform, manual=manual):
                    updates._rate_limit_until = None
                    (self.cache / "rate-limit-until").unlink(missing_ok=True)
                    kind, text = notices[platform]
                    before = opened.call_count
                    with patch.object(sys, "platform", platform):
                        self.app._update_busy = False
                        self.app.check_update(manual=manual)
                        event = self.app.events.get(timeout=2)
                        self.app._update_busy = False
                        self.app.check_update(manual=manual)
                        again = self.app.events.get(timeout=2)
                    self.assertEqual(event, (kind, None, text))
                    self.assertEqual(again, (kind, None, text))
                    self.assertNotIn("up to date", text)
                    self.assertNotIn("HTTP", text)
                    download.assert_not_called()
                    self.assertEqual(opened.call_count, before + 1)

    def test_a_saved_cooldown_blocks_the_network_for_every_check(self):
        (self.cache / "rate-limit-until").write_text(f"{int(time.time()) + 3600}\n", encoding="utf-8")
        updates._rate_limit_until = None
        notices = {"darwin": ("update_available", MAC_LIMIT), "win32": ("update_result", WIN_LIMIT)}
        with patch.object(updates, "urlopen") as opened, patch.object(updates, "download") as download:
            for platform, manual in (("darwin", True), ("darwin", False), ("win32", True), ("win32", False)):
                with self.subTest(platform=platform, manual=manual):
                    kind, text = notices[platform]
                    with patch.object(sys, "platform", platform):
                        self.app._update_busy = False
                        self.app.check_update(manual=manual)
                        event = self.app.events.get(timeout=2)
                    self.assertEqual(event, (kind, None, text))
            opened.assert_not_called()
            download.assert_not_called()

    def test_other_http_failures_keep_a_safe_status(self):
        def fail(*args, **kwargs):
            raise http_error(500, "Server Error", {}, b"secret body")

        expected = {
            "darwin": ("update_available", None, "Could not check for updates: GitHub returned HTTP 500"),
            "win32": ("update_result", None, "Could not update: GitHub returned HTTP 500"),
        }
        with patch.object(updates, "urlopen", side_effect=fail), patch.object(updates, "download") as download:
            for platform, event in expected.items():
                with self.subTest(platform=platform):
                    with patch.object(sys, "platform", platform):
                        self.app._update_busy = False
                        self.app.check_update()
                        self.assertEqual(self.app.events.get(timeout=2), event)
                        self.assertNotIn("secret", event[2])
            download.assert_not_called()
        self.assertFalse((self.cache / "rate-limit-until").exists())
        self.assertIsNone(updates._rate_limit_until)

    def test_handling_the_failure_clears_busy_and_the_next_click_stays_offline(self):
        from harness_discussion.core import App
        app = App.__new__(App)
        app.host, app.chain, app.settings_win, app.stt = Mock(), Mock(busy=False), None, None
        app.popup = Mock()
        app.cfg = {"auto_update": True}
        app.events = queue.Queue()
        app._draining_stt = []
        app._read_options = None
        app._closing = app._update_busy = app._update_manual = False
        app._pending_update = None
        app._update_text = ""
        app.update_asset = None
        (self.cache / "rate-limit-until").write_text(f"{int(time.time()) + 3600}\n", encoding="utf-8")
        updates._rate_limit_until = None
        with patch.object(sys, "platform", "win32"), patch.object(updates, "urlopen") as opened, \
                patch.object(updates, "download") as download:
            app.check_update()
            app._handle(*app.events.get(timeout=2))
            self.assertEqual(app.update_state(), (WIN_LIMIT, False))
            self.assertIsNone(app._pending_update)
            app.check_update()
            app._handle(*app.events.get(timeout=2))
            self.assertEqual(app.update_state(), (WIN_LIMIT, False))
            self.assertIsNone(app._pending_update)
            opened.assert_not_called()
            download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
