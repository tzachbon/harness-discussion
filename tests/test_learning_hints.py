"""Vocabulary settings and provider contracts, without model or network requests."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness_discussion import dictation, learning, local_stt, settings, stt, writer


class SettingsHints(unittest.TestCase):
    def test_defaults_corrupt_and_malformed_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            for content in (None, "{", "[]", "null", '{"dictation_learn": 0, "dictation_words": {}}'):
                with self.subTest(content=content):
                    if content is not None:
                        path.write_text(content, encoding="utf-8")
                    loaded = settings.load(str(path))
                    self.assertIs(loaded["dictation_learn"], True)
                    self.assertEqual(loaded["dictation_words"], [])

    def test_saved_words_use_shared_normalization_and_budget(self):
        words = [" Tzach ", "tzach", "יהונתן", "Open Whispr", None, {}, "a@b.com", "abc123",
                 "a" * 61, "one two three four"]
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "settings.json")
            settings.save({"dictation_learn": False, "dictation_words": words}, path)
            loaded = settings.load(path)
            self.assertIs(loaded["dictation_learn"], False)
            self.assertEqual(loaded["dictation_words"], ["tzach", "יהונתן", "Open Whispr"])
            words = ["Name" + chr(0x400 + i) for i in range(220)]
            settings.save({"dictation_words": words}, path)
            self.assertEqual(settings.load(path)["dictation_words"], learning.normalize(words))
            self.assertLessEqual(len(settings.load(path)["dictation_words"]), 200)
            self.assertLessEqual(len(learning.hint(words)), 1200)
            self.assertEqual(json.loads(Path(path).read_text())["dictation_words"], words)


class ProviderHints(unittest.TestCase):
    def test_local_actual_inference_kwargs_and_cpu_retry(self):
        for prompt in ("", "Tzach, יהונתן", "x" * 1200):
            with self.subTest(prompt_length=len(prompt)):
                source = local_stt.LocalStt()
                self.addCleanup(source.close)
                gpu, cpu = Mock(), Mock()
                gpu.model.device = "cuda"
                def broken():
                    yield SimpleNamespace(text="partial")
                    raise RuntimeError("private backend error")
                gpu.transcribe.return_value = (broken(), None)
                cpu.transcribe.return_value = (iter([SimpleNamespace(text=" words ")]), None)
                with patch.object(source, "_load", side_effect=lambda: setattr(source, "_model", gpu)), \
                        patch.object(stt, "decode_audio") as decode, \
                        patch.object(local_stt, "_load_model", return_value=cpu) as load:
                    self.assertEqual(source.transcribe(b"audio", language="he", prompt=prompt), "words")
                decode.assert_called_once_with(b"audio")
                load.assert_called_once_with(force_cpu=True)
                expected = {"language": "he", "beam_size": 1, "condition_on_previous_text": False}
                if prompt:
                    expected["initial_prompt"] = prompt
                for model in (gpu, cpu):
                    self.assertEqual(model.transcribe.call_args.kwargs, expected)
                    self.assertEqual(model.transcribe.call_args.args[0].getvalue(), b"audio")

    def test_codex_transport_arguments_and_empty_call_compatibility(self):
        cancelled = lambda: False
        for prompt in ("", "Tzach", "x" * 1200):
            with self.subTest(prompt_length=len(prompt)), patch.object(stt, "transcribe", return_value="words") as send:
                self.assertEqual(dictation.CodexStt().transcribe(
                    b"audio", language="he", cancelled=cancelled, prompt=prompt), "words")
                kwargs = {"language": "he", "cancelled": cancelled}
                if prompt:
                    kwargs["prompt"] = prompt
                send.assert_called_once_with(b"audio", **kwargs)

    def test_codex_existing_transport_receives_hint_and_cancellation(self):
        cancelled = lambda: False
        with tempfile.TemporaryDirectory() as folder, patch.object(stt, "DIAG_LOG", Path(folder) / "diag"), \
                patch.object(stt, "_run", return_value="words") as run:
            self.assertEqual(dictation.CodexStt().transcribe(
                b"audio", language="he", cancelled=cancelled, prompt="Tzach"), "words")
        self.assertEqual(run.call_args.args[:3], (b"audio", "he", "Tzach"))
        self.assertIs(run.call_args.args[4], cancelled)

    def test_codex_hint_can_cancel_while_waiting_for_existing_transport(self):
        cancelled = threading.Event()
        def acquire(**kwargs):
            cancelled.set()
            return False
        busy = Mock(acquire=acquire)
        with tempfile.TemporaryDirectory() as folder, patch.object(stt, "DIAG_LOG", Path(folder) / "diag"), \
                patch.object(stt, "_busy", busy), patch.object(stt, "_run") as run:
            with self.assertRaises(writer.Cancelled):
                dictation.CodexStt().transcribe(b"audio", prompt="Tzach", cancelled=cancelled.is_set)
        run.assert_not_called()
        busy.release.assert_not_called()

    def test_chain_only_known_sources_receive_hints_and_fallback_stays_ordered(self):
        for prompt in ("", "Tzach"):
            with self.subTest(prompt=prompt):
                local, codex = local_stt.LocalStt(), dictation.CodexStt()
                self.addCleanup(local.close)
                local.status, codex.status = Mock(return_value="ready"), Mock(return_value="ready")
                local.transcribe = Mock(side_effect=RuntimeError("private"))
                codex.transcribe = Mock(return_value="")
                # A strict unsupported adapter catches unknown prompt/cancelled arguments.
                cursor_call = Mock(return_value=" words ")
                cursor = SimpleNamespace(name="Cursor", status=lambda: "ready",
                                         transcribe=lambda audio, language="": cursor_call(audio, language=language))
                cancelled = lambda: False
                self.assertEqual(dictation.transcribe(b"audio", [local, codex, cursor],
                    [["Local", True], ["Codex", True], ["Cursor", True]],
                    language="he", cancelled=cancelled, prompt=prompt), ("words", "Cursor"))
                kwargs = {"language": "he", "cancelled": cancelled}
                if prompt:
                    kwargs["prompt"] = prompt
                local.transcribe.assert_called_once_with(b"audio", **kwargs)
                codex.transcribe.assert_called_once_with(b"audio", **kwargs)
                cursor_call.assert_called_once_with(b"audio", language="he")

    def test_disabled_and_unready_sources_are_skipped_with_hints(self):
        local, codex = local_stt.LocalStt(), dictation.CodexStt()
        self.addCleanup(local.close)
        local.status, local.transcribe = Mock(), Mock()
        codex.status, codex.transcribe = Mock(return_value="missing"), Mock()
        with self.assertRaises(dictation.Error):
            dictation.transcribe(b"audio", [local, codex], [["Local", False], ["Codex", True]], prompt="Tzach")
        local.status.assert_not_called()
        local.transcribe.assert_not_called()
        codex.transcribe.assert_not_called()

    def test_prompt_validation_and_cancellation_take_precedence_without_inference(self):
        local, codex = local_stt.LocalStt(), dictation.CodexStt()
        self.addCleanup(local.close)
        local.status = Mock(return_value="ready")
        operations = [local.transcribe, codex.transcribe,
                      lambda audio, **kwargs: dictation.transcribe(audio, [local], [["Local", True]], **kwargs)]
        with patch.object(local, "_load") as load, patch.object(stt, "transcribe") as send:
            for operation in operations:
                for prompt in (None, [], 42, "x" * 1201):
                    with self.subTest(operation=operation, prompt_type=type(prompt)):
                        with self.assertRaises(ValueError):
                            operation(b"audio", prompt=prompt)
                        with self.assertRaises(writer.Cancelled):
                            operation(b"audio", prompt=prompt, cancelled=lambda: True)
            load.assert_not_called()
            send.assert_not_called()
            local.status.assert_not_called()

    def test_cancelled_source_prevents_hint_fallback(self):
        local, codex = local_stt.LocalStt(), dictation.CodexStt()
        self.addCleanup(local.close)
        local.status = Mock(return_value="ready")
        local.transcribe = Mock(side_effect=writer.Cancelled("The dictation was cancelled."))
        codex.status = Mock()
        with self.assertRaises(writer.Cancelled):
            dictation.transcribe(b"audio", [local, codex], [["Local", True], ["Codex", True]], prompt="Tzach")
        codex.status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
