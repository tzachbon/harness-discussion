## Acceptance criteria

| Claim | Check | Kind | Task |
| --- | --- | --- | --- |
| M1 ask after 8 s of a call app or browser, once per call | `tests.test_meeting_detect`; sim-call rows S2, S3 | unit + live | T6, T22 |
| M1 Windows sees the call app | T8a live output contains `msedge.exe` during sim-call, absent after | live | T8a |
| M1 Mac sees the call app | T9a live output contains the browser and `us.zoom.xos` ids | live | T9a |
| M2/M3 nothing records before consent and click | `tests.test_app_meetings` (fake mic factory never called before Take notes; Cancel on consent records nothing); S1 | unit + live | T12, T22 |
| M2 indicator visible while recording | S4 screenshot: pill with timer and meter, tray or menu bar recording state | live | T13–T15, T22 |
| M3 manual Start and Stop | `tests.test_app_meetings`; S8 | unit + live | T12, T22 |
| M4 two tracks, 16 kHz, timeline aligned | `tests.test_meeting_session` (lengths 20 s ± 0.2 s, padding) | unit | T11 |
| M4 Windows loopback works in the app and the built exe | T8b live RMS; T20 built-exe RMS above -40 dBFS | live | T8b, T20 |
| M4 Mac system audio works, own audio excluded | T9b live RMS values | live | T9b |
| M5 parts 10–30 s split at pauses, silent parts skipped | `tests.test_meeting_audio`, `tests.test_meeting_session` | unit | T3, T11 |
| M5 labels and timestamps | `tests.test_meetings_store` (`lines`), S6 | unit + live | T4, T22 |
| M6 echo removed from the shown transcript, raw kept | `tests.test_meetings_store` on `transcript.jsonl` | unit | T4 |
| M7 single pass and map-reduce, fallback, valid sections | `tests.test_meeting_notes` | unit | T5 |
| M7 real notes from a real source | S7 notes contain "Friday" under Decisions and "Dana" under Action items | live | T22 |
| M8 ICS parse (TZID, RRULE, EXDATE, overrides, Outlook) | `tests.test_calendar_ics` | unit | T7 |
| M8 EventKit reads events | T10 live count | live | T10 |
| M8 title from the calendar | R4 in the real-call checklist | owner | T22c |
| M9 window list, detail, copy, export, delete | `tests.test_win_notes_window`, `tests.test_mac_notes_window`, screenshots | unit + live | T15, T16 |
| M10 settings page | `tests.test_win_meetings_page`, `tests.test_mac_meetings_page`, screenshots | unit + live | T17, T18 |
| M11 retention under LOCAL_DIR until delete | `tests.test_meetings_store` (`delete`); S10 | unit + live | T4, T22 |
| M12 quit mid-recording keeps audio, next launch finishes | `tests.test_meeting_session` recovery case, `tests.test_app_meetings` quit case; S9 | unit + live | T11, T12, T22 |
| M12 updater waits | `tests.test_app_meetings` updater case | unit | T12 |
| M13 consent text on first use | `tests.test_app_meetings`; S1 | unit + live | T12, T22 |
| M13 log has no text | `assertLogs` cases in every new test module; S11 `rg` over the log | unit + live | all, T22 |
| M13 docs | T21 review | review | T21 |
| M14 keyboard and labels | Notes window tests drive keys; Mac Accessibility Inspector check in T16 report | unit + manual | T15, T16 |
| Quality gate | CI green on `windows-latest` and `macos-latest`; review clean | CI | parent |

## Simulated call (agents, each platform)

Setup: a clean settings copy (`meetings` off, `meeting_consent` false). Speakers on, volume about 50%, no headphones,
so the microphone hears the room. Dictation STT has at least one ready source (Local Whisper downloaded is enough). A
writing source is ready (Codex or Local). Log open: Windows `%APPDATA%\harness-discussion\harness-discussion.log`,
Mac `~/Library/Application Support/harness-discussion/harness-discussion.log`.

Serve the page from localhost, not `file://` (browsers treat `file://` origins inconsistently for microphone
permission): `uv run python -m http.server 8765 --bind 127.0.0.1 --directory docs/meetings`, then open
`http://127.0.0.1:8765/sim-call.html`. It holds the microphone like a meeting tab and reads five fixed "Them" lines through the
speakers over 80 s. The agent has no second person, so the "You" side is the room microphone; the You transcript is
checked for being recorded, not for exact words (fixture tests cover You labeling).

