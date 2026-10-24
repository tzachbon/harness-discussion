<div align="center">

<img src="docs/icon.png" width="80" height="80" alt="">

# Harness Discussion

**Select text in any Windows or Mac app and hear it read aloud with the voice from your Cursor or Codex sign-in.**

Harness Discussion reads with Cursor's voices through your Cursor desktop sign-in, or with the Codex realtime voice from your ChatGPT sign-in through `codex app-server`. No API key. Local voices take over when neither can speak.

<sub>Unofficial. Not affiliated with Cursor or OpenAI.</sub>

[![Build](https://github.com/tzachbon/harness-discussion/actions/workflows/build.yml/badge.svg)](https://github.com/tzachbon/harness-discussion/actions/workflows/build.yml)
[![Latest release](https://img.shields.io/github/v/release/tzachbon/harness-discussion)](https://github.com/tzachbon/harness-discussion/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
![Windows 11](https://img.shields.io/badge/Windows-11-0078d4)
![macOS 13+](https://img.shields.io/badge/macOS-13%2B-555555)

[**Download for Windows**](https://github.com/tzachbon/harness-discussion/releases/latest/download/HarnessDiscussion-Setup.exe) · [**Download for Mac**](https://github.com/tzachbon/harness-discussion/releases/latest/download/HarnessDiscussion.dmg) · [Website](https://tzachbon.github.io/harness-discussion/) · [Report a bug](https://github.com/tzachbon/harness-discussion/issues/new/choose)

[![The current Windows selection bar over sample text, with Play, Summarize, voice, speed, More, and Close](docs/popup.png)](docs/popup.png)

</div>

## What it does

- **Works in most apps.** Drag or double-click to select text in a browser, Notepad, VS Code, or most other apps. A rounded bar appears next to the selection without taking focus: Play, Summarize, voice, speed, **…**, and Close. Language and Settings are in **…**.
- **Reads with the sign-in you already have.** First Cursor, with any of its 28 voices, through the Cursor desktop app's sign-in. Then the Codex realtime voice through the [Codex CLI](https://github.com/openai/codex) sign-in, which uses your ChatGPT plan's Codex access and counts toward its limits.
- **Then Kokoro, on your computer.** Kokoro is an open-weight neural voice that runs offline. Settings → Voices downloads it once (about 120 MB). It reads English, Spanish, and French.
- **Then local voices.** If none of those can speak, Edge neural voices take over, then the offline Windows voices. On a Mac, the built-in macOS voice takes over.
- **You set the order.** Settings → Voices lists the sources in reading order. Turn each one on or off, move it up or down, pick its voice, and test it. A source that needs installing or a sign-in shows a button that opens the download page or starts the sign-in. The bar's source menu moves a source to the top. Picked during a read, it takes over from the sentence being read.
- **Summarize.** ✦ next to Play summarizes the selection on your computer with Qwen3-4B through [llama.cpp](https://github.com/ggml-org/llama.cpp), then reads the summary. The first click asks to download the model once (2.5 GB, verified by SHA-256). Settings → Summarize shows its status and removes it.
- **Rewrite in place.** Over text you can edit, ✎ on the bar, Ctrl+Shift+E on Windows, or ⌥⌘E on a Mac rewrites the selection with the default mode and pastes it back. Modes are Fix errors, Message, Email, Improve writing, Clean dictation, and your own. A toast offers Undo, Review (keep or revert each change), and another mode. Press the rewrite shortcut twice quickly to pick a mode first. Cancel on the toast, or Esc, stops it. Cursor, Codex, then the local Qwen3-4B model rewrite, in the order you set. Off by default. Turn it on in Settings → Writing. *Check as I type* there underlines the default mode's changes to the line you pause on; click one to apply it.
- **Dictation.** Hold Ctrl+Shift+D on Windows (⌥⌘D on Mac), speak, then release. Cursor, then Codex by default, transcribes; the dictation mode tidies the result before insertion. Esc or Cancel stops it. *Clean dictation* removes fillers and formats spoken steps as numbered lists, including corrected numbers. Set its independent mode under Settings → Dictation. Custom prompts come from Writing → Modes. Turn off *Tidy dictation after transcribing* for raw text. A short tap does nothing. Uses the Writing switch. Windows support is experimental; live English, Hebrew, and mixed speech acceptance is pending ([#92](https://github.com/tzachbon/harness-discussion/issues/92)).
- **Pause, resume, stop.** ⏸ pauses and ▶ continues from the same spot. ■ stops.
- **Skip by sentence.** ⏮ and ⏭ on the bar read again from the previous or next sentence.
- **Read shortcut.** Ctrl+Alt+R on Windows, ⌥⌘R on a Mac, reads the selection at once and shows the bar at the pointer. Press it again to stop. Change it or turn it off in Settings → Reading. Any key works, alone or with modifiers. Modifier-only combinations work too: Ctrl+Win on Windows, or ⌃⌥ on Mac.
- **Speed on the bar.** 0.5× to 2×, saved and shared with Settings.
- **Read-along captions.** The current sentence appears above the bar as it plays.
- **Six languages.** Hebrew, English, Arabic, Russian, Spanish, and French.
- **Updates itself if you want.** Optional automatic updates from GitHub releases, verified by SHA-256.
- **Speech-to-text server (optional).** An OpenAI-compatible local endpoint that transcribes with the same Codex sign-in. See [STT.md](STT.md).

[![Windows read-along caption and playback controls, rendered with sample text](docs/captions.png)](docs/captions.png)

Images show the current Windows UI with sample text. [How the promotional images were made](docs/promotion-assets.md).

## Install

1. Download **[HarnessDiscussion-Setup.exe](https://github.com/tzachbon/harness-discussion/releases/latest/download/HarnessDiscussion-Setup.exe)** and run it. It installs for your user only and needs no admin rights.
2. The installer isn't code-signed, so SmartScreen may warn you. Choose *More info*, then *Run anyway*.
3. Optional: sign in to the [Cursor](https://cursor.com) desktop app, or install the [Codex CLI](https://github.com/openai/codex) (`npm i -g @openai/codex`) and sign in once with `codex login`. Without either, the app uses the Edge and Windows voices.
4. The first time it starts, the app opens Settings so you can check the voice sources.

Requirements: Windows 11, 64-bit.

## macOS

1. Download **[HarnessDiscussion.dmg](https://github.com/tzachbon/harness-discussion/releases/latest/download/HarnessDiscussion.dmg)**, open it, and drag Harness Discussion to Applications.
2. Open Harness Discussion from Applications. If macOS says it "could not verify" the app, the build isn't notarized yet:
   - On macOS 15 and later, choose *Done*, open *System Settings > Privacy & Security*, scroll to *Security*, choose *Open Anyway* next to Harness Discussion, then confirm with your password.
   - On macOS 13 and 14, right-click the app, choose *Open*, then *Open* again.
   - Or run `xattr -dr com.apple.quarantine "/Applications/Harness Discussion.app"` in Terminal, then open the app.
3. Harness Discussion lives in the menu bar. It asks for **Accessibility** access, which it needs to read the selected text. A guide window opens System Settings for you and closes once access is granted. While access is missing, the menu bar icon shows a warning.
4. Optional: sign in to Cursor, or install the Codex CLI and run `codex login`, as on Windows.

Requirements: macOS 13 or later on Apple silicon.

On a Mac, the voices are Cursor, Codex, Kokoro, and then the built-in macOS voice. Kokoro and the macOS voice stay on your Mac. Edge voices are Windows only for now. *Start Harness Discussion when I log in* adds a LaunchAgent. Settings are kept in `~/Library/Application Support/harness-discussion`. Updates are not installed automatically: Settings shows when a new version is out, and *Download* downloads it, checks its SHA-256 digest, replaces the app, and reopens it. If the app can't replace itself, for example when it runs from the dmg or a folder you can't write to, *Download* opens the release page instead.

After an update, macOS can stop trusting the app even though System Settings still lists it as allowed. Harness Discussion detects this, and the guide's *Fix permission* button clears the old entry so you can grant it again.

**Upgrading from Codex Speak or Select to TTS?** This is the same app under a new name. On Windows, the update or the new installer replaces the old app, and your settings, sign-in startup choice, and speech-to-text URL carry over. On a Mac, install the new dmg and open it once: it quits Codex Speak, moves its settings, and replaces its login item. macOS asks for Accessibility again, because the app has a new identity. You can then delete Codex Speak from Applications.

## Use

1. Select text with the mouse (drag or double-click).
2. Press ▶ on the bar. The language menu defaults to Auto.
3. Pick a speed from the bar's speed menu, and a source from its source menu.
4. ⏸ pauses and ▶ continues. ■ stops. ⏮ and ⏭ move by a sentence. Selecting new text while paused replaces the paused read.
5. Or press the read shortcut (Ctrl+Alt+R, or ⌥⌘R on a Mac) to read the selection at once.
6. Enable Settings → Writing. Over text you can edit, press ✎ or the rewrite shortcut (Ctrl+Shift+E on Windows, ⌥⌘E on Mac). Hold the dictation shortcut (Ctrl+Shift+D on Windows, ⌥⌘D on Mac) to dictate.

On a Mac, the menu bar icon's menu shows what is playing and has Pause, Stop, Speed, and Voice Source. *Show Popup on Selection* turns the bar off until you turn it back on or restart the app.

Choose **Settings…** from the bar's **…** menu, click the tray or menu bar icon, or start the app again from the Start menu to open **Settings**.

## Where your text goes

| Voice | Needs | Speed | Where your text goes |
| --- | --- | --- | --- |
| Cursor | Cursor desktop app, signed in | 0.5× to 2× | Cursor, through your Cursor sign-in |
| Codex realtime | Codex CLI signed in with ChatGPT | 0.5× to 1×. Above 1×, the next source reads | OpenAI, through your Codex sign-in |
| Kokoro | A one-time 120 MB download from Settings | 0.5× to 2× | Stays on your computer |
| Edge neural (Windows) | Internet | 0.5× to 2× | Microsoft's Edge read-aloud service |
| Windows | Nothing, it's built in | 0.5× to 2× | Stays on your PC |
| macOS | Nothing, it's built in | 0.5× to 2× | Stays on your Mac |

| Rewrite and dictation | Needs | Where your text or audio goes |
| --- | --- | --- |
| Cursor | Cursor CLI (`cursor-agent`), signed in | Cursor, through your Cursor sign-in |
| Codex | Codex CLI signed in with ChatGPT | OpenAI, through your Codex sign-in |
| Local (rewrite only) | The Summarize model | Stays on your computer |

- For local read-aloud, enable only Kokoro and Windows or macOS in Settings → Voices. Writing and Dictation have separate source settings.
- Password fields are never read or rewritten.
- For local rewrites, turn off Cursor and Codex in Settings → Writing and use Local. Dictation audio goes to the first ready source, Cursor by default, and is not saved. Turn off Cursor and Codex under Settings → Dictation to disable transcription through those services.
- A rewrite is pasted only if the same app, window, and selection are still there. Your clipboard is put back afterwards.
- On Windows, the app reads accessible selections without automatic clipboard copying. Apps that hide their selection cannot be read, and list numbers may be omitted.
- The log records the engine, timing, and errors, never the text itself.
- Kokoro has no Hebrew, Arabic, or Russian voice, so the next source reads those.
- In Auto, Cursor and Codex work out the language themselves. Kokoro, Edge, and Windows pick Hebrew, Arabic, or Russian voices from the script and use English for everything else. Choose Spanish or French from the menu for those voices.

## Settings

<a href="docs/settings.png"><img src="docs/settings.png" width="320" align="right" alt="Windows Settings on the Voices page, with example source statuses, source order, voice choices and speed"></a>

- **Start when I sign in to Windows**: adds or removes a Task Scheduler logon task named "Harness Discussion".
- **Check and install update / Install updates automatically**: see [Updates](#updates).
- **Speed**: 0.5× to 2×. Changes apply to the next read.
- **Sources**: under Voices, the voice sources in reading order, by default Cursor, Codex, Kokoro, Edge, then Windows (Cursor, Codex, Kokoro, then macOS on a Mac). Kokoro is skipped until you press its **Download…**. The model goes to `%LOCALAPPDATA%\harness-discussion\kokoro` (`~/Library/Application Support/harness-discussion/kokoro` on a Mac), and its size and SHA-256 digest are checked. Each row shows whether the source is ready, its voice where it has a choice, an on/off box, Test, and ↑ ↓ to change the order. If one source can't read, the next one does.
- **Captions**: font size (8 to 24 points), optional background, and background opacity. Changes apply to the visible caption right away.
- **Read apps that hide their selection**: on Windows, unavailable to protect your clipboard. The app reads selections exposed through accessibility. On a Mac, this option copies with ⌘C and then puts your clipboard back.
- **Prepare speech as soon as I select text**: off by default. When Cursor or Edge reads first, the app requests the opening's audio as soon as the bar appears. This sends those sentences to Cursor or Microsoft before you press play.
- **Speech-to-text server**: see [STT.md](STT.md).
- **Writing**: turns rewriting on or off, and sets the default mode, the rewrite shortcut, and the sources in order with each one's model and effort. The modes editor adds, renames, and deletes your own modes. Built-in modes keep their name, and Reset puts back their instructions.
- **Dictation** (Settings → Dictation): the transcript sources in order (Cursor, then Codex, by default), the cleanup switch and its own mode selector (including custom modes), the hold shortcut, and the microphone permission. On Windows, Microphone settings opens the system privacy page. Changed or unsupported edit targets offer Copy instead of typing into another field.
- **Shortcuts**: Change… records the next key you press, alone or with modifiers. Esc keeps the current shortcut. Holding modifiers such as Ctrl+Win on Windows or ⌃⌥ on Mac and letting go without a key records them alone. That shortcut fires when you let go, unless you pressed another key while holding them. A hold shortcut starts once they are all down. On a Mac, ⌘` also switches windows unless you turn off “Move focus to next window” in System Settings → Keyboard → Keyboard Shortcuts → Keyboard.

Settings are saved in `%APPDATA%\harness-discussion\settings.json`, next to a small `harness-discussion.log`.

<br clear="right">

<details>
<summary><b>Speed details</b></summary>

Cursor, Edge, Windows, and macOS voices support the full range. Codex streams speech in real time, so it can only be slowed down. Above 1×, the next source in the order reads. With only Codex turned on, it reads at its natural pace.

</details>

<details id="updates">
<summary><b>Updates</b></summary>

**Check and install update** checks the latest stable GitHub release, verifies the installer's size and SHA-256 digest, installs it into the current app folder, and relaunches. It waits for speech, including paused speech, and active transcriptions to finish.

**Install updates automatically** is off by default. When on, the app checks shortly after startup and every 24 hours, and installs when idle. Turning it off cancels a pending automatic installation. Settings and your sign-in startup choice are kept.

If a check or download fails, the app keeps running and Settings shows the error. Downloaded installers and `setup.log` are kept under `%LOCALAPPDATA%\harness-discussion\updates` for diagnosis and removed on uninstall. The digest checks that the download matches GitHub's release metadata. The installer is unsigned, so the updater trusts this repository's release account.

Update controls are disabled when running from source.

</details>

<details>
<summary><b>Uninstall</b></summary>

Use *Settings > Apps > Harness Discussion*. This removes the app, its sign-in task, the Kokoro model, and its caches in `%TEMP%`. Your settings and log in `%APPDATA%\harness-discussion` are kept.

</details>

## Known limitations

- Codex's realtime API is marked experimental. A Codex update can break it, and the next source then reads.
- Cursor speech uses the same call as the Cursor desktop app. A Cursor update can change it, and the next source then reads.
- Codex reads a whole selection in one session, in pieces of up to 420 characters, with a short pause between pieces. It reads in its own words: links become site names, file paths become file names, and commit hashes are skipped. Other voices read an opening of whole sentences until it reaches a third of the selection's words or 30 words, whichever is fewer. Finishing its last sentence can take it to 60 words, never more, and a longer first sentence reads alone. Then they read the rest in pieces of up to 5,000 characters. The next piece is prepared while the current one plays. Voice, language, and speed changes apply to the next complete selection.
- In testing, 5 of 16 long Codex reads had a fault: 2 lost the connection, 1 stopped early, and 2 said "Read it." aloud between pieces. Silence falls back automatically. An early stop does not.
- When Codex reads first, each popup shown pre-starts a short Codex voice session.
- Choosing a language from the menu moves keyboard focus away from the source app. The text is already captured.
- Only mouse selections trigger the bar. Keyboard selections (Shift+arrows) don't.
- Kokoro's first sentence after the app starts takes about 2 seconds while the model loads.
- Pausing the Windows voice takes effect after about a second, because SAPI keeps playing audio it has already buffered. Codex and Edge pause at once.

## Run from source

```powershell
uv sync
uv run harness-discussion     # or start .venv\Scripts\harness-discussion.exe (no console window)
uv run python -m unittest discover -s tests
```

[CONTRIBUTING.md](CONTRIBUTING.md) covers the dev setup, building the installer, releases, and a map of the code.

## Code signing policy

Windows code signing is **pending**. The project is applying for free code signing from SignPath Foundation, and current installers are still unsigned. When signing starts, Windows releases will say:

Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).

- Authors and committers: [tzachbon](https://github.com/tzachbon)
- Reviewers: [tzachbon](https://github.com/tzachbon)
- Approvers: [tzachbon](https://github.com/tzachbon)

Only the app's own executable and its installer will be signed, from GitHub Actions builds of this repository, and every release needs a manual approval. See the [privacy policy](PRIVACY.md) for where your text goes.

## Contributing

Bug reports, fixes, and new language voices are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md). Report security issues privately as described in [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE). Not affiliated with Cursor, OpenAI, Microsoft, or Apple.

The Kokoro voice uses [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) (MIT) and the [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) weights (Apache-2.0). The installers bundle [espeak-ng](https://github.com/espeak-ng/espeak-ng) for pronunciation, which is licensed under GPL-3.0.
