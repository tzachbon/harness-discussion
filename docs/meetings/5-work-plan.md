## Work plan

### Rules for every packet

- Read `docs/meetings/PLAN.md`, then only the sections the packet names. Interfaces in part 4 are binding: if one is
  wrong, stop and report, do not change it silently.
- Write only the files listed under **Writes**. Tests go in the named test files. One writer per file: two packets
  that run at the same time never share a file.
- Do not commit, push, merge, or open PRs. The parent integrates and commits.
- Do not add dependencies except the ones T2 adds. Do not run `uv add` outside T2.
- Use `unittest` only (no pytest). Follow the module style: short modules, a docstring that explains constraints,
  no comments that restate code. Mark deliberate simplifications with `ponytail:`.
- Privacy rules in part 2 apply. Each new module's tests include an `assertLogs` check that no transcript text,
  title, or URL reaches the log where the module logs anything.
- Done means the packet's DoD commands pass with the stated result, and
  `uv run python -m unittest discover -s tests` still passes on the machine the packet runs on (platform tests skip
  elsewhere, as in `tests/test_mac*.py`, which check `sys.platform`).
- Report: files changed, commands run with their results, anything unverified.

Machines: **Any** = either OS, including CI. **Mac** = the linked Mac `ADSKFCPF3C0H63` (a T3 thread on that
environment, same branch, pull first). **Win** = this PC.

### Parallel groups

```text
G1 (parallel): T1a, T1b, T1c, T2
G2a (parallel, after G1): T3, T4, T5, T6, T7, T9c, T13, T8a(Win), T8b(Win), T9a(Mac), T9b(Mac)
                          T9a/T9b run on the Mac in parallel with Windows packets (disjoint files)
G2b (parallel, after T3 and T4 land): T10(Mac), T11
G3 (after G2a and G2b): T12 (needs T3-T7, T9c, T10 interface, T11)
G4 (after G3, Mac first): T14(Mac), T16(Mac), T18(Mac)    in parallel
                          T15(Win), T17(Win)              in parallel
G5 (after G4): T19 (tour, both), T20 (packaging), T21 (docs)          in parallel
G6: T22a Mac simulated call, then T22b Windows simulated call, then T22c owner's real calls
```

Mac-first order inside the PR: G4's Mac packets and T22a finish before T22b. Windows packets can be written in
parallel but are accepted after the Mac live check passes.

---

### T1a. Audio, calendar, and registry fixtures (Any, run on Win)

**Goal:** committed fixture files every later test uses. **Reads:** part 4 "On-disk layout", "Interfaces" (Chunker,
calendar_ics), "Platform capture details" (registry).
**Writes:** `tests/fixtures/meetings/make_fixtures.py`, `tests/fixtures/meetings/you.wav`, `tests/fixtures/meetings/them.wav`,
`tests/fixtures/meetings/script.json`, `tests/fixtures/meetings/calendar.ics`, `tests/fixtures/meetings/outlook.ics`,
`tests/fixtures/meetings/registry.json`, `tests/fixtures/meetings/transcript.jsonl`.

- `make_fixtures.py` (run once with `uv run python tests/fixtures/meetings/make_fixtures.py`, output committed):
  synthesizes speech with Windows SAPI (`System.Speech` through PowerShell `SetOutputToWaveFile` with 16 kHz 16-bit
  mono) or macOS `say -o --data-format=LEI16@16000`, whichever OS runs it. Builds two 20 s tracks on one timeline
  from `script.json`:
  `[{"track":"you","at":0.5,"text":"Hi everyone, thanks for joining."},
    {"track":"them","at":4.0,"text":"Thanks. Let's start with the beta date."},
    {"track":"you","at":9.0,"text":"I think Friday works if QA signs off."},
    {"track":"them","at":14.0,"text":"Agreed. Dana will send the release note by Thursday."}]`.
  It mixes an echo into `you.wav`: each Them line at 0.3 gain, 50 ms late. Silence elsewhere is digital zero plus
  -60 dBFS noise. Each file 16 kHz mono s16, 20.0 s (640,044 bytes).