| Step | Action | Pass when |
| --- | --- | --- |
| S1 | Settings → Meetings, turn on Meeting notes. | The consent dialog shows the Flow A text, including where audio parts and the transcript go, and its button reads OK (not Download). OK turns the feature on. Meeting folder count is 0. |
| S2 | Open `http://127.0.0.1:8765/sim-call.html` in Chrome (Mac also Safari, Windows also Edge). Click Start call, allow the mic. | Within 8 to 12 s toast A shows "Meeting detected. Take notes?" with line 2 "Chrome" (or "Edge", "Safari"). No meeting folder exists yet. |
| S3 | Click Not now. Hang up, wait 70 s, Start call again. | No toast during the 70 s. Toast A appears again after the new start. |
| S4 | Click Take notes. Mac: if the Screen Recording guide opens, grant it, relaunch, repeat from S2. | The pill shows "● Recording" and a running timer and a moving meter. The tray icon has the red dot (Windows) or the menu bar shows the recording image (Mac). The menu shows "● Recording m:ss" and Stop Meeting Notes. A meeting folder exists with growing `you.wav` and `them.wav`. |
| S5 | Wait for the five lines (about 85 s). Click Hang up. | Within 20 to 25 s the pill reads "The call seems to have ended. Stop and make notes?". |
| S6 | Click Stop. | Toast C shows progress, then toast D "Notes ready: Meeting" within 3 minutes. Transcript tab shows Them lines matching at least 4 of the 5 script lines (word error tolerance: the key words beta, Friday, Thursday, Dana, release note present), each with a time, in order. You lines, if any, are not duplicates of Them lines (echo removed). |
| S7 | Read the Notes tab. | Summary present; Decisions has a line with "Friday"; Action items has a line with "Dana" and "release note". |
| S8 | Menu → Start Meeting Notes with no call. Wait 30 s. Menu → Stop Meeting Notes. | Records without a toast A; never auto-stops; a second meeting appears and reaches `done` or "Notes failed: …" with a safe reason (silence is acceptable to fail notes). |
| S9 | Start Meeting Notes, play the sim-call lines for 40 s, then Quit from the menu while recording. Relaunch. | After relaunch the meeting shows "Interrupted. Finishing…", then reaches `done`. `you.wav` duration ≈ the recorded time (within 2 s). |
| S10 | Detail → Export Markdown…, save to Desktop. Copy notes, paste into a text editor. Delete the S8 meeting. | The file matches the part 3 format. Paste equals the export without the transcript. The S8 folder is gone; others remain. |
| S11 | `rg -n -i "beta\|friday\|dana\|release note" <log>` | No matches. |

Evidence: screenshots of S2 toast, S4 pill and menu, S6 transcript, S7 notes, S9 relaunch state; the meeting folder
listing with sizes; the S11 command output. Run once from source and once from the built app (T20).

## Real-call checklist (owner, once per platform, about 2 minutes each)

Before: use the build the PR produced (or `uv run harness-discussion` on the branch). Add a 15-minute calendar event
titled "Notes test" starting now (Mac: in Calendar; both: in the calendar whose ICS link is in Settings → Meetings).
Use headphones on one run and speakers on the other if you can. Tell the other person you are recording.

