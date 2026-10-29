import base64
import http.server
import io
import json
import logging
import sqlite3
import struct
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from harness_discussion import cursor_auth, cursor_tts
from harness_discussion.audio import NoAudioError
from harness_discussion.cursor_tts import CursorEngine
from harness_discussion.engines import Chain
from test_edge import FakePlayer, decode_whole, speak, tone_mp3

SECRET = "SECRET-SIGNATURE-0123456789"


def jwt(exp):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJIUzI1NiJ9.{payload}.{SECRET}"


class TempDb(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "state.vscdb"
        patcher = mock.patch.object(cursor_auth, "DB", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def store(self, token=None):
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE IF NOT EXISTS ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
        if token is not None:
            con.execute("INSERT INTO ItemTable VALUES ('cursorAuth/accessToken', ?)", (f'"{token}"',))
        con.commit()
        con.close()


class Status(TempDb):
    def test_no_database_is_missing(self):
        self.assertEqual(cursor_auth.status(), "missing")

    def test_no_token_is_signed_out(self):
        self.store()
        self.assertEqual(cursor_auth.status(), "signed_out")

    def test_a_token_expiring_within_a_minute_is_expired(self):
        self.store(jwt(time.time() + 30))
        self.assertEqual(cursor_auth.status(), "expired")
        with self.assertRaises(NoAudioError):
            cursor_auth.token()

    def test_an_undecodable_token_is_expired(self):
        self.store("not-a-jwt")
        self.assertEqual(cursor_auth.status(), "expired")

    def test_a_valid_token_is_ready_and_read_without_quotes(self):
        token = jwt(time.time() + 3600)
        self.store(token)
        self.assertEqual((cursor_auth.status(), cursor_auth.token()), ("ready", token))

    def test_a_bytes_token_is_read(self):
        token = jwt(time.time() + 3600)
        self.store()
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO ItemTable VALUES ('cursorAuth/accessToken', ?)", (token.encode(),))
        con.commit()
        con.close()
        self.assertEqual((cursor_auth.status(), cursor_auth.token()), ("ready", token))

    def test_an_unset_appdata_is_missing(self):
        with mock.patch.object(cursor_auth, "DB", Path("Cursor/User/globalStorage/state.vscdb")):
            self.assertEqual(cursor_auth.status(), "missing")

    def test_the_database_is_never_changed(self):
        self.store(jwt(time.time() + 3600))
        before = self.db.read_bytes()
        cursor_auth.token()
        self.assertEqual(self.db.read_bytes(), before)


class Encoding(unittest.TestCase):
    def test_long_and_hebrew_text_round_trip(self):
        for text in ("x" * 300, "שלום, זה משפט בעברית שנקרא בקול על ידי Cursor. " * 3):
            fields = cursor_tts.decode(cursor_tts.encode(text, "ara", 1.25))
            self.assertEqual(fields[1].decode(), text)
            self.assertEqual(fields[2], b"ara")
            self.assertEqual(struct.unpack("<f", fields[4])[0], 1.25)

    def test_a_full_response_decodes(self):
        fields = cursor_tts.decode(response(b"mp3"))
        self.assertEqual((fields[1], fields[2], fields[3]), (b"mp3", b"audio/mpeg", 2**40 + 7))
        self.assertEqual(fields[4], fields[5])

    def test_a_truncated_response_is_an_error(self):
        whole = response(b"mp3" * 100)
        for cut in (1, 2, 50, len(whole) - 1):
            with self.assertRaises(ValueError):
                cursor_tts.decode(whole[:cut])
        with self.assertRaises(ValueError):
            cursor_tts.decode(bytes([1 << 3 | 1]) + b"1234")  # fixed64 with 4 bytes

    def test_the_length_is_a_multi_byte_varint(self):
        body = cursor_tts.encode("x" * 300, "ara", 1.0)
        self.assertEqual(body[:3], bytes([0x0A, 0xAC, 0x02]))  # field 1, length 300


def response(audio, mime=b"audio/mpeg"):
    """A TextToSpeechResponse shaped like Cursor's: 3 synthesis_time_ms (int64), 4/5 packed int32 timings."""
    packed = b"".join(map(cursor_tts._varint, (0, 150, 70000)))
    return (cursor_tts._ld(1, audio) + cursor_tts._ld(2, mime) + bytes([3 << 3]) + cursor_tts._varint(2**40 + 7)
            + cursor_tts._ld(4, packed) + cursor_tts._ld(5, packed))


def reply(audio, mime=b"audio/mpeg", body=None):
    resp = mock.MagicMock()
    resp.__enter__.return_value = io.BytesIO(response(audio, mime) if body is None else body)
    return resp


class Synthesize(TempDb):
    def setUp(self):
        super().setUp()
        self.token = jwt(time.time() + 3600)
        self.store(self.token)

    def fails(self, **urlopen):
        with mock.patch.object(cursor_tts, "_open", **urlopen) as call:
            with self.assertRaises(NoAudioError) as ctx:
                cursor_tts.synthesize("hello", "ara", 1.0)
        self.assertNotIn(SECRET, str(ctx.exception))
        return call

    def test_http_401_timeout_empty_audio_and_wrong_mime_have_no_audio(self):
        self.fails(side_effect=urllib.error.HTTPError(cursor_tts.URL, 401, "Unauthorized", {}, None))
        self.fails(side_effect=TimeoutError())
        self.fails(side_effect=urllib.error.URLError("offline"))
        self.fails(return_value=reply(b""))
        self.fails(return_value=reply(b"\xff\xfb", b"audio/wav"))
        self.fails(return_value=reply(b"", body=response(b"\xff\xfb" * 50)[:40]))

    def test_a_redirect_is_refused_and_the_sign_in_is_sent_once(self):
        seen = []

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                seen.append((self.path, self.headers.get("Authorization")))
                self.send_response(302 if self.path == "/tts" else 200)
                self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST

            def log_message(self, *a):
                pass
        server = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with mock.patch.object(cursor_tts, "URL", f"http://127.0.0.1:{server.server_port}/tts"):
            with self.assertRaisesRegex(NoAudioError, "HTTP 302"):
                cursor_tts.synthesize("hello", "ara", 1.0)
        self.assertEqual(seen, [("/tts", f"Bearer {self.token}")])

    def test_an_expired_sign_in_sends_nothing(self):
        self.store(jwt(time.time() - 10))
        self.fails(return_value=reply(b"\xff\xfb")).assert_not_called()

    def test_the_request_carries_the_sign_in_only_in_its_header(self):
        with mock.patch.object(cursor_tts, "_open", return_value=reply(b"mp3")) as call:
            self.assertEqual(cursor_tts.synthesize("hello", "luna", 1.5), b"mp3")
        req, kw = call.call_args[0][0], call.call_args[1]
        self.assertEqual(req.get_header("Authorization"), f"Bearer {self.token}")
        self.assertNotIn(SECRET.encode(), req.data)
        self.assertEqual((req.full_url, kw["timeout"]), (cursor_tts.URL, 30))


class Engine(TempDb):
    def setUp(self):
        super().setUp()
        self.store(jwt(time.time() + 3600))
        FakePlayer.opened = []

    def test_a_cursor_mp3_plays_through_the_player(self):
        data = tone_mp3()
        e = CursorEngine()
        e.Player = FakePlayer
        with mock.patch.object(cursor_tts, "_open", return_value=reply(data)) as call:
            done, result, heard = speak(e)
            self.assertTrue(done.wait(5))
        self.assertEqual((result, len(heard)), ([None], 1))
        self.assertEqual(sum(map(len, FakePlayer.opened[0].writes)), decode_whole(data) * 2)
        self.assertEqual(cursor_tts.decode(call.call_args[0][0].data)[2], b"ara")

    def test_long_reads_fit_the_service_limit_and_play_every_piece(self):
        for text in ("word " * 300 + "x", "שלום " * 400, "x" * 3001, "😀" * 751,
                     "Read this. " * 90 + "😀" * 300):
            for prepared in (False, True):
                with self.subTest(text_length=len(text), prepared=prepared):
                    sent, data = [], tone_mp3(0.1)

                    def answer(req, timeout):
                        fields = cursor_tts.decode(req.data)
                        piece = fields[1].decode()
                        sent.append(piece)
                        if len(piece.encode("utf-16-le")) // 2 > 1500:
                            raise urllib.error.HTTPError(cursor_tts.URL, 400, "too long", {}, None)
                        self.assertEqual(fields[2], b"luna")
                        self.assertEqual(struct.unpack("<f", fields[4])[0], 1.25)
                        return reply(data)

                    e = CursorEngine()
                    e.Player, e.voice, e.speed = FakePlayer, "luna", 1.25
                    FakePlayer.opened = []
                    with mock.patch.object(cursor_tts, "_open", side_effect=answer):
                        if prepared:
                            e.prepare("en-US", text)
                        done, result, heard = speak(e, text)
                        self.assertTrue(done.wait(5))
                    self.assertEqual((result, len(heard)), ([None], 1))
                    self.assertTrue(all(len(p.encode("utf-16-le")) // 2 <= 1500 for p in sent))
                    self.assertEqual("".join("".join(sent).split()), "".join(text.split()))
                    self.assertEqual(sum(map(len, FakePlayer.opened[0].writes)),
                                     decode_whole(data) * 2 * len(sent))

    def test_a_later_piece_failure_does_not_restart_heard_text_on_another_engine(self):
        e, fallback, done, result, heard = CursorEngine(), mock.Mock(), threading.Event(), [], []
        e.Player = FakePlayer
        fallback.name, fallback.speed = "Next", 1.0
        fallback.min_speed, fallback.max_speed = 0, 2.0
        fallback.speak.side_effect = lambda text, tag, finish, on_audio: finish(None)
        chain = Chain([e, fallback])
        calls = 0

        def answer(req, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                return reply(tone_mp3(0.1))
            raise urllib.error.HTTPError(cursor_tts.URL, 503, "unavailable", {}, None)

        with mock.patch.object(cursor_tts, "_open", side_effect=answer):
            chain.speak("word " * 400, "en-US", lambda err: (result.append(err), done.set()),
                        lambda: heard.append(1))
            self.assertTrue(done.wait(5))
        self.assertEqual((calls, heard), (2, [1]))
        self.assertIsInstance(result[0], RuntimeError)
        self.assertNotIsInstance(result[0], NoAudioError)
        fallback.speak.assert_not_called()

    def test_prepare_sends_only_with_text_and_play_joins_it(self):
        e = CursorEngine()
        e.Player = FakePlayer
        with mock.patch.object(cursor_tts, "_open", return_value=reply(tone_mp3())) as call:
            e.prepare("en-US")
            self.assertFalse(call.called)
            e.prepare("en-US", "hello")
            done, _, _ = speak(e)
            self.assertTrue(done.wait(5))
        self.assertEqual(call.call_count, 1)

    def test_the_next_piece_is_fetched_while_one_plays_and_then_joined(self):
        e = CursorEngine()
        e.Player = FakePlayer
        chain, done = Chain([e]), threading.Event()
        sent = []
        def answer(req, timeout):
            sent.append(cursor_tts.decode(req.data)[1].decode())
            return reply(tone_mp3())
        with mock.patch.object(cursor_tts, "_open", side_effect=answer):
            chain.speak("One.", "en-US", lambda err: done.set(),
                        lambda: chain.prepare_next("Two. Three.", "en-US"))
            self.assertTrue(done.wait(5))
            for _ in range(200):
                if len(sent) == 2:
                    break
                time.sleep(0.01)
            self.assertEqual(sent, ["One.", "Two. Three."])  # started once "One." was heard
            done.clear()
            chain.speak("Two. Three.", "en-US", lambda err: done.set())
            self.assertTrue(done.wait(5))
        self.assertEqual(sent, ["One.", "Two. Three."])  # the read joined the fetch, no second request

    def test_a_failed_read_moves_to_the_next_engine_without_leaking_the_token(self):
        class Next:
            name, speed, paused = "Next", 1.0, threading.Event()
            stop = pause = resume = close = lambda self: None

            def speak(self, text, tag, on_done, on_audio):
                on_done(None)
        cursor, done = CursorEngine(), threading.Event()
        logs = io.StringIO()
        handler = logging.StreamHandler(logs)
        root, level = logging.getLogger(), logging.getLogger().level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.setLevel, level)
        self.addCleanup(root.removeHandler, handler)
        chain = Chain([cursor, Next()])
        with mock.patch.object(cursor_tts, "_open",
                               side_effect=urllib.error.HTTPError(cursor_tts.URL, 401, "Unauthorized", {}, None)):
            chain.speak("hello", "en-US", lambda err: done.set())
            self.assertTrue(done.wait(5))
        self.assertEqual(chain.last, "Next")
        self.assertIn("Cursor had no audio", logs.getvalue())
        self.assertNotIn(SECRET, logs.getvalue())

    def test_status_and_voices(self):
        self.assertEqual(CursorEngine().status(), "ready")
        self.assertEqual(len(CursorEngine.voices), 28)
        self.assertIn(("ara", "Ara"), CursorEngine.voices)


if __name__ == "__main__":
    unittest.main()
