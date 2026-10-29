"""Cursor's speech service, reached with the Cursor desktop sign-in."""
import asyncio
import http.client
import struct
import urllib.error
import urllib.request

from . import cursor_auth, sentences
from .audio import NoAudioError
from .engines import EdgeEngine

URL = "https://api2.cursor.sh/aiserver.v1.AiService/TextToSpeech"
MAX_UNITS = 1500  # Cursor rejects requests longer than 1500 UTF-16 code units with HTTP 400.
VOICES = tuple((v, v.capitalize()) for v in (
    "altair ara atlas aurora carina castor celeste cosmo eve helios helix iris kepler leo "
    "liora lumen luna lux naksh orion perseus rex rigel sal sirius ursa zagan zenith").split())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None  # a redirect would carry the sign-in to another URL; it surfaces as an HTTP 30x error


_open = urllib.request.build_opener(_NoRedirect()).open


def _varint(n):
    out = bytearray()
    while n > 0x7F:
        out.append(n & 0x7F | 0x80)
        n >>= 7
    return bytes(out + bytes([n]))


def _ld(field, data):
    return _varint(field << 3 | 2) + _varint(len(data)) + data


def encode(text, voice, speed):
    """TextToSpeechRequest: 1 text, 2 voice_id, 4 speed (float). Language is left out: Cursor detects it."""
    return _ld(1, text.encode()) + _ld(2, voice.encode()) + bytes([4 << 3 | 5]) + struct.pack("<f", speed)


def _read_varint(data, i):
    value = shift = 0
    while True:
        if i >= len(data):
            raise ValueError("truncated")
        byte = data[i]
        i += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, i


def decode(data):
    """Field number -> first value: an int for varints, raw bytes for every other wire type."""
    fields, i = {}, 0
    while i < len(data):
        key, i = _read_varint(data, i)
        wire = key & 7
        if wire == 0:
            value, i = _read_varint(data, i)
        else:
            if wire == 2:
                size, i = _read_varint(data, i)
            else:
                size = {1: 8, 5: 4}.get(wire)
                if size is None:
                    raise ValueError(f"wire type {wire}")
            if i + size > len(data):
                raise ValueError("truncated")
            value, i = data[i:i + size], i + size
        fields.setdefault(key >> 3, value)
    return fields


def synthesize(text, voice, speed):
    """One MP3 for the whole text. Raises NoAudioError for any failure."""
    req = urllib.request.Request(URL, data=encode(text, voice, speed), headers={
        "Content-Type": "application/proto", "Connect-Protocol-Version": "1",
        "Authorization": f"Bearer {cursor_auth.token()}"})
    try:
        with _open(req, timeout=30) as resp:
            fields = decode(resp.read())
    except urllib.error.HTTPError as e:
        raise NoAudioError(f"HTTP {e.code}") from e
    except (OSError, http.client.HTTPException, ValueError) as e:
        raise NoAudioError(f"{type(e).__name__}") from e  # the name only: messages never carry request data
    audio, mime = fields.get(1, b""), fields.get(2, b"")
    if not audio or mime != b"audio/mpeg":
        raise NoAudioError(f"no audio ({mime!r})")
    return audio


class CursorEngine(EdgeEngine):
    name, max_speed, voices, default_voice = "Cursor", 2.0, VOICES, "ara"
    voice = None  # set by core; None reads with Cursor's default

    def _request(self, text, tag):  # tag unused: Cursor detects the language itself
        return text, self.voice or self.default_voice, self.speed

    async def _source(self, text, voice, speed):
        # ponytail: the conservative budget can halve request size; count per piece if latency matters.
        limit = max(MAX_UNITS // 2, MAX_UNITS - sum(ord(c) > 0xFFFF for c in text))
        for i, piece in enumerate(sentences.chunks(text, limit)):
            try:
                audio = await asyncio.to_thread(synthesize, piece, voice, speed)
            except NoAudioError as e:
                if i:
                    raise RuntimeError(f"Cursor: {e}") from e  # earlier pieces may already have been heard
                raise
            yield audio

    def status(self):
        return cursor_auth.status()
