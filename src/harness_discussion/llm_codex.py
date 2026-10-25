"""Codex text turns on the ChatGPT sign-in, for the writer. A warm app-server keeps a fresh thread, without the user's
hooks, MCP servers, plugins, and tools, ready for the next rewrite."""
import json
import logging
import os
import queue
import shutil
import tempfile
import threading
import time
import tomllib

from . import codex_rt
from .writer import Cancelled

SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
          "additionalProperties": False}
MODELS_TTL_S = 600
TIMEOUT_S = 60
IDLE_S = 300
LEAN_FEATURES = ("hooks", "plugins", "apps", "memories", "multi_agent", "goals", "chronicle", "skill_search",
                 "tool_suggest", "shell_tool", "unified_exec", "shell_snapshot", "sleep_tool", "view_image",
                 "image_generation", "browser_use", "computer_use", "workspace_dependencies")
log = logging.getLogger("harness_discussion")


def lean_config():
    """Thread config that leaves out what a rewrite never uses. Loading the user's hooks, MCP servers, and plugins
    took most of a rewrite. Each server is turned off by name, because `mcp_servers: {}` merges."""
    home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    try:
        with open(os.path.join(home, "config.toml"), "rb") as f:
            servers = tomllib.load(f).get("mcp_servers") or {}
    except (OSError, tomllib.TOMLDecodeError):
        servers = {}
    return {"features": dict.fromkeys(LEAN_FEATURES, False), "web_search": "disabled",
            "mcp_servers": {name: {"enabled": False} for name in servers}}


class CodexWriter:
    name = "Codex"

    def __init__(self, server=codex_rt.AppServer):
        self._server = server
        self._models, self._models_at = None, 0.0
        self._srv, self._spare, self._system, self._cwd = None, None, None, None  # spare: (server, thread id, system)
        self._used, self._timer, self._lock, self._closed = 0.0, None, threading.RLock(), False
        self._one = threading.Lock()  # one rewrite reads the server's event queue at a time, a cancelled one included

    def status(self):
        return codex_rt.login_status()

    def models(self):
        """[{"id", "efforts"}] for the visible models, cached for 10 minutes."""
        if self._models is None or time.monotonic() - self._models_at > MODELS_TTL_S:
            srv = self._server()
            try:
                data = srv.call("model/list", {}).get("data", [])
            finally:
                srv.close()
            self._models = [{"id": m["id"], "efforts": [e.get("reasoningEffort")
                                                        for e in m.get("supportedReasoningEfforts", [])]}
                            for m in data if not m.get("hidden")]
            self._models_at = time.monotonic()
        return self._models

    def _take(self, system):
        """(server, thread id) with `system` as the instructions. A thread serves one rewrite, so no earlier text
        leaks into it."""
        with self._lock:
            self._used = time.monotonic()
            spare, self._spare = self._spare, None
            if self._cwd is None:
                self._cwd = tempfile.mkdtemp(prefix="harness-writer-")  # empty, so there is nothing to read
            params = {"ephemeral": True, "baseInstructions": system, "cwd": self._cwd, "config": lean_config()}
            if self._srv is None or not self._srv.alive:
                self._srv = self._server(params)
                return self._srv, self._srv.thread_id
            if spare and spare[0] is self._srv and spare[2] == system:
                return self._srv, spare[1]
            return self._srv, self._srv.call("thread/start", params)["thread"]["id"]

    def prewarm(self, model=None):
        """Starts the server, with a thread for the last rewrite's instructions, so the next rewrite only sends its
        text. Never raises."""
        try:
            with self._lock:
                if self._closed:
                    return
                if self._system is None:
                    if self._srv is None or not self._srv.alive:
                        self._srv = self._server({"ephemeral": True, "config": lean_config()})
                else:
                    self._spare = (*self._take(self._system), self._system)
        except Exception as e:
            log.info("Codex prewarm failed: %s", type(e).__name__)
        self._arm_idle_stop()

    def rewrite(self, system, text, model, effort, cancelled):
        with self._one:
            srv, thread_id = self._take(system)
            self._system = system
            try:
                while not srv.events.empty():
                    srv.events.get_nowait()
                params = {"threadId": thread_id, "input": [{"type": "text", "text": text}],
                          "approvalPolicy": "never", "sandboxPolicy": {"type": "readOnly"}, "outputSchema": SCHEMA}
                params.update({k: v for k, v in (("model", model), ("effort", effort)) if v})
                turn_id = srv.call("turn/start", params)["turn"]["id"]
                return _final_text(srv, thread_id, turn_id, cancelled)
            finally:
                threading.Thread(target=self.prewarm, daemon=True).start()

    def _arm_idle_stop(self):
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(IDLE_S, self._idle_stop)
        self._timer.daemon = True
        self._timer.start()

    def _idle_stop(self):
        with self._lock:
            if time.monotonic() - self._used >= IDLE_S - 1:
                self._stop()

    def _stop(self):
        if self._srv:
            self._srv.close()
        self._srv = self._spare = None
        if self._cwd:
            shutil.rmtree(self._cwd, ignore_errors=True)
            self._cwd = None

    def close(self):
        if self._timer:
            self._timer.cancel()
        with self._lock:
            self._closed = True  # a prewarm queued by a finishing rewrite must not start it again
            self._stop()


def _final_text(srv, thread_id, turn_id, cancelled):
    final, deadline = None, time.monotonic() + TIMEOUT_S
    while True:
        if cancelled() or time.monotonic() > deadline:
            try:
                srv.call("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=5)
            except Exception:
                pass  # the thread is never used again
            if cancelled():
                raise Cancelled("The rewrite was cancelled.")
            raise TimeoutError("Codex took too long.")
        if not srv.alive:
            raise RuntimeError("codex app-server exited")
        try:
            msg = srv.events.get(timeout=0.2)
        except queue.Empty:
            continue
        method, params = msg.get("method"), msg.get("params") or {}
        if params.get("threadId", thread_id) != thread_id:
            continue
        if method == "item/completed" and params.get("item", {}).get("type") == "agentMessage":
            final = params["item"].get("text")
        elif method == "turn/completed":
            if params.get("turn", {}).get("status") != "completed" or not final:
                raise RuntimeError("Codex did not finish the rewrite.")
            return json.loads(final)["text"]