- `calendar.ics`: a Google-style feed with fixed dates in November 2026: one single event with
  `TZID=America/New_York`; a weekly `RRULE` event with one `EXDATE` and one `RECURRENCE-ID` override; a
  `STATUS:CANCELLED` event; an all-day event; folded long `SUMMARY` with `\,`; an event with `DURATION` instead of `DTEND`.
- `outlook.ics`: one event with `TZID:Pacific Standard Time` and a matching `VTIMEZONE` block.
- `registry.json`: a dump shape `{"packaged": {"MSTeams_8wekyb3d8bbwe": {"LastUsedTimeStart": 1, "LastUsedTimeStop": 0}, ...},
  "nonpackaged": {"C:#Program Files#Zoom#bin#Zoom.exe": {...Stop: 0}, "C:#Program Files (x86)#Microsoft#Edge#Application#msedge.exe": {...Stop: 133...}, "<own exe>": {...Stop: 0}}}`.
- `transcript.jsonl`: Parts for the script above, including one echo You part duplicating a Them phrase.

**DoD:** `uv run python -c "import wave;w=wave.open('tests/fixtures/meetings/you.wav');print(w.getframerate(),w.getnchannels(),w.getnframes())"`
prints `16000 1 320000`; same for `them.wav`. Total fixture size under 1.5 MB.

### T1b. Red tests: audio and store (Any)

**Goal:** failing tests that pin `meeting_audio` and `meetings` behavior. **Reads:** part 4 "Interfaces"
(`meeting_audio.py`, `meetings.py`), "On-disk layout", "Echo mitigation"; part 3 "Markdown export format".
**Writes:** `tests/test_meeting_audio.py`, `tests/test_meetings_store.py`.

Cases, `test_meeting_audio`: `TrackFile` header sizes after close; `read_pcm` on a file whose header still says 0
(open or crashed) returns the audio on disk; a file left open (process killed: simulate by not
calling close) is readable after `fix_header` with the right frame count, and an odd trailing byte is dropped;
`Resampler(24000,1,"s16")` turns 1 s into 16000 ± 32 samples; `Resampler(48000,2,"fltp")` from two planes of a
440 Hz sine gives 16 kHz with RMS within 1 dB; `is_silent` on zeros and on fixture speech; `Chunker` on `them.wav`
yields contiguous parts covering 0 to 20 s, none over 30 s, cuts inside pauses (no cut within 0.2 s of a scripted
line start); a 70 s constant tone is cut at 30 s; `read_pcm` clamps.
Cases, `test_meetings_store` (patch `meetings.DIR` to a temp dir): `new` id format, no title in the id; `save`/`load`
round trip; `all` newest first and skips a folder with broken JSON; `delete` refuses `..` ids; `add_part` + `parts`
ignores a truncated last line; `transcribed_until`; `unfinished`; `remove_echo` on `transcript.jsonl` drops the echo
You part and keeps the real ones; `lines` order and labels; `markdown` matches the exact block in part 3 for a fixed
meeting.

**DoD:** `uv run python -m unittest tests.test_meeting_audio tests.test_meetings_store` fails with `ModuleNotFoundError`
(red). The test files import cleanly once stub modules exist.

### T1c. Red tests: notes, detector, calendar (Any)

**Writes:** `tests/test_meeting_notes.py`, `tests/test_meeting_detect.py`, `tests/test_calendar_ics.py`.
**Reads:** part 4 "Interfaces" (`meeting_notes`, `meeting_detect`, `calendar_ics`), "Notes prompts".

