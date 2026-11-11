## Requirements

| ID | Requirement |
| --- | --- |
| M1 | Detect a call: a known call app (or any app during a calendar event) holds the microphone for 8 s. Ask once per call with a toast "Meeting detected. Take notes?" and the buttons Take notes and Not now. |
| M2 | Nothing records before Take notes or Start Meeting Notes. While recording, show an indicator: a recording pill with a timer, an input meter, and Stop, plus a red-dot tray or menu bar icon and a menu item with the elapsed time. |
| M3 | Start Meeting Notes and Stop Meeting Notes are in the tray or menu bar menu at all times while the feature is on. |
| M4 | Record two tracks: microphone (You) and system output (Them), 16 kHz mono int16, written to disk as they arrive. Our own process's output is excluded on Mac (`excludesCurrentProcessAudio`). |
| M5 | Transcribe in the background in parts of 10 to 30 s, split at pauses, through the saved Dictation STT order. Skip silent parts. Label mic parts You and system parts Them, with the part's start time. |
| M6 | Echo mitigation: in the shown transcript, remove runs of 4 or more words in a You part that also appear in a Them part within ±3 s. Keep the raw text on disk. Recommend headphones in the UI and README. |
| M7 | Notes: Summary, Decisions, Action items, made with the Writer source order (Cursor, Codex, Local) and fallback. Single pass when the transcript fits the source's limit, else map-reduce. |
| M8 | Calendar: macOS EventKit (opt in, with permission) and a secret ICS URL (both platforms). An event in progress supplies the meeting title and raises detection confidence. No OAuth. |
| M9 | Meeting Notes window: a list (title, date, length, state) and a detail view (title, calendar event, date and time, app, Notes tab, Transcript tab, Copy notes, Export Markdown, Open folder, Delete, and Make notes again or Transcribe again after a failure). |
| M10 | Settings → Meetings: feature on/off with the consent text, Ask when a call starts, calendar (EventKit on Mac, ICS URL field with Test), Mac Screen Recording permission row, storage size with Open folder. |
| M11 | Retention: keep audio, transcript, and notes under `settings.LOCAL_DIR/meetings` until the user deletes the meeting. Uninstall keeps them, like settings. |
| M12 | Quit, update, crash: audio is on disk within about 1 s of capture. Quit flushes and marks the meeting interrupted. The next launch finishes transcription and notes. The updater waits while a meeting records or transcribes. |
| M13 | Privacy: a two-party-consent warning before first use that also says where audio parts and the transcript go (the enabled Dictation and Writing sources; only Local keeps them on the computer); PRIVACY.md, README, and the tour updated; the log never holds transcript text, notes, titles, or the ICS URL. |
| M14 | Accessibility: every control reachable by keyboard; screen reader labels on Mac controls; state changes announced on Mac. |

## Fixed decisions (owner, do not reopen)

- Both platforms in one PR, Mac first, then Windows.
- Calendar: EventKit (pyobjc-framework-EventKit, MIT) and a secret ICS URL on both platforms. No OAuth.
- On detect: ask with a toast. Nothing records until the click. Visible recording indicator. Manual Start and Stop always available.
- Notes LLM: reuse the Writer source order and adapters. Map-reduce for Local (about 6000-token context budget); cloud single pass when it fits.
- Retention: keep everything until the user deletes it.
- Auto-stop (owner confirmed): ask at call end; stop without an answer only after 2 minutes with the mic free; a recording started manually never auto-stops.
- Dependencies (owner confirmed) as listed in part 4, with the pyobjc frameworks pinned to the 12.x line. ONE PR.
- Privacy: consent warning on first use, PRIVACY.md, README, tour.

## Design decisions (this plan)

- Windows system audio: SoundCard 0.4.6 loopback (spike passed). PyAudioWPatch is not used.
- Mac system audio: ScreenCaptureKit audio (spike passed). Needs Screen Recording permission, with a guide like Accessibility's.
- Mac detection: per-process CoreAudio input (macOS 14+). On macOS 13, detection is off and Settings says why; manual Start still works.
- Detection never uses the device-level "running somewhere" flag (spike: always 1 on the test Mac).
- The detection end signal never stops a recording silently. It asks "The call seems to have ended. Stop and make notes?" and stops only if the user does not answer in 2 minutes and the mic is still released.
- Audio stays WAV (16 kHz mono int16, about 115 MB per hour per track). ponytail: compress to Opus with `av` (already locked) when users ask for space.
- Timestamps are per transcript part (part start), not per word.
- The meeting toast and the recording pill reuse the existing `Toast` classes (busy mode with the meter). No new floating-window code.
- Calendar RRULE expansion uses `python-dateutil`; time zones on Windows use `tzdata`. Both are small, permissive, and pure Python.
- Meetings use the Dictation microphone choice (`dictate_microphone`). No separate setting.

## Scope

In: everything in M1 to M14, both platforms.

Out (deferred): speaker diarization beyond You and Them; live notes during the call; audio playback inside the app;
compression; search across meetings; sharing or sync; Google or Microsoft OAuth calendars; per-app "never ask" lists;
auto-start without asking; reading meeting chat.

## Approach

Pure, platform-free core modules first (store, transcript, notes, detector, ICS), each with red tests from fixtures.
Then one small capture module and one mic-in-use probe per platform, verified on its machine. Then the app mixin that
owns the state machine, with fakes. Then the UI per platform (host menu and icon, toast reuse, notes window, settings
page), the tour, packaging, and docs. Live checks last: agents' simulated call on both machines, then the owner's real call.

## Privacy rules (apply to every task)

- Log only: state names, part counts, durations, source names, byte sizes, and exception type names.
- Never log or put in exception messages: transcript text, notes, meeting titles, calendar titles, app window titles, the ICS URL.
- The ICS URL is a secret. Store it in `settings.json` (same trust level as the STT server token on disk). Mask it in the UI.
- Transcript text goes to the STT sources the user enabled (Dictation order) and the writer sources the user enabled (Writing order). Only-Local keeps everything on the computer.
