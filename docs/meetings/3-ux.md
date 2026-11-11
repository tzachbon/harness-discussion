## UX: flows, copy, mockups

All copy below is final. Implementers paste it as written. `{title}` is the calendar event title, else the call app
name ("Zoom", "Teams", "Google Meet in Chrome"), else "Meeting". `{elapsed}` is `m:ss` under an hour, else `h:mm:ss`.
Copy constants live in one place: `meeting_copy.py` (see part 4), so both platforms and tests share them.

### Flow A: first use (consent)

Triggered the first time the user turns on Meeting notes (Settings, tour, or the tray item when off), and again
if `meeting_consent` is false.

```text
Dialog (host.confirm, native)
Title:   Record meetings?
Message: Meeting notes records your microphone and your computer's sound, then writes notes. In many places
         everyone in a call must agree to be recorded. Tell the others and get their consent before you record.
         The recording, transcript, and notes are saved on this computer until you delete them. To transcribe and
         write notes, parts of the audio and the transcript go to the sources turned on in Settings → Dictation
         and Settings → Writing. Only Local keeps them on this computer.
Buttons: [OK] [Cancel]          host.confirm(title, message, ok="OK"). Mac's confirm defaults ok to "Download"
                                (mac/host.py:275), so pass ok="OK". Windows gains the same ok parameter in T15.
```

OK sets `meeting_consent = true` and `meetings = true`. Cancel leaves both unchanged and nothing turns on.

### Flow B: detected call

```text
idle ──(call app holds the mic 8 s, or any app holds it 8 s during a calendar event)──► toast A
toast A ──Take notes──► recording (Flow C)
toast A ──Not now / × / Esc / 30 s timeout──► idle, no more asks until this call ends
```

Toast A, shown at the bottom-right of the primary work area on Windows and under the menu bar item on Mac (both use
the existing Toast with an explicit `x, y`). It stays 30 s (pass `hide_s=30`; see T13) and pauses on hover.

```text
Windows (Toast, dark pill)                         Mac (HUD pill)
┌──────────────────────────────────────────────┐   ╭──────────────────────────────────────────────╮
│ ● Meeting detected. Take notes?               │   │ ● Meeting detected. Take notes?               │
│   Weekly sync · Zoom                          │   │   Weekly sync · Zoom                          │
│   [ Take notes ]  [ Not now ]            ×   │   │   ( Take notes )  ( Not now )            ⊗   │
└──────────────────────────────────────────────┘   ╰──────────────────────────────────────────────╯
```

Line 2 is `{title} · {app}` when a calendar event matched, else `{app}`. If the app could not be named, line 2 is omitted.

### Flow C: recording

On Take notes or Start Meeting Notes:
1. If `meeting_consent` is false, run Flow A first. Cancel aborts.
2. Mac only: if Screen Recording is not granted, show the Screen Recording guide (Flow G) and record the mic only.
   The pill says "Recording your microphone only" in line 2 until permission arrives; capture of Them starts on the
   next meeting (ScreenCaptureKit needs a relaunch after a grant, which the guide says).
3. Mic permission: reuse `host.mic_permission(then)`. Denied: toast "Microphone access is off. Open settings to allow it."
   with [Open settings] and stop.
4. Start both tracks. Show the recording pill and switch the tray icon to the recording variant.

Recording pill: the existing Toast in busy mode (never hides) with the input meter driven by the mic level.
Position as toast A. It is draggable: no. Clicking Stop or the tray item stops.

```text
Windows                                              Mac
┌──────────────────────────────────────────────┐    ╭──────────────────────────────────────────────╮
│ ● Recording  12:41   ▁▃▅▃▂▁▂▅▇               │    │ ● Recording  12:41   ▁▃▅▃▂▁▂▅▇               │
│   Weekly sync                                 │    │   Weekly sync                                 │
│   [ Stop ]                                    │    │   ( Stop )                                    │
└──────────────────────────────────────────────┘    ╰──────────────────────────────────────────────╯
● is red (#e81123 Windows, systemRedColor Mac). The pill can be hidden with × ; recording continues and the
tray icon and menu still show it. Toast text: "Recording hidden. Stop it from the menu."  (Mac: "menu bar")
```

Tray and menu bar while recording:

```text
Windows tray menu                       Mac menu bar menu
  Settings                                ● Recording 12:41  (disabled title row)
  Take the Tour                           Stop Meeting Notes
  ● Recording 12:41 (disabled)            Meeting Notes…
  Stop Meeting Notes                      ───────────
  Meeting Notes…                          …existing items…
  Recent Dictations ›
  Last voice source: Cursor
  Quit
Tray icon: icons.icon_image with a red dot (icons.recording_image). Tooltip "Harness Discussion: recording a meeting".
Mac status item: the template mark plus a red "record.circle" SF Symbol image, not template.
Accessibility label: "Harness Discussion, recording a meeting".
```

