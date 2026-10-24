"""The Edge neural engine, the threaded engine base, and the chain that tries engines in order."""
import contextlib
import logging
import math
import threading
import time

import edge_tts

from . import lang
from .audio import NoAudioError, PcmPlayer
from .edge_stream import EdgeStream

log = logging.getLogger(__name__)


class _Threaded:
    """Runs a read on a worker thread: `_run(text, tag, stop, on_audio)`, or whatever `_start` is given. Runs never overlap."""
    speed = 1.0

    def __init__(self):
        self._stop, self._busy, self.paused = threading.Event(), threading.Lock(), threading.Event()

    def speak(self, text, lang_tag, on_done, on_audio=lambda: None):
        self._start(lambda stop: self._run(text, lang_tag or lang.detect(text), stop, on_audio), on_done)

    def _start(self, run, on_done, cleanup=lambda: None):
        paused = self.paused.is_set()
        self.stop()
        if paused:
            self.paused.set()
        self._stop = stop = threading.Event()

        def work():
            with self._busy:
                try:
                    if not stop.is_set():
                        run(stop)
                except Exception as e:
                    return on_done(e)
                finally:
                    cleanup()  # also when a newer read replaced this one before it started
            on_done(None)

        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        self._stop.set()
        self.paused.clear()

    def pause(self):
        self.paused.set()

    def resume(self):
        self.paused.clear()

    def close(self):
        self.stop()


