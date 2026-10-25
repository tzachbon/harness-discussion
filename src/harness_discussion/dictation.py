"""The transcript chain for dictation: Local, Cursor and Codex, in the saved order, skipping unready sources."""
import logging

from . import codex_rt, stt, writer
from .llm_cursor import CursorStt
from .local_stt import LocalStt

log = logging.getLogger("harness_discussion")


class Error(Exception):
    """Safe to show: never contains the transcript."""


class CodexStt:
    name = "Codex"

    def status(self):
        return codex_rt.login_status()

    def prewarm(self):
        stt.prewarm()

    def transcribe(self, audio, language="", cancelled=lambda: False):
        return stt.transcribe(audio, language=language, cancelled=cancelled)


def sources():
    return [LocalStt(), CodexStt(), CursorStt()]


def transcribe(audio, chain, order, language="", cancelled=lambda: False):
    """(text, source name) from the first source that returns words."""
    by_name = {s.name: s for s in chain}

    def check():
        if cancelled():
            raise writer.Cancelled("The dictation was cancelled.")

    for name, enabled in order:
        check()
        source = by_name.get(name)
        if not (enabled and source):
            continue
        try:
            check()
            if source.status() != "ready":
                continue
            check()
            text = (source.transcribe(audio, language=language, cancelled=cancelled)
                    if name == "Local" or isinstance(source, CodexStt)
                    else source.transcribe(audio, language=language))
        except writer.Cancelled:
            raise
        except Exception as e:
            check()
            log.info("%s transcript failed: %s", name, type(e).__name__)
            continue
        check()
        if text and text.strip():
            return text.strip(), name
        log.info("%s transcript was empty", name)
    check()
    raise Error("No source could transcribe the recording.")