| # | Do | Pass when |
| --- | --- | --- |
| R1 | Join a Google Meet with one other person (a phone in the same room on mute-off is fine). | Within about 10 s: "Meeting detected. Take notes?" with "Notes test · Chrome" (or your browser). |
| R2 | Click Take notes. | The recording pill and the tray or menu bar recording state show. Nothing recorded before your click (the meeting's start time in the list matches your click, not the join). |
| R3 | Talk for about 2 minutes. Say one decision ("We'll ship on Friday") and one task with a name ("Sam will send the deck by Monday"). Let the other person speak at least twice. | The pill timer runs the whole time. |
| R4 | Leave the call. | Within about 25 s the pill asks to stop. Click Stop. Within about 3 minutes "Notes ready: Notes test" shows. |
| R5 | Open the meeting. | Title "Notes test" and the calendar line show. The transcript has your lines as You and theirs as Them, in order, with times. With speakers, your side does not repeat their words. |
| R6 | Read the notes. | Summary is correct. Decisions has Friday. Action items has Sam and the deck. Nothing invented. |
| R7 | Export Markdown, then delete the meeting. | The file opens and reads well. The meeting is gone from the list. |

Report: pass or fail per row, with a screenshot of R1, R5, and R6. Any fail blocks merge.

## Risks

| Risk | Consequence | Detection | Mitigation | Residual |
| --- | --- | --- | --- | --- |
| Browser holds the mic for non-call reasons (dictation sites, voice search) | Unwanted ask | owner feedback | ask only; 8 s hold; 60 s re-arm; "Ask when a call starts" off switch | an occasional extra toast |
| Registry `LastUsedTimeStop` stays 0 after an app crash (Windows) | Ask with no call, or no end signal | T8a, field | end needs a toast click or 2 min; ponytail: add a running-process check | stale until the app runs again |
| Meet helper bundle id on Mac not in `BROWSERS` | No detection in Safari or Chrome | T9a records ids | prefix match; parent updates `BROWSERS` from T9a | other browsers need manual Start |
| macOS 13 lacks the per-process audio API | No detection | `mic_use.users()` returns None | Settings explains; manual Start works | 13 users start by hand |
| Screen Recording grant lost after an ad-hoc signed update | Them track silent | `system_audio_status()` at start | pill says "your microphone only"; guide with reset, like Accessibility | one regrant per update |
| Echo with speakers | Duplicate You lines | S6, R5 | `remove_echo`; headphones advice | partial echoes under 4 words stay |
| Whisper Small on CPU slower than real time on weak PCs | Notes arrive late | Processor backlog | parts transcribe during the call; progress toast | long meetings finish minutes after the call |
| Local LLM notes quality on long calls | Weak notes | S7, R6 | map-reduce; cloud sources first by default | Local-only users get simpler notes |
| Disk use, about 230 MB per hour | Full disk | free-space check at start, disk error stop | 1 GB floor; size on Settings page; delete | ponytail Opus compression |
| Consent law | Legal exposure for the user | n/a | warning on first use, PRIVACY.md, README, tour, checklist reminder | the user's duty |
| Cloud STT and writer sources receive meeting content | Privacy | PRIVACY.md | follows the user's saved orders; Local only keeps it local | as with dictation |
| pyobjc version mix | Import errors on Mac | T2 `uv lock`, Mac import check | new frameworks pinned `>=12,<13` to match 12.2.2 (owner approved) | none after T2 |
| macOS 15+ shows a recurring ScreenCaptureKit re-approval prompt ("… is requesting to bypass the system private window picker", repeated periodically) | The user is nagged, or denies by mistake and loses the Them track | T20, the owner's real calls | the guide explains it; the pill shows "your microphone only" when capture stops | ponytail: move system audio to CoreAudio process taps (`AudioHardwareCreateProcessTap`, macOS 14.2+), which need only the audio-capture permission, if the prompt annoys users |
| A Processor and dictation share Local Whisper | Dictation waits | `tests.test_meeting_session` pause case | `paused()` is part of `cancelled`, the in-flight part is cancelled and requeued | one segment of delay |
| SoundCard in the PyInstaller bundle | Loopback fails in the exe | T20 built-exe RMS | `--collect-all soundcard` | none after T20 |

## Open items and unverified assumptions

- Unverified: SoundCard needs per-thread COM init when recording off its import thread. T8b decides.
- Unverified: whether Windows records the venv `python.exe` or the base interpreter in the ConsentStore key when running
  from `uv run`. Both are excluded; T8a checks.
- Unverified: the exact wording and interval of the macOS 15+ ScreenCaptureKit re-approval prompt for a signed,
  non-App-Store app. T20 and the owner's Mac call record what appears.
- Unverified: which Chrome and Safari helper bundle ids hold input during a Meet call on macOS 26. T9a records them.
- Unverified: whether macOS 26 shows its own Screen Recording prompt at the first `SCStream` start in the signed app,
  and whether a relaunch is needed after the grant. T20 checks; the guide copy already says to relaunch "if macOS asks".
- Unverified: `com.apple.security.personal-information.calendars` is needed under the hardened runtime without the
  App Sandbox. Adding it is harmless; T20 confirms the prompt appears.
- Unverified: Teams (new) on Windows appears as a packaged key `MSTeams_8wekyb3d8bbwe`. T8a checks if Teams is installed; otherwise the owner's checklist covers Meet only.
- Assumption: Whisper Small handles 10–30 s parts with `stt.decode_audio`'s 60 s cap. Parts never exceed 30 s.
- Deferred: diarization of Them speakers, live notes, Opus compression, search, OAuth calendars, per-app ignore list.