When idle the same slot shows `Start Meeting Notes` (and `Meeting Notes…`). With the feature off, `Start Meeting Notes`
still appears; clicking it runs Flow A.

### Flow D: call ended

```text
recording ──(the call app released the mic for 20 s)──► toast B (pill keeps recording)
toast B ──Stop──► stopping (Flow E)
toast B ──Keep recording──► recording; this end signal is ignored until the mic is held again
toast B ──no answer 2 min and mic still released──► stopping (Flow E)
```

Toast B replaces the pill text, keeping the timer and Stop:

```text
│ ● The call seems to have ended. Stop and make notes?  │
│   [ Stop ]  [ Keep recording ]                        │
```

Manual recordings (started from the menu with no call detected) never auto-stop. Max length: 4 h, then stop with
toast "Recording stopped after 4 hours. Notes are on the way."

### Flow E: after stop

```text
toast C (busy, spinner):  Making notes… 3 of 9 parts transcribed            [ Open ]
toast D (done, 10 s):     Notes ready: Weekly sync                          [ Open ]  ×
toast E (failed, 10 s):   Couldn't make notes. Your recording and transcript are saved.  [ Open ]  ×
```

Open shows the Meeting Notes window on that meeting. Failure reasons (safe text only) show in the detail view.

### Flow F: Meeting Notes window

One window per platform, opened from the tray `Meeting Notes…`, from toasts, from Settings → Meetings. A list on the
left, detail on the right. Newest first. Window title "Meeting Notes".

Windows (tk + ttk, same palette as Settings: PAGE #f3f3f3, CARD white, SUBTLE #5f5f5f):

```text
┌ Meeting Notes ───────────────────────────────────────────────────────────────── _ □ × ┐
│ ┌──────────────────────────┐ ┌────────────────────────────────────────────────────────┐ │
│ │ Weekly sync              │ │ Weekly sync                                            │ │
│ │ Today 10:02 · 31 min     │ │ 📅 Weekly sync · Tue 11 Nov 2026, 10:00–10:30          │ │
│ │ ──────────────────────── │ │ Zoom · 10:02–10:33 · 31 min                            │ │
│ │ Meeting                  │ │                                                        │ │
│ │ Mon 10 Nov 15:40 · 4 min │ │  [ Notes ] [ Transcript ]                              │ │
│ │ Making notes…            │ │ ┌────────────────────────────────────────────────────┐ │ │
│ │ ──────────────────────── │ │ │ Summary                                            │ │ │
│ │ Design review            │ │ │ The team agreed to ship the beta on Friday ...     │ │ │
│ │ Fri 7 Nov 14:00 · 52 min │ │ │                                                    │ │ │
│ │ Notes failed             │ │ │ Decisions                                          │ │ │
│ │                          │ │ │ • Ship the beta on Friday.                         │ │ │
│ │                          │ │ │ Action items                                       │ │ │
│ │                          │ │ │ • Dana: send the release note by Thursday.         │ │ │
│ │                          │ │ └────────────────────────────────────────────────────┘ │ │
│ │                          │ │ [ Copy notes ] [ Export Markdown… ] [ Open folder ]    │ │
│ │                          │ │                                      [ Delete… ]       │ │
│ └──────────────────────────┘ └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────────────┘
List: ttk.Treeview (one column, two-line text via "title\nsubtitle" rows height 2), focusable, arrow keys move.
Notes / Transcript: ttk.Notebook. Content: read-only tk.Text (state=disabled) with tags for headings (bold) and the
speaker label (You in #0067c0, Them in #5f5f5f), selectable, Ctrl+A / Ctrl+C work.
```

Transcript tab content:

```text
00:00  You   Hi everyone, thanks for joining.
00:12  Them  Thanks. Let's start with the beta date.
00:31  You   I think Friday works if QA signs off.
```

Mac (AppKit, same `ui.py` helpers, NSSplitView with NSTableView left and NSTabView right):

```text
╭ Meeting Notes ─────────────────────────────────────────────────────────────────────────╮
│ ● ● ●                                                                                  │
│ ┌──────────────────────────┐ ┌───────────────────────────────────────────────────────┐ │
│ │ Weekly sync              │ │ Weekly sync                                           │ │
│ │ Today 10:02 · 31 min     │ │ 􀉉 Weekly sync · Tue 11 Nov 2026, 10:00–10:30           │ │
│ │ Meeting                  │ │ Zoom · 10:02–10:33 · 31 min                           │ │
│ │ Mon 10 Nov · Making…     │ │        ⟨ Notes │ Transcript ⟩   (NSSegmentedControl)    │ │
│ │ Design review            │ │ ┌───────────────────────────────────────────────────┐ │ │
│ │ Fri 7 Nov · Notes failed │ │ │ Summary …                                         │ │ │
│ │                          │ │ └───────────────────────────────────────────────────┘ │ │
│ │                          │ │ (Copy notes) (Export Markdown…) (Show in Finder)  (Delete…) │
│ └──────────────────────────┘ └───────────────────────────────────────────────────────┘ │
╰────────────────────────────────────────────────────────────────────────────────────────╯
Mac labels "Show in Finder" instead of "Open folder". Text views are NSTextView, not editable, selectable.
```

Detail rules:

- Header line 1: meeting title. Line 2 (only with a calendar event): calendar glyph, event title, date, event time range.
  Line 3: app name (if known), recording start–end in local time, length.
- State line under the header when not `done`: "Recording…", "Transcribing… 3 of 9 parts", "Making notes…",
  "Interrupted. Finishing…", "Transcription failed: {reason}" with [Transcribe again], "Notes failed: {reason}" with [Make notes again].
  In a `done` meeting whose `error` is set: "Some parts could not be transcribed." with [Transcribe again].
- Notes tab before notes exist: "Notes appear here when the meeting ends."
- Empty list: centered "No meetings yet. Start one from the menu, or join a call with Ask when a call starts on."
- Copy notes: copies the Markdown export (header, notes, no transcript) with `host.copy_text`. Toast "Notes copied".
- Export Markdown…: native save dialog (Windows `tkinter.filedialog.asksaveasfilename`, Mac `NSSavePanel`), default
  name `{YYYY-MM-DD} {title}.md`, content = header + notes + transcript.
- Delete…: confirm "Delete this meeting? Its recording, transcript, and notes are removed from this computer. This can't
  be undone." [Delete] [Cancel]. Delete is refused while that meeting is recording or processing (button disabled,
  tooltip "Stop the meeting first").

