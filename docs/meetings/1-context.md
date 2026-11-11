## Objective

Add **Meeting notes** to Harness Discussion on macOS 13+ and Windows 11. When the user is in a call, the app notices,
asks once with a toast, and records only after the user accepts. It records the microphone ("You") and the computer's
sound ("Them") as two tracks, transcribes them in the background in short parts, and when the call ends it writes notes
(summary, decisions, action items) with the same writing sources the Writer uses. Meetings, with their audio,
transcript, and notes, stay on the computer until the user deletes them from the Meeting Notes window.

## Definition of Done

All of the following, on the linked Mac (`ADSKFCPF3C0H63`, Apple silicon) and on this Windows 11 PC:

1. **Live end to end.** During a call the app detects it. A toast asks "Meeting detected. Take notes?". The user clicks
   Take notes. Microphone and system audio are recorded while a recording indicator is visible. After the call, the
   transcript, labeled You and Them with times, and notes (Summary, Decisions, Action items) are saved and open in the
   Meeting Notes window.
2. **Manual control.** Start Meeting Notes and Stop Meeting Notes are always in the tray or menu bar menu, with or
   without detection.
3. **Nothing records before consent.** No audio is captured before the user clicks Take notes or Start. The first use
   shows the recording-consent warning.
4. **No data loss on quit.** Quitting the app (tray Quit, update install, or sign-out) during a recording keeps all
   audio up to the quit, and the next launch finishes the transcript and notes.
5. **Automated tests.** Tests use recorded audio fixtures. `uv run python -m unittest discover -s tests` passes in CI on
   `windows-latest` and `macos-latest`. Review is clean.
6. **Live proof.** Agents pass the simulated call (part 6) on both machines. The owner passes ONE real ~2 minute Google
   Meet call per platform from the checklist in part 6 before merge.
7. **Docs.** PRIVACY.md, README.md, and the Get Started tour describe Meeting notes, where audio and text go, and the
   consent duty.

## Context and evidence

### Repo facts (read in this worktree, branch `feat/notes-calendar-intent`, base `1a1b534`)

- App shape: `core.App(Captions, Sources, Updates, Summaries, Skips, Writer)` (`core.py:34`), extended by
  `app_live.App` and `app_tour.App` (`app_tour.py:255`). Background threads post tuples to `self.events`. `core.App.pump`
  handles them on the main thread every 30 ms (`core.py:307`). Writer events route through a `WRITER_EVENTS` dict
  (`app_writer.py:35`, `core.py:404`). Quit is the `"quit"` event (`core.py:418`).
- Mic: `mic.Recorder` (sounddevice `RawInputStream`, 24 kHz mono int16, 60 s cap, `audio._live_lock` and
  `audio._live` bookkeeping, a CoreAudio deadlock workaround in `stop()`). `mic._input_device(name)` resolves the saved
  device name. The meeting recorder must reuse these, not the 60 s Recorder itself.
- STT: `dictation.transcribe(audio, chain, order, language, cancelled, prompt)` runs the saved `stt_sources` order
  (Local Whisper Small, Cursor, Codex) and returns `(text, source)`. `stt.decode_audio` caps input at
  `stt.MAX_SECONDS = 60`. `LocalStt.transcribe` holds one model lock, shared with dictation.
- LLM: `writer.chain(sources, order)` gives the enabled writer sources. Each source has `name`, `status()`,
  `rewrite(system, text, model, effort, cancelled) -> str`. `summarize.LocalWriter.rewrite` refuses text longer than
  `MAX_CHARS // 2 = 12000` characters and caps the reply at 4096 tokens in an 8192-token context. Codex and Cursor
  adapters time out at 60 s (`llm_codex.TIMEOUT_S`, `llm_cursor.TIMEOUT_S`). A local call must bump `_summary_gen` and
  call `_arm_summary_stop()` afterwards (`app_summarize.py:594`), as local rewrites do.
- Toasts: `win/toast.py` and `mac/toast.py` `Toast.show(message, x, y, actions, busy, above)`. They hide after 6 s
  (`HIDE_MS`, `HIDE_S`), pause on hover, never take focus, and can show an input-level meter (dictation).
- Tray: Windows `win/host.py:40` pystray menu (dynamic labels supported, see "Last voice source"). Mac
  `mac/host.py:42` NSStatusItem menu rebuilt in `refresh_menu()` on `menuWillOpen_`.
