"""Local dictation lifecycle and ordered fallback, without model downloads or audio leaving the test."""
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness_discussion import dictation, local_stt, settings, stt, writer


class LocalTest(unittest.TestCase):
    def test_gpu_is_optional_and_a_probe_failure_keeps_cpu_loading_offline(self):
        with patch("faster_whisper.WhisperModel") as model, patch.object(
                local_stt, "_cuda_runtime", side_effect=OSError("driver details")):
            local_stt._load_model()
        self.assertEqual(model.call_args.kwargs["device"], "cpu")
        self.assertTrue(model.call_args.kwargs["local_files_only"])
        with patch("faster_whisper.WhisperModel") as model, patch.object(
                local_stt, "_cuda_runtime", return_value=True):
            local_stt._load_model()
        self.assertEqual(model.call_args.kwargs["device"], "cuda")
        self.assertEqual(model.call_args.kwargs["compute_type"], "int8_float16")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = patch.object(local_stt, "DIR", Path(self.temp.name) / "stt")
        p.start()
        self.addCleanup(p.stop)

    def install(self):
        p = patch.object(local_stt, "FILES", {n: (5, hashlib.sha256(b"model").hexdigest()) for n in local_stt.FILES})
        p.start()
        self.addCleanup(p.stop)
        def fetch(url, size, digest, path, progress, cancelled):
            path.write_bytes(b"model")
        with patch.object(local_stt.summarize, "_fetch", side_effect=fetch):
            local_stt.download()

    def test_missing_download_is_never_started_by_status_or_transcribe(self):
        source = local_stt.LocalStt()
        with patch.object(local_stt.summarize, "_fetch") as fetch:
            self.assertEqual(source.status(), "missing")
            with self.assertRaises(local_stt.Error):
                source.transcribe(b"audio")
            fetch.assert_not_called()

    def test_download_failure_leaves_no_ready_model(self):
        with patch.object(local_stt.summarize, "_fetch", side_effect=ValueError("bad hash")):
            with self.assertRaises(ValueError):
                local_stt.download()
        self.assertEqual(local_stt.LocalStt().status(), "missing")
        self.assertFalse(local_stt.DIR.exists())

    def test_warmed_model_is_loaded_once_and_keeps_multilingual_text(self):
        self.install()
        engine = Mock()
        engine.transcribe.side_effect = lambda *a, **k: (iter([SimpleNamespace(text=" hello"),
                                                               SimpleNamespace(text=" שלום")]), None)
        source = local_stt.LocalStt()
        self.addCleanup(source.close)
        with patch.object(local_stt, "_load_model", return_value=engine) as load, patch.object(
                local_stt.stt, "decode_audio", return_value=b"pcm"):
            source.prewarm()
            self.assertEqual(source.transcribe(b"audio", language="he"), "hello שלום")
            self.assertEqual(source.transcribe(b"audio"), "hello שלום")
        load.assert_called_once()
        self.assertEqual(engine.transcribe.call_args.kwargs["beam_size"], 1)
        self.assertIsNone(engine.transcribe.call_args.kwargs["language"])

    def test_closed_source_cannot_be_rewarmed(self):
        self.install()
        source = local_stt.LocalStt()
        source.close()
        with patch.object(local_stt, "_load_model") as load:
            source.prewarm()
            load.assert_not_called()

    def test_gpu_failure_during_lazy_inference_retries_once_on_cpu(self):
        self.install()
        gpu, cpu = Mock(), Mock()
        gpu.model.device = "cuda"
        def broken():
            yield SimpleNamespace(text="partial, discarded")
            raise RuntimeError("private GPU runtime details")
        gpu.transcribe.return_value = (broken(), None)
        cpu.transcribe.side_effect = lambda *a, **k: (iter([SimpleNamespace(text="complete")]), None)
        source = local_stt.LocalStt()
        self.addCleanup(source.close)
        with patch.object(local_stt, "_load_model", side_effect=[gpu, cpu]) as load, patch.object(
                local_stt.stt, "decode_audio", return_value=b"pcm"):
            self.assertEqual(source.transcribe(b"audio"), "complete")
            self.assertEqual(source.transcribe(b"audio"), "complete")
        self.assertEqual(load.call_args_list, [unittest.mock.call(), unittest.mock.call(force_cpu=True)])
        self.assertEqual(gpu.transcribe.call_count, 1)

    def test_cancelled_gpu_failure_does_not_start_a_cpu_retry(self):
        self.install()
        cancelled = threading.Event()
        gpu = Mock()
        gpu.model.device = "cuda"
        def fail(*a, **k):
            cancelled.set()
            raise RuntimeError("device failure")
        gpu.transcribe.side_effect = fail
        source = local_stt.LocalStt()
        self.addCleanup(source.close)
        with patch.object(local_stt, "_load_model", return_value=gpu) as load, patch.object(
                local_stt.stt, "decode_audio", return_value=b"pcm"):
            with self.assertRaises(writer.Cancelled):
                source.transcribe(b"audio", cancelled=cancelled.is_set)
        load.assert_called_once_with()

    def test_incomplete_install_never_constructs_a_model_or_fetches_files(self):
        self.install()
        (local_stt.DIR / "tokenizer.json").unlink()
        source = local_stt.LocalStt()
        with patch.object(local_stt, "_load_model") as load, patch.object(local_stt.summarize, "_fetch") as fetch:
            self.assertEqual(source.status(), "missing")
            with self.assertRaises(local_stt.Error):
                source.transcribe(b"audio")
            load.assert_not_called()
            fetch.assert_not_called()

    def test_cancel_during_model_load_never_starts_inference(self):
        self.install()
        source, cancelled, engine = local_stt.LocalStt(), threading.Event(), Mock()
        self.addCleanup(source.close)
        def load():
            cancelled.set()
            return engine
        with patch.object(local_stt, "_load_model", side_effect=load), patch.object(
                local_stt.stt, "decode_audio", return_value=b"pcm"):
            with self.assertRaises(writer.Cancelled):
                source.transcribe(b"audio", cancelled=cancelled.is_set)
        engine.transcribe.assert_not_called()

    def test_cancelled_queued_work_never_starts_inference(self):
        self.install()
        source = local_stt.LocalStt()
        self.addCleanup(source.close)
        cancelled, waiting, errors = threading.Event(), threading.Event(), []
        def work():
            waiting.set()
            try:
                source.transcribe(b"audio", cancelled=cancelled.is_set)
            except Exception as e:
                errors.append(e)
        with source._lock, patch.object(local_stt, "_load_model") as load:
            thread = threading.Thread(target=work)
            thread.start()
            self.assertTrue(waiting.wait(1))
            cancelled.set()
            thread.join(2)
            self.assertFalse(thread.is_alive(), "Cancel must not wait for another inference to finish")
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(errors[0], writer.Cancelled)
        load.assert_not_called()