Markdown export format (fixed, tested in T4):

```markdown
# Weekly sync

- Date: 2026-11-11 10:02–10:33 (31 min)
- Calendar: Weekly sync, 10:00–10:30
- App: Zoom

## Summary
...

## Decisions
- ...

## Action items
- [ ] Dana: send the release note by Thursday.

## Transcript
**00:00 You:** Hi everyone, thanks for joining.
**00:12 Them:** Thanks. Let's start with the beta date.
```

### Flow G: Mac Screen Recording guide

Same layout as `mac/guide.py` (`GuideWindow`), separate class `ScreenGuideWindow` in the same file.

```text
╭ Harness Discussion needs permission ────────────────────────╮
│ Let Harness Discussion hear the other side of a call         │
│ Meeting notes records your computer's sound so the notes     │
│ include what others say. macOS lists this under Screen &     │
│ System Audio Recording. Nothing is recorded until you take   │
│ notes.                                                       │
│ 1. Click Open System Settings below.                         │
│ 2. Turn on Harness Discussion in the list.                   │
│ 3. If macOS asks, quit and reopen Harness Discussion.        │
│ (Open System Settings)  ◌ Waiting for permission…           │
╰──────────────────────────────────────────────────────────────╯
Settings URL: x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture
Granted check: CGPreflightScreenCaptureAccess(); request: CGRequestScreenCaptureAccess() (Quartz, already a dependency).
```

### Settings → Meetings page

New page in the always-shown list, after Speech to Text and before General, on both platforms (Windows `PAGES`
gets "Meetings"; Mac `PAGES` gets `("Meetings", "person.2.wave.2.fill", "systemGreenColor")`). Windows sidebar glyph:
`icons.page_image("Meetings")`, color `#16a34a`, two heads. It does not depend on `writer_ready`, because it does not
need the Writer's edit capability.