- Settings pages: Windows `win/settings_ui.py` `PAGES` tuple plus one mixin per page (for example
  `win/summarize_page.py`). Mac `mac/settings_ui.py` `PAGES` of (title, SF Symbol, color) plus mixins.
  Sidebar glyphs on Windows come from `icons.page_image(name)`.
- Permission guide pattern: `mac/permission.py` (`Watcher` polls once a second) and `mac/guide.py` (`GuideWindow`).
- Storage: `settings.LOCAL_DIR` is `%LOCALAPPDATA%\harness-discussion` or `~/Library/Application Support/harness-discussion`.
  `history.py` shows the atomic write style (`tmp` then `os.replace`).
- Updater waits for idle in `app_updates.py:101` and then sends `"quit"`. Meetings must be part of that wait.
- Packaging: `packaging/build.ps1` and `packaging/build_mac.sh` (PyInstaller, `--collect-all` lists, Info.plist set
  with `plutil`). `packaging/entitlements.plist` has `com.apple.security.device.audio-input` already.
  `mac/main.py:_self_test` imports every Mac module in CI on the built app.
- Tests: `unittest`, fakes in `tests/` (for example `tests/test_core.FakeHost`, `tests/stt_helpers.wav`). CI runs
  `uv run python -m unittest discover -s tests` on `windows-latest` and `macos-latest` (`.github/workflows/build.yml:40,61`).
- Lockfile facts: `numpy` and `cffi` are already locked (SoundCard needs both). `pyobjc-core` is locked at 12.2.2.
  `tzdata` and `python-dateutil` are not locked. On this PC `zoneinfo.ZoneInfo("America/New_York")` raises
  `ZoneInfoNotFoundError` (verified 2026-11-11), so Windows needs `tzdata` for calendar time zones.
- Privacy invariant: the log has source names, timings, and error type names only. Never text, transcripts, prompts,
  replies, calendar titles, or the ICS URL.

### Spike results (verified by the root agent and a Codex child, 2026-11-11)

| Capability | Result | How |
| --- | --- | --- |
| Windows system audio | **Pass.** SoundCard 0.4.6 WASAPI loopback captured a played 440 Hz tone, RMS 0.12, on a virtual default speaker (SteelSeries Sonar). | `sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True).recorder(samplerate=48000, channels=1).record(...)` |
| Windows mic-in-use | **Pass.** `HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone` has packaged-app subkeys and a `NonPackaged` subtree. Keys are exe paths with `#` for `\` (for example `C:#Program Files (x86)#Microsoft#Edge#Application#msedge.exe`). Each has `LastUsedTimeStart` and `LastUsedTimeStop` FILETIMEs. In use means `LastUsedTimeStop == 0`. | `winreg` read |
| Mac system audio | **Pass** on macOS 26.7.2. ScreenCaptureKit `SCStream` with `capturesAudio=True`, `excludesCurrentProcessAudio=True`, `sampleRate=48000`, `channelCount=2`, minimal video (2×2, 1 s frame interval). An `NSObject` subclass implementing `stream:didOutputSampleBuffer:ofType:` for the audio type got 251 buffers in ~4 s: 48 kHz, 2 channels, **float32 non-interleaved**, RMS 0.049 overall, 0.176 peak during `afplay`. No TCC prompt in a terminal context; the packaged app still needs Screen Recording permission. | Reference: `docs/meetings/spikes/sck_audio.py` (committed by the root agent) |
| Mac mic-in-use | **Pass** per process (macOS 14+ API). ctypes CoreAudio `kAudioHardwarePropertyProcessObjectList`, then per process `kAudioProcessPropertyPID`, `kAudioProcessPropertyBundleID`, `kAudioProcessPropertyIsRunningInput`. A child PID showed input=1 during its stream and disappeared after. Baseline showed Zoom and a Notion helper holding input. | Reference: `docs/meetings/spikes/mic_procs.py` (committed by the root agent) |
| Mac device-level mic flag | **Unusable.** `kAudioDevicePropertyDeviceIsRunningSomewhere` stayed 1 at baseline and throughout. | same spike |
| Mac calendar | **Pass.** EventKit authorization status 3 (full access), overlapping events read. Info.plist needs `NSCalendarsFullAccessUsageDescription`. | pyobjc-framework-EventKit |

### Prior art (reference only, do not vendor)

OpenWhispr (MIT) has meeting detection and calendar-linked notes; its PR #2822 puts the linked calendar event in the
note header. This plan does the same in the detail header and the Markdown export.
