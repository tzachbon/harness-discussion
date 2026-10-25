"""The platform-neutral app: reads selections aloud sentence by sentence, owns settings, updates and STT.

A host (win.host.TkHost or mac.host.MacHost) owns the main thread, windows, tray or menu bar,
and the OS services. Background threads only put events on `App.events`; the host drains it
on the main thread through `pump`.
"""
import logging
import os
import queue
import sys
import threading
import time

from . import app_skip, lang, sentences, settings, stt_server
from .app_skip import Skips
from .app_summarize import Summaries
from .app_updates import Updates
from .app_writer import Writer
from .captions import Captions
from .engines import causes as _causes
from .sources import Sources

log = logging.getLogger("harness_discussion")
# Codex's transcript arrives about 0.4 s before its audio, which then waits in the 0.25 s jitter cushion.
CAPTION_DELAY_S = 0.6
SPEECH_KEYS = ("sources", "voices", "speed", "prefetch")  # captured for the entire selection
# A short opening goes alone so speech starts fast; the rest goes in large pieces, each fetched while
# the one before plays, so no request waits on a line break. The opening is whole sentences reaching
# a third of the words, at most FIRST_WORDS, so a one-word first line doesn't end before the next piece is ready.
PIECE_CHARS = 5000
FIRST_WORDS = 30


