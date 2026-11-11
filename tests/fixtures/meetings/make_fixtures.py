"""Generate the 16 kHz meeting fixtures with the platform speech synthesizer."""

import array
import json
import os
import random
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RATE = 16000
FRAMES = 20 * RATE


def speech(text, path):
    if sys.platform == "win32":
        command = """Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$ErrorActionPreference = 'Stop'
$s.SetOutputToWaveFile($env:MEETING_FIXTURE_WAV, $f)
$s.Speak([Console]::In.ReadToEnd())
$s.Dispose()
"""
        subprocess.run(["powershell", "-NoProfile", "-Command", command],
                       input=text, text=True, check=True,
                       env={**os.environ, "MEETING_FIXTURE_WAV": str(path)})
    elif sys.platform == "darwin":
        subprocess.run(["say", "-o", str(path), "--file-format=WAVE",
                        "--data-format=LEI16@16000", text], check=True)
    else:
        raise RuntimeError("Fixture synthesis requires Windows or macOS")
    with wave.open(str(path), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (RATE, 1, 2)
        samples = array.array("h", audio.readframes(audio.getnframes()))
        if sys.byteorder != "little":
            samples.byteswap()
        return samples


def main():
    script = json.loads((ROOT / "script.json").read_text(encoding="utf-8"))
    rng = random.Random(1)
    tracks = {name: [rng.gauss(0, 32767 * 0.001) for _ in range(FRAMES)]
              for name in ("you", "them")}
    with tempfile.TemporaryDirectory() as tmp:
        for line in script:
            samples = speech(line["text"], Path(tmp) / "speech.wav")
            start = round(line["at"] * RATE)
            assert start + len(samples) <= FRAMES
            for name, offset, gain in [(line["track"], 0, 1.0)] + (
                [("you", int(0.05 * RATE), 0.3)] if line["track"] == "them" else []
            ):
                assert start + offset + len(samples) <= FRAMES
                for i, sample in enumerate(samples, start + offset):
                    tracks[name][i] += sample * gain
    for name, values in tracks.items():
        samples = array.array("h", (max(-32768, min(32767, round(v))) for v in values))
        if sys.byteorder != "little":
            samples.byteswap()
        with wave.open(str(ROOT / f"{name}.wav"), "wb") as audio:
            audio.setparams((1, 2, RATE, FRAMES, "NONE", "not compressed"))
            audio.writeframes(samples.tobytes())


if __name__ == "__main__":
    main()
