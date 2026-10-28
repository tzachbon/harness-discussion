"""Both native settings surfaces retain, validate, remove and disable learned vocabulary."""
import sys
import unittest
from unittest.mock import patch

if sys.platform == "win32":
    from tests import test_win_history_settings as fixtures
elif sys.platform == "darwin":
    from tests import test_mac_writing_page as fixtures
else:
    raise unittest.SkipTest("Native settings need Windows or macOS")


class VocabularySettings(unittest.TestCase):
    def setUp(self):
        if sys.platform == "win32":
            fixtures.HistorySettingsUi.setUp(self)
            self.addCleanup(lambda: fixtures.HistorySettingsUi.tearDown(self))
            self.page = self.window
        else:
            fixtures.WritingSettingsTest.setUp(self)
            self.page = self.win

    def enter(self, text):
        if sys.platform == "win32":
            self.page.words_text.delete("1.0", "end")
            self.page.words_text.insert("1.0", text)
        else:
            self.page.words_text.setStringValue_(text)

    def text(self):
        if sys.platform == "win32":
            return self.page.words_text.get("1.0", "end").strip()
        return str(self.page.words_text.stringValue())

    def test_save_remove_and_clear_words(self):
        self.enter("Tzach\nיהונתן\nOpenWhispr")
        self.page._save_words()
        self.assertEqual(self.changes[-1], ("dictation_words", ["Tzach", "יהונתן", "OpenWhispr"]))
        self.enter("יהונתן\nOpenWhispr")
        self.page._save_words()
        self.assertEqual(self.changes[-1], ("dictation_words", ["יהונתן", "OpenWhispr"]))
        self.page._clear_words()
        self.assertEqual(self.changes[-1], ("dictation_words", []))
        self.assertEqual(self.text(), "")

    def test_invalid_word_does_not_overwrite_vocabulary(self):
        self.enter("Tzach\na@b.com")
        previous = list(self.changes)
        if sys.platform == "win32":
            with patch("harness_discussion.win.learning_page.messagebox.showerror") as error:
                self.page._save_words()
                error.assert_called_once()
        else:
            self.page._save_words()
            self.app.host.notify.assert_called_once()
        self.assertEqual(self.changes, previous)

    def test_learning_defaults_on_and_switch_saves_off(self):
        self.assertIs(self.page.cfg["dictation_learn"], True)
        if sys.platform == "win32":
            self.assertTrue(self.page.learning_on.get())
            self.page.learning_check.invoke()
        else:
            self.assertEqual(self.page.learning_switch.state(), 1)
            self.page.learning_switch.setState_(0)
            control = self.page.learning_switch
            control.sendAction_to_(control.action(), control.target())
        self.assertEqual(self.changes[-1], ("dictation_learn", False))


if __name__ == "__main__":
    unittest.main()
