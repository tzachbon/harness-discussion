"""Controlled microphone streams and shared dictation lifecycle fixtures."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness_discussion import app_writer, core, mic
from harness_discussion.edit_logic import Context
from tests.test_app_writer import _App

RECORDER = mic.Recorder


class Devices:
    def __init__(self, blocked=None):
        self.blocked = blocked
        self.entered, self.release = threading.Event(), threading.Event()
        self.calls, self.streams = [], []
        self.active = self.maximum = 0
        self.audio_before_start = False

    def factory(self, callback):
        self.active += 1
        self.maximum = max(self.active, self.maximum)
        owner = self
        class Stream:
            def step(self, operation):
                owner.calls.append((operation, threading.get_ident()))
                if operation == owner.blocked:
                    owner.entered.set()
                    if not owner.release.wait(5):
                        raise AssertionError("test did not release the device barrier")

            def start(self):
                if owner.audio_before_start:
                    callback(b"\x01\x00" * mic.RATE, mic.RATE, None, None)
                self.step("start")
                if not owner.audio_before_start:
                    callback(b"\x01\x00" * mic.RATE, mic.RATE, None, None)

            def stop(self):
                self.step("stop")

            def close(self):
                self.step("close")
                owner.active -= 1
        stream = Stream()
        self.streams.append(stream)
        return stream


class DictationCase(unittest.TestCase):
    def make_app(self, blocked=None):
        app = _App()
        app.stt_sources = [SimpleNamespace(name="Codex", status=Mock(return_value="ready"),
                                           transcribe=Mock(return_value="words"))]
        app.host.edit.type_at_caret = Mock(return_value=True)
        app.host.dictation_target = lambda: ((app.host.edit.ctx.app, app.host.edit.ctx.window)
                                             if app.host.edit.ctx is not None else None)
        devices = Devices(blocked)
        patcher = patch.object(app_writer.mic, "Recorder", lambda device="": RECORDER(devices.factory, device=device))
        patcher.start()
        timers = []
        app.host.after = lambda ms, fn: timers.append((ms, fn))
        app.deadlines = timers
        self.addCleanup(patcher.stop)
        self.addCleanup(app._close_writer)
        self.addCleanup(devices.release.set)
        return app, devices

    def fire(self, app, seconds):
        callback = next(fn for ms, fn in app.deadlines if ms == seconds * 1000)
        callback()
        app.drain("dictation_timer")

    def start(self, app):
        app._dictate_down()
        app.drain("recording_ready")

    def finish(self, app):
        app._dictate_up()
        app.drain("recording_done")
        app.drain("dictated")