`test_meeting_notes` with a fake source class (`name`, `status`, `rewrite` recording calls, `short_prompt` flag):
single pass when the text fits; map-reduce for a `short_prompt` source when over `LOCAL_CHARS - len(about)` (map
call count = parts, then one reduce); with a 400-character About you note, every local call's text stays within
`LOCAL_CHARS - 400`; a cloud source single-passes a 30,000-character transcript; fallback to the next source on an
exception and on an invalid reply; `writer.Cancelled` stops; `<think>` and preamble before `## Summary` removed;
`split` never exceeds the limit and keeps line boundaries; prompts each under 2000 characters; `assertLogs` holds no
transcript words.
`test_meeting_detect`: ask after 8 s of a call app (not at 6 s); a browser counts; a non-call app counts only
`in_event`; no second ask until `REARM_S` of no candidate; `dismissed` suppresses; `"ended"` after 20 s absent for an
auto recording, never for `"manual"`; `kept` ignores the end until the app returns; `app_name` prefix matching for
`com.google.chrome.helper`; `current_event` window rules (5 min early, latest start, over 4 h ignored).
`test_calendar_ics` on both fixtures with a fixed window: expected titles and aware start times; EXDATE removed;
RECURRENCE-ID override replaces; cancelled and all-day skipped; DURATION; folded and escaped SUMMARY; Outlook TZID
falls back to the VTIMEZONE offset; `normalize_url` webcal to https, rejects http and file; `fetch` refuses a
redirect to http (patch the opener) and a body over `MAX_BYTES`.

**DoD:** `uv run python -m unittest tests.test_meeting_notes tests.test_meeting_detect tests.test_calendar_ics` is red
with `ModuleNotFoundError`.

### T2. Dependencies, settings keys, copy (Any, run on Win, then `uv lock` check on Mac by the parent)

**Writes:** `pyproject.toml`, `uv.lock`, `src/harness_discussion/settings.py`, `src/harness_discussion/meeting_copy.py`,
`tests/test_meeting_settings.py`.
**Reads:** part 4 "Dependencies", "Settings keys"; part 3 (all copy).

- Add the six dependencies with their markers. The three pyobjc frameworks are `>=12,<13` (owner approved). Run
  `uv lock` and confirm every `pyobjc-*` entry in `uv.lock` is 12.2.2.
- Settings keys and validation as part 4.
- `meeting_copy.py`: every string from part 3 as constants; `elapsed(seconds) -> "m:ss" | "h:mm:ss"`;
  `ask_lines(title, app) -> (line1, line2 | None)`; `export_name(meeting) -> "YYYY-MM-DD title.md"` with characters
  `\/:*?"<>|` replaced by `-`.
- Tests: defaults present; bad types reset; a 3000-character URL resets to ""; `elapsed(5)=="0:05"`,
  `elapsed(3725)=="1:02:05"`; `export_name` sanitizes.

**DoD:** `uv sync` succeeds; `uv run python -c "import dateutil.rrule"` works; on Win `uv run python -c "import soundcard, zoneinfo; zoneinfo.ZoneInfo('America/New_York')"` works;
`uv run python -m unittest tests.test_meeting_settings` passes. Parent then runs `uv sync` on the Mac and
`uv run python -c "import ScreenCaptureKit, EventKit, CoreMedia"`.

### T3. `meeting_audio.py` (Any)

**Writes:** `src/harness_discussion/meeting_audio.py`. **Reads:** part 4 "Interfaces" (meeting_audio), "Chunker rule".
Use `av.AudioResampler` (already a dependency through aiortc) and `array`; `numpy` is allowed (already installed).
**DoD:** `uv run python -m unittest tests.test_meeting_audio` passes (green).

### T4. `meetings.py` store, echo, Markdown (Any)

**Writes:** `src/harness_discussion/meetings.py`. **Reads:** part 4 "Interfaces" (meetings), "On-disk layout", "Echo
mitigation"; part 3 "Markdown export format".
**DoD:** `uv run python -m unittest tests.test_meetings_store` passes.

### T5. `meeting_notes.py` (Any)

**Writes:** `src/harness_discussion/meeting_notes.py`. **Reads:** part 4 "Interfaces" (meeting_notes), "Notes prompts".
Reuse `writer.chain`, `writer.Cancelled`, `writer.about`; import `summarize._clean` is not suitable (it flattens
Markdown), write a small `_clean` that only drops `<think>` blocks and the preamble.
**DoD:** `uv run python -m unittest tests.test_meeting_notes` passes.

### T6. `meeting_detect.py` (Any)

**Writes:** `src/harness_discussion/meeting_detect.py`. **Reads:** part 4 "Interfaces" (meeting_detect), "Detector rules", `CALL_APPS`.
**DoD:** `uv run python -m unittest tests.test_meeting_detect` passes.

### T7. `calendar_ics.py` (Any)

