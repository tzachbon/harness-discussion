# Subscription speech-to-text

The same module serves Python callers and other apps on this Windows PC.
It uses the existing Codex ChatGPT login, with no OpenAI API key. Audio goes
to Codex's remote realtime service. The HTTP listener itself is localhost only.

## Built-in dictation

**Settings → Dictation → Local → Download** installs Whisper Small (486 MB), a
multilingual speech model that runs on this computer. The files are pinned to a
specific revision and checked by size and SHA-256. This download is separate from
the Local writing model. Local is tried first by default. It warms while you hold
the shortcut and stays loaded for later recordings. Inference uses CPU int8 and
does not require a GPU or contact a model service. An optional Windows NVIDIA
build uses GPU int8/float16 instead, and retries locally on CPU if GPU inference fails.

For an NVIDIA Windows source run, use `uv sync --extra cuda` and
`uv run --extra cuda harness-discussion`. Build that Windows installer with
`packaging/build.ps1 -Cuda`. The extra bundles about 1.2 GB of pinned NVIDIA
libraries inside the app, without changing the system driver. The ordinary
build omits them. On this PC's RTX 4080 Super, the 7.8 s synthetic English clip
took 0.2 s warm on GPU. A 57 s repeated clip took about 11 s, versus 43 s on CPU.

**Prefer local tidy for faster results** uses the enabled, downloaded Local
writing model before the other Writing sources. It also warms while you speak.
Turn this off to follow the Writing order for Tidy. The ordinary Rewrite action
always follows Writing order.

Windows x64 local cleanup uses llama.cpp's Vulkan runtime to run Qwen3-4B on the
graphics processor when available, with a CPU backend included. Existing CPU-only
installations need the 32 MB runtime update through Writing → Local → Download.
The already verified 2.5 GB model is reused. Two different synthetic drafts took
0.30 and 0.79 seconds to tidy on this PC after warmup. First-ever shader preparation
was slower, so these are warm inference timings, not first-use or native insertion results.

Enabled cloud sources remain fallbacks. For fully offline dictation, download
both local models and disable Cursor and Codex in both Writing and Dictation.
Otherwise a failed local transcription can send audio to a cloud source, and
cloud Tidy can send the transcript. The HTTP server below continues to use Codex.

On one Windows CPU synthetic English sample, warm Small transcription took about
2.4 seconds, compared with 12.5 seconds for a cached large-v3-turbo model. These
are model timings, not native release-to-insertion measurements. Real microphone,
Hebrew/mixed speech quality and macOS frozen-runtime performance remain to be checked.

Enable **Settings → Writing → Rewrite selected text**. Hold Ctrl+Shift+D
on Windows, or ⌥⌘D on Mac, to record; release to transcribe. The built-in flow
uses the source order under **Settings → Dictation** on both platforms,
independently of the HTTP server. It can tidy the transcript with the selected dictation mode, or insert
the raw transcript when tidying is off. Esc cancels recording or transcription.

The field, document and caret or selection captured during preparation, before
**Listening** appears, become the insertion target. This is not a snapshot of
the exact key-down instant. A programmatic focus change before that capture can
therefore establish a different target within the same window. Mouse clicks or
unrelated keystrokes from the hold's activation until insertion prevent pasting,
while recording continues. If the captured target later changes or cannot be
verified, dictation automatically copies the result to the clipboard. Escape
cancels the session without inserting or copying its result. Preparation, device
cleanup and transcription have deadlines, with a single worker retaining device
ownership until cleanup finishes. Windows exposes
microphone status and a link to Windows microphone privacy settings. Choose an
input under **Settings → Dictation → Input device**, or leave **System default**.
The recording pill shows live input bars while the shortcut is held. Flat bars
mean little or no input, which can reveal a muted or incorrectly routed virtual
microphone. The selected device is saved for this app without changing the system
default. After a device change, dictation can refresh the device list and retry
the selected microphone once when no audio stream is open. It never switches
to another input. If it is disconnected, choose another input in Settings.

The recording and transcription pill has an **×** cancel button. Cancelling
during transcription lets you start another recording immediately and discards
any late result. An in-progress native model call may finish its current segment
in the background. A replacement Codex transcription waits for that transport
to become free; cancelling while queued discards it before starting a request.
Dictation
finishes quietly after requesting insertion. It neither retries nor copies an
unconfirmed paste. With no safe input target it shows **Copied to clipboard**
after copying the result, or reports a clipboard failure with a manual Copy retry.