class App(Captions, Sources, Updates, Summaries, Skips, Writer):
    def __init__(self, host):
        self.host = host
        host.app = self
        self.events, self._seq, self.settings_win, self._read_id = queue.Queue(), 0, None, 0
        self._units, self._unit_index, self._pending_next, self._read_options = [], 0, False, None
        self._continuous, self._last_caption, self._held_caption = False, "", ""
        self._caption_steps = self._caption_held = None
        self._read_text, self._sentences, self._sentence = "", [], 0  # what ⏮ and ⏭ step through
        self._tracked, self._hotkey_wait = False, None  # a caption matched _sentence; a shortcut read in flight
        self._read_start, self._by_position = 0, False
        self.stt, self.stt_error = None, None
        self._draining_stt = []
        self._closing, self._update_busy, self._update_manual = False, False, False
        self._pending_update, self.update_asset = None, None
        self._update_text = ("Checks at startup and daily when enabled." if sys.platform == "darwin"
                             else "Checks at startup and daily when enabled. Updates restart this app.")
        from .engines import Chain
        self.chain = Chain(host.engines())
        self.chain.on_engine = lambda name: self.events.put(("engine", name))
        self.statuses = {}  # engine name -> status(), filled by refresh_statuses
        self._checking = threading.Lock()  # one status check at a time, so an older slow one can't win
        first_run = not os.path.exists(settings.PATH) or settings.first_run(settings.PATH)  # or the installer wrote it
        self.cfg = settings.load()
        if first_run:
            try:
                settings.save(self.cfg)
            except OSError:
                pass  # Settings just shows again on the next start
            self.events.put(("settings", True))  # show new users where the app lives, with a welcome
        self._init_summaries()
        self._init_writer()
        for key, value in self.cfg.items():
            self.apply(key, value)
        # hosts without in-place editing build the bar without the writing layout
        writing = dict(on_rewrite=self.rewrite_from_bar, modes=self.mode_names,
                       on_escape=self._escape) if self.writer_ready else {}
        self.popup = host.make_popup(self.cfg["speed"], on_play=self.play, on_stop=self.stop,
                                     on_summarize=self.summarize,
                                     on_pause=self.pause, on_resume=self.resume,
                                     on_lang=self._prepare_selection, on_speed=self._popup_speed,
                                     on_settings=lambda: self.events.put(("settings",)),
                                     on_source=self._popup_source, sources=self._source_choices,
                                     on_skip=self.skip, **writing)
        if self.writer_ready:
            self.popup.set_last_mode(self.cfg["default_mode"])
        self._configure_captions()
        self._show_source()

    def start(self):
        """Starts listening for selections and schedules the update check."""
        def pressed(x, y):
            self._invalidate_dictation_target()
            self.events.put(("press", x, y))

        self.host.start_trigger(self._selected, pressed)
        self.host.after(30_000, self._auto_update)
        self.refresh_statuses()

    def apply(self, key, value):
        """Called on the main thread. Engines read these attributes when the next read starts."""
        if key.startswith("caption_"):
            if hasattr(self, "popup"):
                self._configure_captions()
            return
        if self._read_options and key in SPEECH_KEYS:
            return  # speech options are captured for the entire selection
        if key == "sources":
            self.chain.enabled = [name for name, on in value if on]
            self.chain.only = None  # also ends the pin on the voice that read the selection's first sentence
        elif key == "voices":
            for e in self.chain.engines:
                if hasattr(e, "voices"):  # an unknown id reads with the engine's default
                    e.voice = value.get(e.name) if value.get(e.name) in dict(e.voices) else None
        elif key == "speed":
            for e in self.chain.engines:
                e.speed = value
        elif key == "prefetch":
            self.chain.prefetch = value
        elif key == "stt_server":
            self._run_stt(value)
        elif key == "hotkey":
            self.host.set_hotkey(value, lambda: self.events.put(("hotkey",)))
        elif key in ("rewrite_hotkey", "dictate_hotkey", "default_mode") and self.writer_ready:
            self._apply_writer(key, value)

    def _configure_captions(self):
        self.popup.configure_captions(self.cfg["caption_font_size"], self.cfg["caption_background"],
                                      self.cfg["caption_background_opacity"])

    def _run_stt(self, on, tries=60):
        if on and not self.stt:
            try:
                self.stt, self.stt_error = stt_server.start(), None
            except OSError as e:
                # The port stays taken while an earlier server finishes a transcription, or another
                # program uses it. Retry for 2 minutes, then show the error in Settings.
                self.stt_error = f"Could not start the server: {e.strerror or e}"
                if tries == 60:
                    log.warning("speech-to-text server did not start: %s", e)
                if tries:
                    self.host.after(2000, lambda: self.cfg["stt_server"] and self._run_stt(True, tries - 1))
        elif not on:
            self.stt_error = "The server is off."
            if self.stt:
                server, self.stt = self.stt, None
                self._draining_stt.append(server)
                server.shutdown()  # up to 0.5 s
                server.socket.close()  # frees the port now for a quick re-enable
                # server_close() waits for a transcription in flight, so not on the main thread
                def close():
                    server.server_close()
                    self.events.put(("stt_closed", server))
                threading.Thread(target=close, daemon=True).start()

    def change(self, key, value, save=True):
        self.cfg[key] = value
        self.apply(key, value)
        if key == "speed":  # keep the popup and an open Settings window showing the same speed
            self.popup.set_speed(value)
            if self.settings_win and self.settings_win.alive:
                self.settings_win.set_speed(value)
        if key == "sources":
            self._show_source()
            if self.settings_win and self.settings_win.alive:
                self.settings_win.set_sources(value)
        if key in SPEECH_KEYS and not self._read_options and self.popup.text:
            self._prepare_selection(self.popup.lang_tag())
        if not save:
            return
        try:
            settings.save(self.cfg)
        except OSError as e:
            self.host.notify(f"Could not save settings: {e}")
        if key == "auto_update":
            if value:
                self.check_update(manual=False)
            elif not self._update_manual:
                self._discard_update()

    def _popup_speed(self, speed):
        self.change("speed", speed)

    def _prepare_selection(self, tag=None):
        if self._read_options or not self.popup.visible:
            return
        units = self._units_for(self.popup.text)
        if units:
            self.chain.prepare(tag, units[0], fallback_tag=lang.detect(self.popup.text))

    def test_voice(self, text):
        self.stop()  # a popup read in progress is replaced by the test
        if self.popup.visible:
            self.popup.hide()
        self._read_id += 1
        rid = self._read_id
        done, _ = self._logged(text, lambda err: self.events.put(("done", err, rid)))
        self.chain.speak(text, "en-US", done)

    def _selected(self, x, y):  # trigger thread: hand off, never block
        if self._selection_busy():
            return
        self._seq += 1
        threading.Thread(target=self._read, args=(self._seq, x, y), daemon=True).start()

    def _selection_busy(self):
        return self._holding or self._recorder is not None or self.rewriting

    def _read(self, seq, x, y):
        time.sleep(0.08)  # let the app finish updating its selection
        if seq != self._seq or self._selection_busy():
            return
        try:
            text = self.host.read_selection(self.cfg["clipboard_fallback"])
            if seq != self._seq or self._selection_busy():
                return
            ctx = self.edit.capture(False) if text and self.writer_ready and self.cfg["writer"] else None
        except Exception:
            return  # some apps throw from the accessibility API; no popup is the right outcome
        if text and seq == self._seq and not self._selection_busy():
            self.events.put(("show", text, x, y, bool(ctx and ctx.editable and not ctx.secure), seq))

    def _units_for(self, text):
        """Codex reads the whole selection in one session; other engines get the opening, then the rest."""
        if self.chain.continuous():
            return [text.strip()] if text.strip() else []
        units = sentences.split(text)
        if not units:
            return []
        goal, end, words = min(-(-len(text.split()) // 3), FIRST_WORDS), 0, 0
        for unit in units:
            stop = text.index(unit, end) + len(unit)
            if words and (words + len(unit.split()) > 2 * FIRST_WORDS or stop > PIECE_CHARS):
                break  # a long next sentence would delay the start more than a short opening does
            end, words = stop, words + len(unit.split())
            if words >= goal:
                break
        return [text[:end].strip(), *sentences.chunks(text[end:], PIECE_CHARS)]

    def play(self, text, lang_tag, only=None, start=0):
        """only: read through that engine alone, as a Settings Test button does. start: the sentence to begin at."""
        if self._read_options:
            self.stop()
        self._read_text, self._sentences, self._sentence = text, sentences.split(text), start
        self._read_start, self._tracked, self._by_position = start, False, False
        text = text[app_skip.offset(self._read_text, self._sentences, start):]
        self.chain.only = only  # after stop(), which clears it
        self._read_id += 1
        rid = self._read_id  # events from an older read are ignored when they arrive late
        self._continuous = self.chain.continuous()
        self._units, self._unit_index, self._pending_next = self._units_for(text), 0, False
        self._last_caption = self._held_caption = ""
        self._heard = (0, "")
        self._read_options = (lang_tag, self.cfg["sources"], self.cfg["speed"], lang.detect(text))
        self._read_done, self._read_heard = self._logged(text, lambda err: self.events.put(("done", err, rid)))
        self.popup.set_caption("")
        if self._units:
            self._start_unit()
        else:
            self._read_done(None)

    def _start_unit(self):
        rid, index = self._read_id, self._unit_index
        heard = self._read_heard
        self.chain.speak(self._units[index], self._read_options[0],
                         lambda err: self.events.put(("sentence_done", err, rid, index)),
                         lambda: (heard(), self.events.put(("audio", rid, index))),
                         fallback_tag=self._read_options[3],
                         on_caption=lambda text: self.events.put(("caption", rid, index, text)),
                         on_progress=lambda chars: self.events.put(("progress", rid, index, chars)))

    def _restore_speech_options(self):
        self._read_options = None
        for key in SPEECH_KEYS:
            self.apply(key, self.cfg[key])

    def stop(self):
        self._read_id += 1  # invalidate callbacks before asynchronous engine cleanup
        self._units, self._pending_next = [], False
        self._hotkey_wait = None  # a shortcut read still fetching its selection is cancelled too
        self.chain.stop()
        self._restore_speech_options()
        self.popup.set_playing(False)
        self._show_source()

    def pause(self):
        self.popup.set_caption_paused(True)
        self._hold_captions()
        self.chain.pause()

    def resume(self):
        self.popup.set_caption_paused(False)
        held, self._held_caption = self._held_caption, ""
        if held:
            self._display(held)  # tracked when it arrived
        self._resume_captions()
        self.chain.resume()
        if self._pending_next:
            self._pending_next = False
            self._start_unit()

    def _logged(self, text, on_done):
        """(done, heard): done logs the read, heard marks the first audio, which ends the spinner."""
        t0, first, speed = time.monotonic(), [], self.cfg["speed"]

        def done(err):  # never the text, nor an error message, which could echo the text
            error = _causes(err) if err else None
            log.info("read %d chars with %s in %.1fs (first audio %s) at %sx, error: %s", len(text),
                     self.chain.last, time.monotonic() - t0, f"{first[0]:.1f}s" if first else "-",
                     speed, error)
            on_done(err)
        return done, lambda: first or first.append(time.monotonic() - t0)

    def pump(self):
        """Main thread, every 30 ms: handles what background threads queued."""
        while not self.events.empty():
            kind, *args = self.events.get_nowait()
            try:
                self._handle(kind, *args)
            except Exception as e:
                log.exception("handling %s", kind)
                self.host.notify(f"Unexpected error: {e}")
            if self._closing:
                return

    def _handle(self, kind, *args):
        p = self.popup
        if kind == "stt_closed":
            self._draining_stt.remove(args[0])
        elif kind == "update_status":
            self._update_status(args[0])
        elif kind == "update_available":
            self._update_busy = False
            self.update_asset = args[0]
            self._update_status(args[1])
        elif kind == "update_result":
            self._update_busy = False
            self._pending_update = args[0]
            self._update_status(args[1])
            self._try_update()
        elif kind == "show":
            if self._selection_busy() or (len(args) > 4 and args[4] != self._seq):
                return
            text, x, y, editable = (*args, False)[:4]
            # double-clicking the popup is not a new selection, and a running read isn't interrupted
            if not (p.contains(x, y) or (p.playing and not p.paused) or self.rewriting):
                if p.paused:
                    self.stop()  # a new selection replaces the paused read
                p.show(text, x, y, editable) if self.writer_ready else p.show(text, x, y)
                self._prepare_selection()
                self.refresh_statuses()  # a sign-in may have changed since the last check
        elif kind == "press":
            self._invalidate_dictation_target()
            if p.contains(*args):
                pass  # clicks on the popup itself (including opening the menu)
            elif p.menu_open:
                p.menu_open = False  # this click picked a language or dismissed the menu
                p.schedule_hide()
            elif p.visible and not p.playing:
                p.hide()
        elif kind == "audio":
            if args[0] == self._read_id and args[1] == self._unit_index and self._read_options:
                p.set_loading(False)
                if not self._continuous or self.chain.active_name != "Codex":
                    unit = self._units[self._unit_index]  # Codex captions follow its transcript
                    self._step_captions(*args[:2], list(sentences.split(unit)) or [unit])
                if self._unit_index + 1 < len(self._units):
                    self.chain.prepare_next(self._units[self._unit_index + 1], self._read_options[0],
                                            fallback_tag=self._read_options[3])
        elif kind == "progress":
            self._follow_progress(*args)
        elif kind == "hotkey":
            self._hotkey()
        elif kind == "hotkey_read":
            self._hotkey_shown(*args)
        elif kind == "caption":
            self.host.after(int(CAPTION_DELAY_S * 1000), lambda: self._caption(*args))
        elif kind == "sentence_done":
            err, rid, index = args
            if rid != self._read_id or index != self._unit_index or not self._read_options:
                return
            if err or index + 1 == len(self._units):
                self._unit_index += 1
                self._read_done(err)
            else:
                self.chain.only = self.chain.last  # preserve the first successful voice across the selection
                self._unit_index += 1
                if p.paused:
                    self._pending_next = True
                else:
                    self._start_unit()
        elif kind == "done":
            if args[1] != self._read_id:
                return
            self.stop()
            if args[0]:
                self.host.notify(f"Could not read aloud: {args[0]}")
        elif kind == "settings":
            if self.settings_win and self.settings_win.alive:
                self.settings_win.focus()
            else:
                self.settings_win = self.host.open_settings(welcome=bool(args))  # ("settings", True) on first run
            self.refresh_statuses()
            if self.writer_ready:
                self.refresh_writer()
        elif kind == "engine":
            if self.chain.busy:  # an attempt that started before a stop is not shown
                p.set_source(args[0])
        elif kind == "summary":
            self._summary_done(*args)
        elif kind in self.WRITER_EVENTS:
            getattr(self, self.WRITER_EVENTS[kind])(*args)
        elif kind == "call":
            args[0]()
        elif kind == "summarize_progress":
            if self._summarize_progress is not None:  # a late event after the download ended is dropped
                self._set_summarize_progress(args[0])
        elif kind == "summarize_done":
            self._summarize_downloaded(args[0])
        elif kind == "statuses":
            self._poll_while_downloading()
            self._show_source()
            if self.settings_win and self.settings_win.alive:
                self.settings_win.refresh_sources()
        elif kind == "quit":
            self._closing = True
            self.stop()
            self._close_summaries()
            self._close_writer()
            self._run_stt(False)
            self.host.stop_trigger()
            self.host.set_hotkey("", None)
            self.chain.close()
            self.host.quit()