**Writes:** `src/harness_discussion/calendar_ics.py`. **Reads:** part 4 "Interfaces" (calendar_ics), "Parse rules".
Reuse the redirect-guard pattern of `summarize._HTTPSRedirect` (https only, any host, since calendar hosts vary).
**DoD:** `uv run python -m unittest tests.test_calendar_ics` passes.

### T8a. Windows mic-in-use probe (Win)

**Writes:** `src/harness_discussion/win/mic_use.py`, `tests/test_win_mic_use.py`.
**Reads:** part 1 spike row "Windows mic-in-use", part 4 "Windows `win/mic_use.py`".
Pure `_in_use(entries: dict, own: set[str]) -> set[str]` tested on `registry.json`; `own` holds `sys.executable`
and `sys._base_executable`, lowercased. `users()` reads `winreg`. The test class is
`@unittest.skipUnless(sys.platform == "win32", "Windows only")` (the pure-function test may run everywhere, the
`winreg` test may not).
**DoD:** `uv run python -m unittest tests.test_win_mic_use` passes on Win and skips the registry test on Mac. Live:
serve `docs/meetings` with `uv run python -m http.server 8765 --bind 127.0.0.1 --directory docs/meetings`, open
`http://127.0.0.1:8765/sim-call.html` in Edge, click Start call, allow the mic, then `uv run python -c "from harness_discussion.win import mic_use; print(mic_use.users())"`
prints a set containing `msedge.exe`; after Hang up it no longer does (within 2 s). Own exclusion: record 5 s with
`uv run python -c "import sounddevice as s;s.rec(80000,16000,1);s.wait()"` in one shell while a second shell prints
`users()` during it; the set must not contain `python.exe` or the base interpreter's name. Report the exact output.

### T8b. Windows loopback capture (Win)

**Writes:** `src/harness_discussion/win/loopback.py`, `tests/test_win_loopback.py`.
**Reads:** part 1 spike row "Windows system audio", part 4 "Windows `win/loopback.py`", `Resampler` in part 4.
Tests (class decorated `@unittest.skipUnless(sys.platform == "win32", "Windows only")`): with a fake `soundcard`
module (patched into `sys.modules`), frames are converted to 16 kHz s16 and passed to `on_pcm`; an exception calls
`on_error` once with the type name. No default-device follow (ponytail in part 4).
**DoD:** `uv run python -m unittest tests.test_win_loopback` passes on Win and skips on Mac. Live: a 5 s capture on a worker thread while
`powershell -c "(New-Object Media.SoundPlayer 'C:\Windows\Media\Alarm01.wav').PlaySync()"` plays yields RMS above
-40 dBFS; report the value.

### T9a. Mac mic-in-use probe (Mac)

**Writes:** `src/harness_discussion/mac/mic_use.py`, `tests/test_mac_mic_use.py`.
**Reads:** part 1 spike row "Mac mic-in-use", part 4 "Mac `mac/mic_use.py`", `docs/meetings/spikes/mic_procs.py`.
Tests (skip off darwin): own PID excluded (patch the PID list reader); a missing selector returns `None`.
**DoD:** `uv run python -m unittest tests.test_mac_mic_use` passes. Live: serve `docs/meetings` as in T8a, open
`http://127.0.0.1:8765/sim-call.html` in Chrome and in Safari, start the call, `uv run python -c "from harness_discussion.mac import mic_use; print(mic_use.users())"`
shows the browser's bundle id (record exactly which helper id appears, for `BROWSERS`); with Zoom in a test meeting
it shows `us.zoom.xos`. Report the outputs; if a helper id is not prefix-matched by `BROWSERS`, say so (the parent
updates T6).

### T9b. Mac system audio capture (Mac)

**Writes:** `src/harness_discussion/mac/system_audio.py`, `tests/test_mac_system_audio.py`.
**Reads:** part 1 spike row "Mac system audio", part 4 "Mac `mac/system_audio.py`", `docs/meetings/spikes/sck_audio.py`.
`start()` must return without waiting on the `SCShareableContent` completion handler.
Tests (skip off darwin): the planar float conversion path with a synthetic buffer (factor the buffer-to-bytes step into
a pure function `_planes(sample_buffer) -> list[bytes]` and test the downstream `Resampler` call with fake planes).
**DoD:** test passes. Live: a 5 s capture while `afplay /System/Library/Sounds/Glass.aiff` loops yields RMS above
-40 dBFS; `excludesCurrentProcessAudio` holds (a tone played by the capturing Python process itself, through
`sounddevice`, stays below -50 dBFS). Report both values.

