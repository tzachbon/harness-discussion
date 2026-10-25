"""Whisper Small on this computer. Only Download accesses the network, never transcription."""
import io
import importlib
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading

from . import settings, stt, summarize, writer

REVISION = "536b0662742c02347bc0e980a01041f333bce120"
URL = f"https://huggingface.co/Systran/faster-whisper-small/resolve/{REVISION}/"
FILES = {
    "config.json": (2370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
    "model.bin": (483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
    "tokenizer.json": (2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
    "vocabulary.txt": (459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
}
DIR = Path(settings.LOCAL_DIR) / "dictation-small"
log = logging.getLogger("harness_discussion")
_download_lock = threading.Lock()
_cuda_handles = []  # Windows DLL search directories and libraries must stay open for inference


class Error(Exception):
    """Adapter-owned messages, safe to show without backend details."""


def downloaded():
    try:
        return (json.loads((DIR / "ready.json").read_text(encoding="utf-8")) == REVISION
                and all((DIR / name).is_file() and (DIR / name).stat().st_size == size
                        for name, (size, _) in FILES.items()))
    except (OSError, ValueError):
        return False


def download():
    """Explicit one-time download, size/hash checked before the ready directory is published."""
    with _download_lock:
        if downloaded():
            return
        DIR.parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="dictation-download-", dir=DIR.parent))
        try:
            for name, (size, digest) in FILES.items():
                summarize._fetch(URL + name, size, digest, work / name, lambda n: None, lambda: False)
            (work / "ready.json").write_text(json.dumps(REVISION), encoding="utf-8")
            if DIR.exists():
                # Only this adapter's incomplete install. Downloads never replace a ready model in use.
                shutil.rmtree(DIR)
            os.replace(work, DIR)
        finally:
            shutil.rmtree(work, ignore_errors=True)


def _cuda_runtime():
    import ctypes
    import ctranslate2
    if sys.platform != "win32" or not ctranslate2.get_cuda_device_count():
        return False
    folders = [Path(next(iter(importlib.import_module(name).__path__))) / "bin"
               for name in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc")]
    for folder in folders:
        _cuda_handles.append(os.add_dll_directory(str(folder)))
    for folder in folders:
        for dll in sorted(folder.glob("*.dll")):
            _cuda_handles.append(ctypes.WinDLL(str(dll)))
    return True


def _load_model(force_cpu=False):
    from faster_whisper import WhisperModel
    if not force_cpu:
        try:
            if _cuda_runtime():
                return WhisperModel(str(DIR), device="cuda", compute_type="int8_float16", local_files_only=True)
        except (ImportError, OSError, RuntimeError) as e:
            log.info("Local dictation GPU unavailable: %s", type(e).__name__)
    # ponytail: half the logical cores approximates the fast cores (Apple P-cores, x86 physical cores).
    # Using every core starves the desktop and waits on efficiency cores. Read hw.perflevel0 if that misses.
    threads = max(1, (os.cpu_count() or 2) // 2)
    return WhisperModel(str(DIR), device="cpu", compute_type="int8", cpu_threads=threads, local_files_only=True)


class LocalStt:
    name = "Local"

    def __init__(self):
        self._model, self._closed = None, False
        self._lock = threading.Lock()  # model loading and inference have one owner, even after Esc

    def status(self):
        return "downloading" if _download_lock.locked() else "ready" if downloaded() else "missing"

    def _load(self):
        if self._closed:
            raise Error("Local dictation has stopped.")
        if not downloaded():
            raise Error("Download the local dictation model in Settings → Dictation first.")
        if self._model is None:
            self._model = _load_model()
        return self._model

    def prewarm(self):
        try:
            with self._lock:
                self._load()
        except Exception as e:
            log.info("Local dictation warmup failed: %s", type(e).__name__)

    def transcribe(self, audio, language="", cancelled=lambda: False):
        while not self._lock.acquire(timeout=0.1):
            if cancelled():
                raise writer.Cancelled("The dictation was cancelled.")
        try:
            if cancelled():
                raise writer.Cancelled("The dictation was cancelled.")
            self._load()
            stt.decode_audio(audio)  # common format, byte and duration limits before inference
            try:
                return self._transcript(audio, language, cancelled)
            except RuntimeError:
                if self._model.model.device != "cuda":
                    raise
                if cancelled():
                    raise writer.Cancelled("The dictation was cancelled.")
                log.info("Local dictation GPU failed; retrying on CPU")
                self._model = _load_model(force_cpu=True)
                return self._transcript(audio, language, cancelled)
        finally:
            self._lock.release()

    def _transcript(self, audio, language, cancelled):
        if cancelled():
            raise writer.Cancelled("The dictation was cancelled.")
        # Preserve the container's sample rate. Whisper resamples it to 16 kHz itself.
        segments, _ = self._model.transcribe(io.BytesIO(audio), language=language or None, beam_size=1,
                                             condition_on_previous_text=False)
        words = []
        try:
            for segment in segments:  # finish lazy inference while we still own the model
                if cancelled():
                    raise writer.Cancelled("The dictation was cancelled.")
                words.append(segment.text)
            if cancelled():
                raise writer.Cancelled("The dictation was cancelled.")
            return "".join(words).strip()
        finally:
            close = getattr(segments, "close", None)
            if close:
                close()

    def close(self):
        with self._lock:
            self._closed, self._model = True, None