class EdgeEngine(_Threaded):
    name = "Edge"
    reports_progress = True  # speak() takes on_progress(chars): where in the text the speaker is
    Player = PcmPlayer  # tests swap in one without a sound device

    def __init__(self):
        super().__init__()
        self._pre = None  # (request, stream) started before Play

    async def _source(self, text, voice, rate):
        stream = edge_tts.Communicate(text, voice, rate=rate).stream()
        async with contextlib.aclosing(stream):  # a stopped read closes the connection, not the garbage collector
            async for chunk in stream:
                if chunk["type"] == "audio":
                    yield chunk["data"]

    def _request(self, text, tag):
        voice = lang.LANGS.get(tag, lang.LANGS["en-US"])[1]
        return text, voice, f"{round((self.speed - 1) * 100):+d}%"

    def discard(self):
        """Drop a prefetch. Never stops a read in progress."""
        pre, self._pre = self._pre, None
        if pre:
            pre[1].cancel()

    def prepare(self, lang_tag, text=None):
        """Start the request before Play, so Play joins it. No text: just drop what was prepared."""
        self.discard()
        if text:
            request = self._request(text, lang_tag or lang.detect(text))
            self._pre = request, EdgeStream(self._source(*request))

    def speak(self, text, lang_tag, on_done, on_audio=lambda: None, on_progress=lambda chars: None):
        tag = lang_tag or lang.detect(text)
        request, pre = self._request(text, tag), self._pre
        if pre and pre[0] == request and not pre[1].error:  # a prefetch that failed is not worth joining
            self._pre, stream = None, pre[1]  # claimed first: stop() in _start drops only a stale one
        else:
            self.discard()
            stream = EdgeStream(self._source(*request))  # starts now, not when the worker gets the lock
        self._start(lambda stop: self._play(stream, stop, on_audio, len(text), on_progress), on_done, stream.cancel)

    def stop(self):
        self.discard()
        super().stop()

    def _play(self, stream, stop, on_audio, chars=0, on_progress=lambda chars: None):
        player, total = None, 0
        try:
            chunks = stream.read(stop)
            while True:
                starved, t = player is not None and not player.pending, time.monotonic()
                try:
                    pcm = next(chunks, None)
                except Exception as e:
                    if player is None:
                        raise NoAudioError(f"{self.name}: {e}") from e
                    raise  # speech already started: the chain must not restart it on another engine
                if pcm is None:
                    break
                if starved and time.monotonic() - t > 0.3:
                    log.info("edge stream stalled %.1fs after first audio", time.monotonic() - t)
                if player is None:
                    try:
                        player = self.Player(24000, 1, self.paused)
                    except Exception as e:
                        raise NoAudioError(f"{self.name}: {e}") from e
                    player.arm(on_audio)
                player.write(pcm)
                total += len(pcm)
            if stop.is_set():
                return
            if player is None:
                raise NoAudioError(f"{self.name}: no audio")
            while player.pending and player.active and not stop.wait(0.05):
                # the queue still holds speech after the download ends; only now is the whole length known
                on_progress(chars * player.played // total)  # shortcut: assumes an even pace across the text
        finally:
            stream.cancel()
            if player:
                player.close()


class Chain:
    """Tries engines in order, moving on only when one fails before producing any speech."""

    def __init__(self, engines):
        self.engines, self.only, self.last, self.prefetch = engines, None, None, False
        self.enabled = [e.name for e in engines]  # the user's order; disabled sources are left out
        self.on_engine = lambda name: None  # called on the reading thread as each attempt starts
        self.statuses = {}  # core's cached status() per engine; order() never calls status(), which can block
        self._active, self._gen, self._paused = None, 0, False
        self._idle = threading.Event()
        self._idle.set()

    @property
    def busy(self):
        return not self._idle.is_set()

    @property
    def active_name(self):
        return self._active.name if self._active else None

    def continuous(self):
        """Codex reads a whole selection in one session; other engines read sentence by sentence."""
        return self.order()[0].name == "Codex"

    def order(self):
        if self.only:
            return [e for e in self.engines if e.name == self.only] or self.engines
        on = [e for name in self.enabled for e in self.engines if e.name == name]
        # a missing or signed-out source would fail every sentence; an unchecked one is still tried
        ready = [e for e in on if self.statuses.get(e.name, "ready") == "ready"]
        # skips engines that cannot reach the chosen speed (Codex cannot read faster)
        fit = [e for e in ready if getattr(e, "min_speed", 0) <= e.speed <= getattr(e, "max_speed", math.inf)]
        return fit or ready or on or self.engines

    def prepare(self, lang_tag, text=None, *, fallback_tag=None):
        first = self.order()[0]
        for e in self.engines:
            if e is not first and hasattr(e, "discard"):
                e.discard()  # a speed or engine change can hand the read to another engine
        if hasattr(first, "prepare"):
            tag = lang_tag if first.name == "Codex" else lang_tag or fallback_tag
            first.prepare(tag, text if self.prefetch else None)

    def prepare_next(self, text, lang_tag, *, fallback_tag=None):
        """While one piece plays, starts fetching the next on the engine reading it, whatever `prefetch` says."""
        engine = self._active
        if engine and engine.name != "Codex" and hasattr(engine, "prepare"):
            engine.prepare(lang_tag or fallback_tag, text)

    def speak(self, text, lang_tag, on_done, on_audio=lambda: None, *, fallback_tag=None, on_caption=None,
              on_progress=None):
        self._gen += 1
        self._idle = idle = threading.Event()
        self._paused = False
        gen, order = self._gen, self.order()
        for e in self.engines:
            if e is not order[0]:
                e.stop()

        def attempt(i):
            engine = self._active = order[i]
            self.on_engine(engine.name)
            if hasattr(engine, "paused"):
                engine.paused.set() if self._paused else engine.paused.clear()

            def done(err):
                if gen != self._gen:
                    return  # superseded by a newer speak()
                if isinstance(err, NoAudioError) and i + 1 < len(order):
                    log.info("%s had no audio (%s), trying %s", engine.name, type(err.__cause__).__name__,
                             order[i + 1].name)
                    return attempt(i + 1)
                self.last = engine.name
                idle.set()  # a late completion cannot mark a newer read idle
                on_done(err)

            codex = engine.name == "Codex"
            kw = {"on_caption": lambda t: gen == self._gen and on_caption(t)} if codex and on_caption else {}
            if getattr(engine, "reports_progress", False) and on_progress:
                kw = {"on_progress": lambda chars: gen == self._gen and on_progress(chars)}
            engine.speak(text, lang_tag if codex else lang_tag or fallback_tag, done,
                         lambda: gen == self._gen and on_audio(), **kw)
            if gen == self._gen and self._paused:
                engine.pause()  # a pause made before a fallback carries over to the next engine

        attempt(0)

    def stop(self):
        self._gen += 1
        self._idle.set()
        for e in self.engines:
            e.stop()

    def pause(self):
        self._paused = True
        if self._active:
            self._active.pause()

    def resume(self):
        self._paused = False
        if self._active:
            self._active.resume()

    def close(self):
        for e in self.engines:
            e.close()

def causes(err):
    """"NoAudioError from HTTPError 401": type names and HTTP codes only, since messages could echo the text."""
    names = []
    while err is not None and len(names) < 5:
        code = getattr(err, "code", None)
        names.append(f"{type(err).__name__} {code}" if isinstance(code, int) else type(err).__name__)
        err = err.__cause__ or err.__context__
    return " from ".join(names)
