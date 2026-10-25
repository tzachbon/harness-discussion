"""What the tour says, the sample texts it practises on, and the canned results used when no source is ready."""
VOICE = "This is how Harness Discussion sounds when it reads to you."
READ = ("Harness Discussion reads any text you select, in any app. It reads the opening first, so you hear it "
        "within a second, then continues sentence by sentence.")
REWRITE = "We recieved the report yesterday but nobody has read it yet ,so lets meet tomorow"  # page.html's en line
SAY = "Let's meet tomorrow at ten and bring the report."
DICTATED = "Let's meet tomorrow at 10 and bring the report."
LIVE = "I recieved the files yesterday"
SAMPLE = "Sample"  # the source name the toast shows for a canned result
NO_VOICE = "Local voices are always ready, so no sample is needed."
FIXES = (("recieved", "received"), ("yesterday but", "yesterday, but"), (" ,so lets", ", so let's"),
         ("tomorow", "tomorrow."))


def sample_fix(text: str) -> str:
    """The canned rewrite: the known mistakes fixed in whatever part of a sample was selected."""
    for old, new in FIXES:
        text = text.replace(old, new)
    return text


# COPY[step] = (title, lead, lead_without_shortcut); STATUS[...] and the other strings follow the Screen spec table.
COPY = {
    "welcome": ("Hear, rewrite, and dictate in any app",
                "About 2 minutes. You'll try each feature on sample text, right here. Practice doesn't change "
                "your settings unless you keep a feature on or change a shortcut.", None),
    "voice": ("Pick the voice that reads to you",
              "The first ready source reads. Sign in or download to add better voices.", None),
    "read": ("Select text, press play", "Select the first sentence below. The bar appears next to it. Press ▶.",
             None),
    "rewrite": ("Fix text where you typed it", "Select the text and press {rewrite}, or ✎ on the bar.",
                "Select the text, then press ✎ on the bar."),
    "review": ("See exactly what changed",
               "Click Review on the toast. Struck-through words were removed, green words are new. "
               "Click a change to keep your original there.", None),
    "dictate": ("Hold to talk", "Click in the box, hold {dictate}, say the line, then let go.",
                "Press Change… to set a dictation shortcut, then hold it and say the line."),
    "live": ("Get fixes as you type",
             "Click at the end of the line, type a space, and pause. Click the underline, or press {rewrite} "
             "to apply them all.",
             "Click at the end of the line, type a space, and pause. Click the underline to apply it."),
    "about": ("Tell the writing tools about you",
              "A line or two:\n• Who you are and what you do\n• Your working days and hours\n"
              "• How you like to write\nThe date and time zone are added for you.", None),
}
NO_SOURCE = ("No writing source is ready", "Sign in for real results. Until then, you'll see a sample.")
NO_STT = ("No speech source is ready", "Sign in or download to add speech-to-text.")
MORE = ("More: ✦ Summarize on the bar (2.5 GB download once) · ⏮ ⏭ skip by sentence · speed 0.5×–2× · "
        "read-along captions · voice order in Settings → Voices · speech-to-text server")