```text
Windows                                                   Mac (cards, same widths as other pages)
Meetings                                                  Meetings
┌──────────────────────────────────────────────────┐     ╭──────────────────────────────────────────────────╮
│ [x] Meeting notes                                 │     │ Meeting notes                          [ ●— ]    │
│     Record calls and write notes on this PC.      │     │ Record calls and write notes on this Mac.        │
│ [x] Ask when a call starts                        │     │ Ask when a call starts                 [ ●— ]    │
│     When Zoom, Teams, Meet, Webex, Slack, or a    │     │ When Zoom, Teams, Meet, Webex, Slack, or a       │
│     browser uses the microphone.                  │     │ browser uses the microphone.                     │
└──────────────────────────────────────────────────┘     ╰──────────────────────────────────────────────────╯
Calendar                                                  Calendar
┌──────────────────────────────────────────────────┐     ╭──────────────────────────────────────────────────╮
│ Calendar link (ICS)                               │     │ [x] Use Calendar on this Mac   ● Allowed          │
│ [ •••••••••••••••••••••••••••• ] [Show] [Test]    │     │     (Allow access…) when not determined/denied    │
│ ● 3 events today                                  │     │ Calendar link (ICS)                               │
│ Paste the secret address of your calendar. In     │     │ [ ••••••••••••••••••• ] (Show) (Test)             │
│ Google Calendar: Settings → your calendar →       │     │ ● 3 events today                                  │
│ Secret address in iCal format. In Outlook:        │     │ (same help text)                                  │
│ Settings → Calendar → Shared calendars → Publish. │     ╰──────────────────────────────────────────────────╯
└──────────────────────────────────────────────────┘     System audio
Storage                                                   ╭──────────────────────────────────────────────────╮
┌──────────────────────────────────────────────────┐     │ ⚠ Screen & System Audio Recording is off.         │
│ 5 meetings · 412 MB        [Meeting Notes…]       │     │   Without it, notes have only your side. (Fix this)│
│                            [Open folder]          │     ╰──────────────────────────────────────────────────╯
└──────────────────────────────────────────────────┘     Storage (as Windows, "Show in Finder")
Note: Meetings use the Dictation microphone and speech  Note: (same)
sources, and the Writing sources for notes. Use
headphones so your speakers don't echo into your
microphone. Tell everyone in a call before you record.
```

Status texts for the ICS row: "Not set", "Checking…", "● {n} events today" (green), "● {n} events in the next 7 days"
when today has none, "● Couldn't load the calendar. Check the link." (red), "● That isn't a calendar link." (red).
EventKit row status: "● Allowed" (green), "Not allowed. (Allow access…)", "Turned off in System Settings. (Open
System Settings)", and on macOS 13 "Needs macOS 14 for full calendar access" only if the API is missing.
Detection on macOS 13: the Ask row is disabled with the sub-label "Needs macOS 14 or later. Start notes from the menu bar."

Toggling Meeting notes on runs Flow A. Toggling it off while recording asks "Stop the meeting in progress?" first.

### Tour step

New step `"meetings"` in `tour.STEPS`, after `"live"` and before `"about"`. Not a practice step.

```text
Title: Notes for your calls
Lead:  Turn on Meeting notes and the app asks when a call starts. It records you and the call, then writes a
       summary, decisions, and action items. It saves them on this computer until you delete them. Transcripts and
       notes use your Dictation and Writing sources; only Local keeps them on this computer.
       Tell the others before you record.
Body:  [ Turn on Meeting notes ]   (runs Flow A; then the button reads "✓ Meeting notes is on")
       Status row: "Asks when Zoom, Teams, Meet, or a browser uses the microphone."
Mac:   if Screen Recording is missing, a second chip [ Allow system audio ] opens Flow G.
Footer: Skip / Next (existing footer).
```

Mark: `tour_mark("meetings", TRIED)` when the feature is on at Next; SKIPPED otherwise. Mac SF Symbol for the step
`("person.2.wave.2.fill", (0x30, 0xd1, 0x58))`.

### Accessibility

- Keyboard: Windows notes window: Tab order list → tabs → text → buttons; Up/Down in the list; Delete key opens the
  delete confirm; Ctrl+C copies the selection; Esc closes. Mac: standard key view loop; ⌘C copy; ⌫ opens delete confirm
  when the table is focused; ⌘W closes.
- Toast buttons are buttons on both platforms (existing). Esc on toast A means Not now (existing Esc watcher).
- Screen reader (Mac): set `setAccessibilityLabel_` on the meter ("Microphone input level", existing), the record dot
  ("Recording"), the timer ("Elapsed time"), list rows ("{title}, {date}, {length}, {state}"), and the segmented
  control. Post `NSAccessibilityPostNotificationWithUserInfo` announcement when recording starts ("Recording started"),
  stops ("Recording stopped"), and notes are ready ("Notes ready").
- Screen reader (Windows): tk exposes limited UIA. Use plain ttk widgets (Treeview, Notebook, Button, Text) whose UIA
  names come from their text; give each button text, not glyph-only. Tray tooltip carries the state. ponytail: no
  custom UIA provider; add one if a screen-reader user reports gaps.
- Color is never the only signal: "Recording" text accompanies the red dot; state lines are text.
