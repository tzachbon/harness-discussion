"""App's Summarize button: downloads the local model on first use, then summarizes and reads the summary."""
import logging
import threading

from . import lang, summarize

log = logging.getLogger("harness_discussion")


class Summaries:
    def _init_summaries(self):
        self.summary_server = summarize.Server()
        self._summarize_status = summarize.status()
        self._summarize_progress = None  # 0..1 while the model downloads
        self._summarize_cancel = threading.Event()
        self._summary_gen = 0  # the idle stop only applies to the latest summary

    def summarize_state(self):
        """(status, progress): "ready", "missing" or "unsupported", and 0..1 or None."""
        return self._summarize_status, self._summarize_progress

    def summarize(self, text, lang_tag):
        """The popup's ✦ button."""
        if self._summarize_progress is not None:
            return
        if self._summarize_status == "unsupported":
            self.host.notify("Summarize isn't available on this computer.")
            return
        if self._summarize_status != "ready":
            if self.host.confirm("Download the Summarize model?",
                                 f"Summarize runs {summarize.MODEL_NAME} on this computer. It downloads once "
                                 f"from GitHub and Hugging Face: {summarize.download_size_label()}."):
                self._download_summarize()
            return
        self.stop()  # a read in progress is replaced by the summary
        rid = self._read_id
        self._summary_gen += 1  # older idle timers must not stop the server under this summary
        gen = self._summary_gen
        self.popup.set_summary()
        tag = lang_tag or lang.detect(text)
        language = lang.name(tag) or "the language of the text"

        def work():
            try:
                result = self.summary_server.summarize(text, language)
            except Exception as e:
                result = e
            self.events.put(("summary", rid, gen, result, lang_tag))
        threading.Thread(target=work, daemon=True).start()

    def summarize_action(self):
        """Settings' button: Download, Cancel or Remove, by state."""
        if self._summarize_progress is not None:
            self._summarize_cancel.set()
        elif self._summarize_status == "ready":
            self.summary_server.stop()
            summarize.remove()
            self._summarize_status = summarize.status()
            self._refresh_summarize()
        elif self._summarize_status == "missing":
            self._download_summarize()

    def _download_summarize(self):
        self._summarize_cancel.clear()
        self._set_summarize_progress(0.0)
        shown = [0]

        def progress(done, total):
            percent = done * 100 // total
            if percent != shown[0]:  # an event per percent, not per megabyte
                shown[0] = percent
                self.events.put(("summarize_progress", done / total))

        def work():
            try:
                summarize.download(progress, self._summarize_cancel.is_set)
                err = None
            except Exception as e:
                err = e
            self.events.put(("summarize_done", err))
        threading.Thread(target=work, daemon=True).start()

    def _set_summarize_progress(self, fraction):
        self._summarize_progress = fraction
        self.popup.set_summarize_progress(fraction)
        self._refresh_summarize()

    def _refresh_summarize(self):
        if self.settings_win and self.settings_win.alive:
            self.settings_win.refresh_summarize()

    def _arm_summary_stop(self):
        """Stops the local model after IDLE_S, unless a newer summary or rewrite bumps `_summary_gen` first.
        Main thread only. A local rewrite bumps `_summary_gen` when it starts and calls this when it ends."""
        gen = self._summary_gen
        self.host.after(summarize.IDLE_S * 1000, lambda: gen == self._summary_gen and threading.Thread(
            target=self.summary_server.stop, daemon=True).start())

    def _summary_done(self, rid, gen, result, lang_tag):
        if gen == self._summary_gen:  # the latest summary, even if its read was stopped
            self._arm_summary_stop()
        if rid != self._read_id:
            return  # stopped, or replaced by another read, while the model worked
        if isinstance(result, Exception):
            self.stop()
            log.info("summary failed: %s", type(result).__name__)
            self.host.notify(f"Could not summarize. {self._summary_error(result)}")
            return
        paused = self.popup.paused
        self.play(result, lang_tag)
        if paused:  # paused while the model worked: the summary waits for Play
            self.chain.pause()

    def _summarize_downloaded(self, err):
        self._summarize_status = summarize.status()
        self._set_summarize_progress(None)
        if err and not isinstance(err, summarize.Cancelled):
            log.info("summarize download failed: %s", type(err).__name__)
            reason = ("Check your connection and try again." if isinstance(err, OSError)
                      else self._summary_error(err))
            self.host.notify(f"Could not download the Summarize model. {reason}")

    @staticmethod
    def _summary_error(err):
        """Our own messages, or the type name: other messages could echo the text."""
        return str(err) if isinstance(err, summarize.Error) else type(err).__name__

    def _close_summaries(self):
        self._summarize_cancel.set()
        self.summary_server.close()
