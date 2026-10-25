"""Check as you type: what stops a check, and when a failed line is sent again."""
import unittest

from tests.test_app_live import _App
from tests.test_app_writer import Source


class Hook(Source):
    """A source that runs `during` as it is asked, as if the user acted while the request was out."""
    def __init__(self, name, reply, during):
        super().__init__(name, reply)
        self.during = during

    def rewrite(self, *args):
        self.during()
        return super().rewrite(*args)


class StopsAndRetries(unittest.TestCase):
    def setUp(self):
        self.app = _App()

    def test_turning_the_app_off_stops_a_check_before_the_next_source(self):
        first = Hook("Cursor", RuntimeError("down"), lambda: self.app.change("live_off_apps", ["notepad.exe"]))
        second = Source("Codex", "I think so, ok")
        self.app.writer_sources = [first, second]
        self.app.cfg["writer_sources"] = [["Cursor", True], ["Codex", True]]
        self.app.type("i thinks so, ok", at=0)
        self.app.tick(2)
        self.assertEqual(len(first.texts), 1)
        self.assertEqual(second.texts, [])

    def test_a_result_from_before_a_settings_change_is_not_kept(self):
        model = {"Cursor": {"model": "another"}}
        self.app.writer_sources = [Hook("Cursor", "I think so, ok", lambda: self.app.change("writer_models", model))]
        self.app.type("i thinks so, ok", at=0)
        self.app.tick(2)
        self.assertFalse(self.app.live_cache.has("i thinks so, ok", self.app._find_mode(None)))

    def test_a_line_every_source_failed_on_is_sent_again_only_after_more_typing(self):
        self.app.source.reply = RuntimeError("down")
        self.app.type("i thinks so, ok", at=0)
        for at in range(2, 8):
            self.app.tick(at)
        self.assertEqual(len(self.app.source.texts), 1)
        self.app.source.reply = "I think so, ok"
        self.app.type("i thinks so, ok!", at=10)
        self.app.tick(12)
        self.assertEqual(len(self.app.source.texts), 2)


if __name__ == "__main__":
    unittest.main()
