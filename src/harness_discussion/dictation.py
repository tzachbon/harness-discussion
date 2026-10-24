"""The transcript chain for dictation: Local, Cursor, Codex, skipping unready sources."""
import logging

from . import codex_rt, stt, writer
from .local_stt import LocalStt
from .llm_cursor import CursorStt

log = logging.getLogger("harness_discussion")


class Error(Exception):
    """Safe to show: never contains the transcript."""


class CodexStt:
    name = "Codex"

    def status(self):
        return codex_rt.login_status()

    def transcribe(self, audio, language=""):
        return stt.transcribe(audio, language=language)


def sources():
    return [LocalStt(), CodexStt(), CursorStt()]


def transcribe(audio, chain, order, language="", cancelled=lambda: False):
    """(text, source name) from the first source that returns words."""
    by_name = {s.name: s for s in chain}
    for name, enabled in order:
        if cancelled():
            raise writer.Cancelled("The dictation was cancelled.")
        source = by_name.get(name)
        if not (enabled and source):
            continue
        try:
            if source.status() != "ready":
                continue
            text = (source.transcribe(audio, language=language, cancelled=cancelled) if name == "Local"
                    else source.transcribe(audio, language=language))
        except writer.Cancelled:
            raise
        except Exception as e:
            log.info("%s transcript failed: %s", name, type(e).__name__)
            continue
        if text and text.strip():
            return text.strip(), name
        log.info("%s transcript was empty", name)
    raise Error("No source could transcribe the recording.")