class ChainTest(unittest.TestCase):
    def test_codex_restart_waits_for_old_request_and_can_cancel_while_queued(self):
        for cancel_waiter in (False, True):
            with self.subTest(cancel_waiter=cancel_waiter), tempfile.TemporaryDirectory() as folder:
                lock, waiting, cancel = threading.Lock(), threading.Event(), threading.Event()
                lock.acquire()  # A cancelled native request still owns the transport.
                def acquire(*args, **kwargs):
                    acquired = lock.acquire(*args, **kwargs)
                    if not acquired:
                        waiting.set()
                    return acquired
                busy = Mock(acquire=acquire, release=lock.release)
                results, errors = [], []
                def transcribe():
                    try:
                        results.append(dictation.transcribe(
                            b"new audio", [dictation.CodexStt()], [["Codex", True]],
                            cancelled=cancel.is_set))
                    except Exception as error:
                        errors.append(error)
                with patch.object(stt, "_busy", busy), patch.object(stt, "_run", return_value="new words") as run, \
                        patch.object(stt, "DIAG_LOG", Path(folder) / "diag.log"), \
                        patch.object(dictation.CodexStt, "status", return_value="ready"):
                    worker = threading.Thread(target=transcribe, daemon=True)
                    worker.start()
                    try:
                        self.assertTrue(waiting.wait(1))
                        self.assertTrue(worker.is_alive())
                        run.assert_not_called()
                        if cancel_waiter:
                            cancel.set()
                            worker.join(0.5)
                            self.assertFalse(worker.is_alive())
                            self.assertTrue(lock.locked())
                        else:
                            lock.release()
                            worker.join(1)
                        self.assertFalse(worker.is_alive())
                        if cancel_waiter:
                            self.assertEqual(results, [])
                            self.assertEqual(len(errors), 1)
                            self.assertIsInstance(errors[0], writer.Cancelled)
                            run.assert_not_called()
                        else:
                            self.assertEqual(errors, [])
                            self.assertEqual(results, [("new words", "Codex")])
                            run.assert_called_once()
                            self.assertFalse(lock.locked())
                    finally:
                        cancel.set()
                        if lock.locked():
                            lock.release()
                        worker.join(1)

    def test_ready_local_avoids_remote_status_checks_and_transcription(self):
        local = SimpleNamespace(name="Local", status=lambda: "ready", transcribe=lambda *a, **k: "words")
        remote = Mock(name="remote")
        remote.name = "Codex"
        self.assertEqual(dictation.transcribe(b"audio", [local, remote], [["Local", True], ["Codex", True]]),
                         ("words", "Local"))
        remote.status.assert_not_called()
        remote.transcribe.assert_not_called()

    def test_cancel_prevents_fallback_to_a_remote_source(self):
        cancel = threading.Event()
        def fail(*a, **k):
            cancel.set()
            raise RuntimeError("failed")
        local = SimpleNamespace(name="Local", status=lambda: "ready", transcribe=fail)
        remote = Mock()
        remote.name = "Codex"
        with self.assertRaises(writer.Cancelled):
            dictation.transcribe(b"audio", [local, remote], [["Local", True], ["Codex", True]],
                                 cancelled=cancel.is_set)
        remote.status.assert_not_called()

    def test_saved_source_order_gains_local_first_without_reenabling_explicit_choices(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = str(Path(tmp) / "settings.json")
            settings.save({"stt_sources": [["Codex", False], ["Cursor", True]]}, p)
            self.assertEqual(settings.load(p)["stt_sources"],
                             [["Local", True], ["Codex", False], ["Cursor", True]])
            settings.save({"stt_sources": [["Codex", True], ["Local", False], ["Cursor", True]]}, p)
            self.assertEqual(settings.load(p)["stt_sources"][1], ["Local", False])


if __name__ == "__main__":
    unittest.main()
