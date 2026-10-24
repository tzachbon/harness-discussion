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

On Windows and Mac, rewriting and dictation send the selection or the recorded audio to Cursor or Codex, in the order set in Writing and Dictation. Both platforms have separate Settings → Writing and Settings → Dictation pages. Local rewriting stays on your computer. Dictation audio is not saved.

*Check as I type* (Settings → Writing, off by default) sends the line you are typing, without selecting it, to the same writing sources after you pause, in every app except password fields, terminals, and apps you turn it off for. It asks before turning on. Results stay in memory and are never saved. With only Local on, nothing leaves your computer.

The optional speech-to-text server (Settings → Speech-to-text, off by default) sends audio and any prompt hint that other apps on your computer send it to OpenAI through your Codex sign-in. See [STT.md](STT.md).

Password fields are never read or rewritten. The log records the engine, timing, and errors, never the text.

## Downloads and update checks

- Kokoro downloads from GitHub, and the Summarize model from GitHub and Hugging Face, only when you ask in Settings.
- The app checks GitHub releases 30 seconds after it starts and daily, when *Check for updates automatically* (Mac, on by default) or *Install updates automatically* (Windows, off by default) is on. On Windows that setting also downloads and installs a new release. Otherwise the app checks only when you click, and downloading needs your click.

## Keeping everything on your computer

The Windows installer offers **Use only voices that run on this PC**. It turns on Kokoro and the Windows voices, and turns off Cursor, Codex, Edge, preparing speech early, the speech-to-text server, and Writing and Dictation through Cursor or Codex. It does not change update checks, which send no text. At any time, turn those sources off in Settings → Voices, and turn off Cursor and Codex separately for Writing and Dictation (Settings → Writing and Settings → Dictation).

The services above have their own privacy policies: [Cursor](https://cursor.com/privacy), [OpenAI](https://openai.com/policies/privacy-policy/), and [Microsoft](https://privacy.microsoft.com/privacystatement).
