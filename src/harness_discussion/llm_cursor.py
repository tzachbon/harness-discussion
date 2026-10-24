"""Cursor for the writer: rewrites through a warm `cursor-agent acp` process (Agent Client Protocol, JSON-RPC over
stdio) and transcripts through AiService/TranscribeAudio with the Cursor desktop sign-in."""
import http.client
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

from . import cursor_auth
from .codex_rt import SPAWN, extended_path
from .cursor_tts import _ld, _open, decode
from .writer import Cancelled

IDLE_S = 300
TIMEOUT_S = 60
READY_TTL_S = 60  # a confirmed sign-in is trusted this long; other answers are never cached
SPARE_TTL_S = 60  # a pre-warmed session older than this is not used
PREWARM_WAIT_S = 3  # how long a rewrite waits for an unfinished prewarm before setting up its own session
log = logging.getLogger("harness_discussion")
_ready_at = float("-inf")
STT_URL = "https://api2.cursor.sh/aiserver.v1.AiService/TranscribeAudio"


def agent_exe():
    for name in ("cursor-agent", "agent"):
        found = shutil.which(name, path=extended_path())
        if found:
            return found
    return None


def login_status():
    """"ready", "missing", or "signed_out", from `cursor-agent status`."""
    global _ready_at
    if time.monotonic() - _ready_at < READY_TTL_S:
        return "ready"
    exe = agent_exe()
    if not exe:
        return "missing"
    try:
        done = subprocess.run([exe, "status"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                              stdin=subprocess.DEVNULL, env=_env(), **SPAWN)
    except (OSError, subprocess.SubprocessError):
        return "ready"  # unknown: the rewrite tries it, and the next source runs if it fails
    out = done.stdout.lower()
    if done.returncode == 0 and "logged in" in out and "not logged in" not in out:
        _ready_at = time.monotonic()
        return "ready"
    return "signed_out"


def _env():
    return {**os.environ, "PATH": extended_path()} if sys.platform == "darwin" else None


class Acp:
    """One `cursor-agent acp` process. Every request from the agent (permissions, file reads) is refused."""

    def __init__(self, exe):
        self.cwd = tempfile.mkdtemp(prefix="harness-writer-")  # empty, so the agent has nothing to read
        self.p = subprocess.Popen([exe, "acp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, encoding="utf-8", bufsize=1, cwd=self.cwd,
                                  env=_env(), **SPAWN)
        self.updates, self._pending, self._next, self._lock = queue.Queue(), {}, 0, threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}}, 30)
        except BaseException:
            self.close()
            raise

    @property
    def alive(self):
        return self.p.poll() is None

    def _send(self, msg):
        with self._lock:
            self.p.stdin.write(json.dumps({"jsonrpc": "2.0", **msg}) + "\n")
            self.p.stdin.flush()

    def _read(self):
        for line in self.p.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg and "method" in msg:
                self._send({"id": msg["id"], "error": {"code": -32601, "message": "unsupported"}})
            elif msg.get("id") in self._pending:
                self._pending.pop(msg["id"]).put(msg)
            else:
                self.updates.put(msg)
        for box in list(self._pending.values()):
            box.put({"error": "cursor-agent exited"})

    def call(self, method, params, timeout=20):
        with self._lock:
            self._next += 1
            n = self._next
            box = self._pending[n] = queue.Queue()
        self._send({"id": n, "method": method, "params": params})
        msg = box.get(timeout=timeout)
        if "error" in msg:
            raise RuntimeError(f"{method} failed")  # the error can quote the prompt
        return msg["result"]

    def notify(self, method, params):
        self._send({"method": method, "params": params})

    def close(self):
        if self.alive:
            self.p.terminate()
            try:
                self.p.wait(5)
            except subprocess.TimeoutExpired:
                self.p.kill()
        shutil.rmtree(self.cwd, ignore_errors=True)