Windows support is experimental. Native microphone start/Esc cancellation and a
Codex rewrite/paste were checked on Windows 11; real English, Hebrew and mixed
microphone transcription, interactive Review/Undo, and the browser/app matrix
remain pending in [#92](https://github.com/tzachbon/harness-discussion/issues/92).
Unit tests do not establish those native acceptance results.

## Run the server from the tray app

Turn on **Settings → Speech-to-text → Run the speech-to-text server**. The app then
serves on port 18765 whenever it runs, and the URL stays the same after restarts, so
OpenWhispr keeps working. The manual command below is for running without the tray app.

## Run the server manually

From this project's directory:

```powershell
.venv\Scripts\python.exe -m harness_discussion.stt_server
```

The default port is 18765. Binding fails if another service already owns it.
Keep that process running. Ctrl+C stops accepting requests and waits for active
transcription to finish. It does not install a service or start automatically.

Open `%LOCALAPPDATA%\harness-discussion\stt-connection.json` and copy `base_url` into
OpenWhispr's **Speech-to-Text → Self-Hosted → Server URL**. Set **Model** to
`codex-realtime` (or leave it empty). No API key is needed in that panel.

The URL includes a random local access token. It changes each time the server
starts, so recopy it after a restart. Keep the connection file private. The
server removes its connection file on normal shutdown and does not log URLs,
recordings, or transcripts. `--port` and `--connection-file` can override defaults.

## Internal Python use

```python
from pathlib import Path
from harness_discussion.stt import transcribe

# Blocking call: run in a worker thread, not the GUI thread.
text = transcribe(Path("recording.webm").read_bytes())
```

This API accepts caller-supplied audio. The built-in dictation UI captures its
own microphone audio through the separate ordered dictation adapters.

## HTTP contract

- `GET {base_url}/models` lists `codex-realtime`, a local adapter name, not a selectable OpenAI model.
- `POST {base_url}/audio/transcriptions` takes multipart `file`, optional `model`,
  `language`, `prompt`, and `response_format` (`json` or `text`).
- Default response: `{"text": "transcribed words"}`. Other formats and fields
  are rejected. `language` and `prompt` are advisory hints, not verified controls
  over the input-transcription engine.
- WAV, WebM, Ogg, MP3, MP4/M4A, and FLAC are decoded in memory. Maximum encoded
  audio: 25 MiB. Maximum decoded duration: 60 seconds. One transcription at a time.
- Busy requests receive 429. Bad audio/form fields receive 400, oversized HTTP
  uploads 413, backend failures 502, and backend timeouts 504.
- Backend failures add `error.code` (for example `no_transcript`, `connection_failed`,
  `backend_start_failed`, `unknown`) and `error.request_id`. Messages never include
  backend exception text, and only startup failures suggest checking the Codex login.
- Each transcription call (including invalid-input and busy rejections) appends one metadata line to `%LOCALAPPDATA%\harness-discussion\stt-diagnostics.log`
  (rotated at 1 MiB): request ID, outcome code, failing stage, stage timings, event-type
  counts, and the audio duration Codex reported receiving. No audio, text, or URLs.
  `no_transcript` with a low `server_audio_ms` means Codex did not ingest the audio.
- Native requests and Electron's `Origin: null` are accepted only with the private
  URL and a local Host header. Arbitrary browser origins are rejected.

This matches [OpenWhispr's documented self-hosted request format](https://github.com/OpenWhispr/openwhispr/blob/main/examples/custom-asr-shim/README.md).
An actual OpenWhispr dictation session is a separate acceptance check.

## Limits and verification

This is experimental Codex WebRTC v3, tested with Codex 0.160.1.

Audio is sent 4 times faster than real time. Codex then keeps transcribing for a
while (about 10 s after sending a 23 s clip). English and Hebrew synthetic clips
stayed at 0% word errors and finished about 9 s sooner than a 1x send.
Mixed Hebrew/English accuracy varied widely at both speeds, so whether 4x
changes it is inconclusive.

Codex sends no whole-recording final signal (its user turn often never closes).
Each transcript fragment carries its position in the audio. The adapter returns
once at least 2.5 s have passed since sending finished and no fragment arrived for
2 s plus the audio Codex has not yet transcribed, plus 1 s for clock skew (Codex
positions ran about 0.6 s ahead of the sent audio in testing). That gives a late
word the time it would have had at 1x. The untranscribed allowance is capped at
the time saved by sending ahead (0.75 × clip length), which is also used when a
position is more than 1 s past the end. With no fragments at all it returns
2.5 s + 0.75 × clip length after sending finished. The hard cap is
12 s + 0.75 × clip length. The 2 s and 2.5 s limits are
about twice the worst case measured on 8 synthetic EN/HE/mixed runs sent at 1x
(last word 1.2 s after the audio, 1.1 s between words).
A slower Codex reply can still lose final words. Do not use it for long
recordings or as a guaranteed verbatim transcription service.

Codex sometimes does not take in the audio at all (`no_transcript` or
`session_closed` with `server_audio_ms` 0). This was seen at both speeds.

The original synthetic English probe had 0% word errors. Mixed Hebrew/English
had 33%, including unwanted transliteration. Real microphone/noisy speech is
not validated. There is no silent fallback to a different provider.

Run deterministic checks:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p test_stt.py -v
```

Explicit live HTTP check, using the existing synthetic English fixture under
`spikes/out` (consumes subscription usage):

```powershell
$env:STT_LIVE_CHECK = '1'
.venv\Scripts\python.exe -m unittest discover -s tests -p test_stt.py -v
Remove-Item Env:\STT_LIVE_CHECK
```

The live check encodes the fixture as WebM, uploads it over HTTP, and compares
the returned transcript to the reference only after receiving the result.
