"""The Cursor desktop sign-in, read from Cursor's own state database. Never writes it and never refreshes the token."""
import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .audio import NoAudioError

_STATE = Path("Cursor", "User", "globalStorage", "state.vscdb")
DB = (Path(os.environ.get("APPDATA", "")) if sys.platform == "win32"
      else Path.home() / "Library" / "Application Support") / _STATE
_ENCRYPTED = "cursor-safe-storage:v1:"  # Cursor 3.27+ wraps the token with Electron safeStorage
_key = None


def _decrypt(value):
    """Chromium's macOS v10 format: AES-128-CBC keyed from the "Cursor Safe Storage" Keychain item. "" on failure."""
    global _key
    try:
        blob = base64.b64decode(value[len(_ENCRYPTED):])
        if blob[:3] != b"v10":
            return ""
        if _key is None:  # one Keychain read per run; macOS asks once to allow it
            password = subprocess.run(["security", "find-generic-password", "-w", "-s", "Cursor Safe Storage"],
                                      capture_output=True, text=True, timeout=60, check=True).stdout.strip()
            _key = hashlib.pbkdf2_hmac("sha1", password.encode(), b"saltysalt", 1003, 16)
        dec = Cipher(algorithms.AES(_key), modes.CBC(b" " * 16)).decryptor()
        unpad = padding.PKCS7(128).unpadder()
        return (unpad.update(dec.update(blob[3:]) + dec.finalize()) + unpad.finalize()).decode()
    except (OSError, subprocess.SubprocessError, ValueError):
        _key = None  # Cursor may have reset its key; read it again next time
        return ""


def _expires(token):
    try:
        payload = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return 0  # undecodable counts as expired


def _check():
    try:
        con = sqlite3.connect(f"{DB.as_uri()}?mode=ro", uri=True)  # read-only: Cursor may hold it open
        try:
            row = con.execute("SELECT value FROM ItemTable WHERE key = 'cursorAuth/accessToken'").fetchone()
        finally:
            con.close()
    except (sqlite3.Error, ValueError):  # ValueError: a relative path when %APPDATA% is unset
        return "missing", None
    value = row[0] if row else None
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    token = value.strip('"') if isinstance(value, str) else ""
    # shortcut: Windows wraps it with DPAPI and AES-GCM, which is not decrypted yet, so it still reads as expired.
    if token.startswith(_ENCRYPTED) and sys.platform == "darwin":
        token = _decrypt(token)
    if not token:
        return "signed_out", None
    return ("ready" if _expires(token) > time.time() + 60 else "expired"), token


def status():
    """"ready", "missing" (no Cursor database), "signed_out" or "expired"."""
    return _check()[0]


def token():
    state, tok = _check()
    if state != "ready":
        raise NoAudioError(f"{state}")
    return tok