class CursorWriter:
    name = "Cursor"

    def __init__(self, start=None):
        self._start = start or (lambda: Acp(agent_exe()))
        self._acp, self._models, self._used, self._lock = None, None, 0.0, threading.Lock()
        self._timer, self._one = None, threading.Lock()  # one rewrite reads the shared update queue at a time
        self._spare = None  # (acp, session id, model, created): a fresh session for the next rewrite
        self._prewarmed = threading.Event()  # cleared while a prewarm sets up the spare
        self._prewarmed.set()

    def status(self):
        return login_status()

    def _warm(self):
        with self._lock:
            if self._acp is None or not self._acp.alive:
                self._acp = self._start()
            self._used = time.monotonic()
            return self._acp

    def _arm_idle_stop(self):
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(IDLE_S, self._idle_stop)
        self._timer.daemon = True
        self._timer.start()

    def _idle_stop(self):
        with self._lock:
            if self._acp and time.monotonic() - self._used >= IDLE_S - 1:
                self._acp.close()
                self._acp, self._spare = None, None

    def close(self):
        if self._timer:
            self._timer.cancel()
        with self._lock:
            if self._acp:
                self._acp.close()
                self._acp, self._spare = None, None

    def _session(self, acp):
        session = acp.call("session/new", {"cwd": acp.cwd, "mcpServers": []}, 30)
        sid = session["sessionId"]
        models = (session.get("models") or {}).get("availableModels") or []
        self._models = [{"id": m["modelId"], "label": m.get("name") or m["modelId"], "efforts": []}
                        for m in models if m.get("modelId")]
        if any(m.get("id") == "ask" for m in (session.get("modes") or {}).get("availableModes", [])):
            acp.call("session/set_mode", {"sessionId": sid, "modeId": "ask"})
        return sid

    def models(self):
        """[{"id", "label", "efforts"}]. Effort is part of the Cursor model id, for example `...[effort=low]`."""
        if self._models is None:
            acp = self._warm()
            try:
                self._session(acp)
            finally:
                self._arm_idle_stop()
        return self._models or []

    def rewrite(self, system, text, model, effort, cancelled):
        self._prewarmed.wait(PREWARM_WAIT_S)
        with self._one:
            return self._rewrite(system, text, model, cancelled)

    def _fresh(self, spare, acp, model):
        return spare is not None and spare[0] is acp and spare[2] == model and time.monotonic() - spare[3] < SPARE_TTL_S

    def prewarm(self, model):
        """Start the agent and one session with `model` set, so the next rewrite only sends its prompt. Never raises."""
        if not self._prewarmed.is_set():
            return
        self._prewarmed.clear()
        try:  # outside _one: setup reads no updates, and a hung session/new must not hold up the rewrite
            if self.status() != "ready":
                return
            acp = self._warm()
            if self._fresh(self._spare, acp, model):
                return
            sid = self._session(acp)
            if model:
                acp.call("session/set_model", {"sessionId": sid, "modelId": model})
            self._spare = (acp, sid, model, time.monotonic())
        except Exception as e:
            log.info("Cursor prewarm failed: %s", type(e).__name__)
        finally:
            self._prewarmed.set()
            self._arm_idle_stop()

    def _rewrite(self, system, text, model, cancelled):
        acp = self._warm()
        spare, self._spare = self._spare, None  # a session serves one rewrite, so no earlier text leaks into it
        try:
            if self._fresh(spare, acp, model):
                sid = spare[1]
            else:
                sid = self._session(acp)
                if model:
                    acp.call("session/set_model", {"sessionId": sid, "modelId": model})
            while not acp.updates.empty():
                acp.updates.get_nowait()
            result, done = {}, threading.Event()

            def prompt():
                try:
                    result["ok"] = acp.call("session/prompt", {"sessionId": sid, "prompt": [
                        {"type": "text", "text": f"{system}\n\n{text}"}]}, TIMEOUT_S)
                except Exception as e:
                    result["error"] = e
                done.set()
            threading.Thread(target=prompt, daemon=True).start()
            out, deadline = [], time.monotonic() + TIMEOUT_S
            while not done.is_set() or not acp.updates.empty():
                if cancelled():
                    acp.notify("session/cancel", {"sessionId": sid})
                    raise Cancelled("The rewrite was cancelled.")
                if time.monotonic() > deadline:
                    acp.notify("session/cancel", {"sessionId": sid})  # so the warm agent stops working on it
                    raise TimeoutError("Cursor took too long.")
                try:
                    msg = acp.updates.get(timeout=0.1)
                except queue.Empty:
                    continue
                params = msg.get("params") or {}
                update = params.get("update") or {}
                if params.get("sessionId") == sid and update.get("sessionUpdate") == "agent_message_chunk":
                    out.append((update.get("content") or {}).get("text", ""))
            if "error" in result:
                raise result["error"]
            stop = (result.get("ok") or {}).get("stopReason")
            if stop != "end_turn":  # max_tokens or refusal: a partial rewrite must never replace the selection
                raise RuntimeError(f"Cursor stopped early: {stop}")
            return "".join(out)
        finally:
            self._used = time.monotonic()
            self._arm_idle_stop()


def encode_stt(audio, mime="audio/wav", language=""):
    """TranscribeAudioRequest: 1 audio, 2 mime_type, 3 language (optional)."""
    return _ld(1, audio) + _ld(2, mime.encode()) + (_ld(3, language.encode()) if language else b"")


class SttError(RuntimeError):
    pass


class CursorStt:
    name = "Cursor"

    def status(self):
        return cursor_auth.status()

    def transcribe(self, audio, language=""):
        try:
            req = urllib.request.Request(STT_URL, data=encode_stt(audio, language=language), headers={
                "Content-Type": "application/proto", "Connect-Protocol-Version": "1",
                "Authorization": f"Bearer {cursor_auth.token()}"})
            with _open(req, timeout=60) as resp:
                fields = decode(resp.read())
        except urllib.error.HTTPError as e:
            raise SttError(f"HTTP {e.code}") from e
        except (OSError, http.client.HTTPException, ValueError) as e:
            raise SttError(type(e).__name__) from e  # the name only: messages never carry request data
        except Exception as e:  # NoAudioError from token() when signed out
            raise SttError(type(e).__name__) from e
        text = fields.get(1, b"")
        return text.decode("utf-8", "replace").strip() if isinstance(text, bytes) else ""
