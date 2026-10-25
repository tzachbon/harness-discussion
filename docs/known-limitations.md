# Known limitations

- Codex's realtime API is marked experimental. A Codex update can break it, and the next source then reads.
- Cursor speech uses the same call as the Cursor desktop app. A Cursor update can change it, and the next source then reads.
- Codex reads a whole selection in one session, in pieces of up to 420 characters, with a short pause between pieces. It reads in its own words: links become site names, file paths become file names, and commit hashes are skipped. Other voices first read whole sentences up to a third of the selection's words or 30 words, whichever is fewer; finishing the last sentence can take it to 60 words, never more, and a longer first sentence reads alone. They read the rest in pieces of up to 5,000 characters, preparing the next while the current one plays. Voice, language, and speed changes apply to the next complete selection.
- In testing, 5 of 16 long Codex reads had a fault: 2 lost the connection, 1 stopped early, and 2 said "Read it." aloud between pieces. Silence falls back automatically. An early stop does not.
- When Codex reads first, each popup shown pre-starts a short Codex voice session.
- Choosing a language from the menu moves keyboard focus away from the source app. The text is already captured.
- Only mouse selections trigger the bar. Keyboard selections (Shift+arrows) don't.
- Kokoro's first sentence after the app starts takes about 2 seconds while the model loads.
- Pausing the Windows voice takes effect after about a second, because SAPI keeps playing audio it has already buffered. Codex and Edge pause at once.