### T9c. `mic.Recorder` streaming option (Any)

**Writes:** `src/harness_discussion/mic.py`, `tests/test_mic.py` (add cases only).
Add `Recorder(stream_factory=None, device="", on_pcm=None)`. With `on_pcm`, the callback passes each chunk to
`on_pcm` and does not keep it in memory or enforce the 60 s cap; `stop()` returns `b""`. Dictation behavior unchanged.
**DoD:** `uv run python -m unittest tests.test_mic tests.test_dictation_lifecycle` passes.

### T10. Mac calendar adapter (Mac)

**Writes:** `src/harness_discussion/mac/calendar.py`, `tests/test_mac_calendar.py`.
**Reads:** part 1 spike row "Mac calendar", part 4 "Mac `mac/calendar.py`".
Returns `meetings.Event` (from T4, which lands before this packet starts: G2b).
**DoD:** tests (skip off darwin, status mapping with a patched `authorizationStatusForEntityType_`) pass. Live: with
access granted, `events(now - 1 day, now + 1 day)` returns at least the owner's events today; print only the count.

### T11. `meeting_session.py` (Any)

**Writes:** `src/harness_discussion/meeting_session.py`, `tests/test_meeting_session.py`.
**Reads:** part 4 "Interfaces" (meeting_session), "Writer thread", "Processor thread", "State machine", "Threads".
Starts after T3 and T4 land (G2b).
Tests with fakes and a fake clock (pass `clock=fake.now`; the test advances it, nothing sleeps for real time): a fake mic
factory feeding `you.wav` at 24 kHz in 100 ms chunks and a fake system factory feeding `them.wav`; after `stop()`, both
files are 20 s ± 0.2 s and the state is `transcribing`; a system factory that delivers nothing for 5 fake seconds gives
a zero-padded `them.wav` of the right length; a mic burst after a stall does NOT lengthen `you.wav` beyond the samples
delivered (only `them.wav` is padded); `close()` within 3 s sets `interrupted`;
a disk error posts `meeting_error`; Processor with a fake `transcribe` (returns the script text by part start) writes
Parts for both tracks, skips silent parts without calling `transcribe`, stores a part that fails on every source
once as `failed` (no retry); with some parts failed it still calls `make_notes` once and reaches `done` with `error` set;
with every speech part failed it ends in `failed_transcript` without calling `make_notes`; `redo_failed=True` on a
meeting with one failed part leaves exactly one Part for that start (the new one), reaches `done`, and resets `error` to ""; a notes failure sets `failed_notes` with a safe error; recovery: a folder left `interrupted` with
unpatched headers and half the parts transcribed finishes without re-transcribing the done parts; `paused()` flipping
True while a fake `transcribe` is in flight makes its `cancelled()` return True, the part is not stored, and it is
transcribed again after `paused()` turns False (not counted as failed); `cancel()` during a part raises out of
`transcribe`, the thread ends, and `meeting.json` and `transcript.jsonl` are unchanged.
**DoD:** `uv run python -m unittest tests.test_meeting_session` passes in under 30 s.

### T12. `app_meetings.py` and core wiring (Any)

