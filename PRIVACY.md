# Privacy policy

Harness Discussion reads text you select aloud. This page lists every place your text, audio, or other data can go.

## Text you select

The app sends the text you play to the first ready voice source in Settings → Voices:

| Source | Where the text goes |
| --- | --- |
| Cursor | Cursor, through your Cursor desktop sign-in |
| Codex | OpenAI, through your Codex CLI sign-in |
| Kokoro | Stays on your computer |
| Edge (Windows) | Microsoft's Edge read-aloud service |
| Windows, macOS | Stays on your computer |

*Prepare speech as soon as I select text* is off by default. When it is on, and Cursor or Edge reads first, the opening sentences are sent when the bar appears, before you press play.

On Windows and Mac, Dictation tries Local first by default. Downloading Whisper Small (486 MB) is a separate action in Settings → Dictation. Local transcription runs on your computer. If it is unavailable or fails, enabled Cursor or Codex sources can receive the recording in the saved order. Dictation audio is not saved.

Tidy prefers the installed, enabled Local writing model by default. Otherwise it follows the Writing source order, which can send the transcript to Cursor or Codex. Turn off **Prefer local tidy for faster results** to always follow that order. For fully offline dictation, disable Cursor and Codex in both Writing and Dictation, and download both local models first. Local transcription by itself does not make cloud cleanup or fallback offline.

*Check as I type* (Settings → Writing, on by default once rewriting is on) sends the line you are typing, without selecting it, to the same writing sources after you pause, in every app except password fields, terminals, and apps you turn it off for. Turning it back on after turning it off asks first. Results stay in memory and are never saved. With only Local on, nothing leaves your computer.

The optional speech-to-text server (Settings → Speech-to-text, off by default) sends audio and any prompt hint that other apps on your computer send it to OpenAI through your Codex sign-in. See [STT.md](STT.md).

Password fields are never read or rewritten. The log records the engine, timing, and errors, never the text.

## The Get Started tour

The tour uses the same sources as the features it shows, and each step names the source it uses. Playing the voice sample and reading the practice sentence send that text to the first ready voice. Rewriting the sample, dictating the practice line, and pausing in the last step's box send that text or recording to the first ready writing or speech source. With no source ready, the tour shows sample results that never leave your computer. Practice doesn't change your saved settings unless you turn on a Keep switch or change a shortcut. New installs start with Writing and Check as I type on. Check as I type sends a line you type in any app to the first ready writing source after you pause; turn it off in Settings → Writing.

## Downloads and update checks

- Kokoro downloads from GitHub, and the Summarize model from GitHub and Hugging Face, only when you ask in Settings.
- Whisper Small downloads from Hugging Face only when you start the download in Settings → Dictation.
- The app checks GitHub releases 30 seconds after it starts and daily, when *Check for updates automatically* (Mac, on by default) or *Install updates automatically* (Windows, off by default) is on. On Windows that setting also downloads and installs a new release. Otherwise the app checks only when you click, and downloading needs your click.

## Keeping everything on your computer

The Windows installer offers **Use only voices that run on this PC**. It turns on Kokoro and the Windows voices, and turns off Cursor, Codex, Edge, preparing speech early, the speech-to-text server, and Writing and Dictation through Cursor or Codex. It does not change update checks, which send no text. At any time, turn those sources off in Settings → Voices, and turn off Cursor and Codex separately for Writing and Dictation (Settings → Writing and Settings → Dictation).

The services above have their own privacy policies: [Cursor](https://cursor.com/privacy), [OpenAI](https://openai.com/policies/privacy-policy/), and [Microsoft](https://privacy.microsoft.com/privacystatement).