**Writes:** `src/harness_discussion/app_meetings.py`, `src/harness_discussion/core.py`, `src/harness_discussion/app_updates.py`,
`tests/test_app_meetings.py`.
**Reads:** part 3 Flows A to E; part 4 "App wiring", "Threads", "State machine", "Host methods".
Implements: consent (Flow A via `host.confirm(title, message, ok="OK")`, text from `meeting_copy`), detection loop and calendar loop threads, toast A/B/C/D/E through
`self.meeting_toast`, `meeting_start(auto, app_id=None)`, `meeting_stop()`, pill refresh once a second
(`host.after(1000, ...)` with level and elapsed), `host.set_meeting_state`, free-space check, `_recover_meetings`,
`_close_meetings` (cancel Processors first, then `session.close()`), `meeting_busy`, `delete_meeting`,
`redo_transcript`, `redo_notes`, the `{id: Processor}` map that ignores a second start for a live id, `meeting_stop`
running `session.stop()` on a worker thread and handling `meeting_stopped`, the recovery snapshot taken in
`_init_meetings`, Processor `paused` bound to
dictation state (`paused=lambda: bool(self._recorder or self._dictation_job)`, see `app_dictation.py:269`), Local writer idle-stop handling.
Tests with `tests.test_core.FakeHost` extended in the test file (fake `mic_users`, `system_audio`, `make_toast`
recording calls) and patched `meetings.DIR`: detection ask after polls; Not now suppresses; Take notes without consent
runs the confirm with `ok="OK"` and the exact consent message from part 3 (assert the full string, including "Only
Local keeps them on this computer."), and on Cancel records nothing (fake mic factory never called); start, stop, done
toast; `meeting_stop` returns before the fake Session's `stop()` finishes (it blocks on an event the test releases);
quit during recording leaves `interrupted` and the next App instance's recovery finishes it; quit while a Processor is
mid-part leaves `transcribing` with no failed part (Processor cancelled before `_close_writer`); recovery race: a
meeting started within the 10 s before `_recover_meetings` runs is not touched (its `you.wav` header and state stay as
the Session left them), and a meeting created after `_init_meetings` is not in the snapshot; Make notes again clicked
twice and recovery for the same id start only one Processor; the updater waits while recording; Start from the menu
works with `meetings` False (consent first); logs never contain the script text.
**DoD:** `uv run python -m unittest tests.test_app_meetings tests.test_core tests.test_update_app` passes.

### T13. Toast timeout, tray images (Any; Windows toast change verified on Win)

**Writes:** `src/harness_discussion/win/toast.py`, `src/harness_discussion/mac/toast.py`, `src/harness_discussion/icons.py`,
`tests/test_meeting_icons.py`.
`Toast.show(..., hide_s=None)` overrides the 6 s auto-hide for that show only. `icons.recording_image(size)` = the app
icon with a red dot at top right (like `badged`, color `#e81123`). `icons.page_image("Meetings")` glyph: two white
heads and shoulders on `#16a34a`.
**DoD:** `uv run python -m unittest tests.test_meeting_icons tests.test_win_toast_wave tests.test_mac_popup` passes
(image sizes, the red pixel present; toast tests skip off their platform).

### T14. Mac host, Screen Recording guide (Mac)

**Writes:** `src/harness_discussion/mac/host.py`, `src/harness_discussion/mac/guide.py`, `src/harness_discussion/mac/main.py`,
`tests/test_mac_meetings_host.py`.
**Reads:** part 3 tray and Flow G; part 4 "Host methods".
Menu items (Start or Stop Meeting Notes, the disabled recording row, Meeting Notes…) in `refresh_menu`; status item
image while recording; the host methods; `ScreenGuideWindow`; `_self_test` imports.
**DoD:** `uv run python -m unittest tests.test_mac_meetings_host tests.test_mac` passes; `uv run python -m harness_discussion.mac.main --self-test`
prints `self-test ok` from source.

### T15. Windows host and notes window (Win)

**Writes:** `src/harness_discussion/win/host.py`, `src/harness_discussion/win/notes_window.py`,
`tests/test_win_notes_window.py`.
**Reads:** part 3 tray, Flow A (buttons), Flow F, Accessibility; part 4 "Host methods".
`win/host.py` `confirm(self, title, message, ok="OK")`: Windows `messagebox.askokcancel` cannot rename its button, so
`ok` is accepted for parity with Mac and ignored. ponytail: a custom tk dialog when a caller needs a non-OK label.
**DoD:** `uv run python -m unittest tests.test_win_notes_window` passes (Tk tests create a root like
`tests/test_win_writer_ui.py`; list fills from a temp `meetings.DIR` with two fixture meetings; selecting shows the
header and notes; the Transcript tab shows `00:00  You  ...`; Delete asks then removes; Export writes the Markdown via a
patched save dialog; Delete disabled while `recording`). Screenshot of the window with fixture data attached to the report.

### T16. Mac notes window (Mac)

**Writes:** `src/harness_discussion/mac/notes_window.py`, `tests/test_mac_notes_window.py`.
Same behavior and DoD shape as T15, AppKit. Accessibility labels per part 3. Screenshot in the report.

### T17. Windows Settings → Meetings (Win)

**Writes:** `src/harness_discussion/win/meetings_page.py`, `src/harness_discussion/win/settings_ui.py`,
`tests/test_win_meetings_page.py`.
**DoD:** test passes: toggling Meeting notes calls the consent path; the URL field is masked with Show toggling it;
Test posts a calendar refresh and the status label shows the counts text; storage text reads `0 meetings · 0 MB` on an
empty dir. Screenshot in the report.

### T18. Mac Settings → Meetings (Mac)

**Writes:** `src/harness_discussion/mac/meetings_page.py`, `src/harness_discussion/mac/settings_ui.py`,
`tests/test_mac_meetings_page.py`.
Same DoD shape as T17, plus the EventKit row states and the Screen Recording row with Fix this opening the guide.

### T19. Tour step (Any, UI checked on both)

**Writes:** `src/harness_discussion/tour.py`, `src/harness_discussion/tour_copy.py`, `src/harness_discussion/app_tour.py`,
`src/harness_discussion/win/tour_steps.py`, `src/harness_discussion/mac/tour_steps.py`, `tests/test_tour.py` (add cases).
**DoD:** `uv run python -m unittest tests.test_tour tests.test_app_tour tests.test_app_tour_flow tests.test_win_tour tests.test_mac_tour`
passes; `"meetings"` sits before `"about"`; saved progress from before the change still resumes at the right step.

### T20. Packaging (Win for build.ps1, Mac for build_mac.sh)

**Writes:** `packaging/build.ps1`, `packaging/build_mac.sh`, `packaging/entitlements.plist`.
**DoD:** Win: `powershell -File packaging/build.ps1` builds. Start the built app, Start Meeting Notes from the tray,
play `C:\Windows\Media\Alarm01.wav` for 10 s, Stop, and confirm the newest `them.wav` is not silent:
`uv run python -c "from harness_discussion import meeting_audio as a, meetings as m;f=sorted(m.DIR.iterdir())[-1]/'them.wav';b=f.read_bytes()[44:];print(max(a.rms_dbfs(b[i:i+32000]) for i in range(0,len(b),32000)))"`
prints a value above -40 (the loudest 1 s window, so silence around the sound does not drag the average down). Mac: `packaging/build_mac.sh` builds a
signed local app; `--self-test` prints `self-test ok`; first Start shows the Screen Recording request and the Calendar
prompt names the usage string. Report screenshots of both prompts.

### T21. Docs (Any)

**Writes:** `PRIVACY.md`, `README.md`.
PRIVACY: a "Meetings" section: nothing records until you click; what is recorded (mic and computer sound); where it
is stored (exact paths for both OSes) and that it stays until you delete it; transcript parts go to the Dictation
sources in order, notes to the Writing sources in order, Local keeps them on the computer; the ICS URL is stored in
settings.json and fetched only from that address; EventKit reads events locally; the consent duty. README: a "What it
does" bullet, rows in "Where your text goes" (Meetings audio and transcript), a Settings bullet for Meetings,
headphones advice, and the Screen Recording permission under "macOS".
**DoD:** `rg -n "Meeting" PRIVACY.md README.md` shows the new sections; links resolve; the parent reviews wording.

### T22a. Mac simulated call (Mac, agent)

Run the procedure in part 6 "Simulated call" on the Mac with `uv run harness-discussion` and then on the signed local
build from T20. **DoD:** every row of the agent table in part 6 passes, with screenshots and the meeting folder listing.

### T22b. Windows simulated call (Win, agent)

Same as T22a on this PC, from source and from the T20 installer build.

### T22c. Owner's real calls (owner)

The parent sends the owner the checklist in part 6 "Real-call checklist", once per platform. Merge waits for both.
