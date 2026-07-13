"""
Quick TTS sampler — run this to hear the announcement at different speeds.
Usage:  python test_tts.py

It plays the sample phrase at four rates. Note which one sounds best,
then tell Claude the number (1-4) and it'll update consumer_worker.py.
"""
import asyncio
import os
import subprocess
import tempfile

import edge_tts

VOICE = "hi-IN-SwaraNeural"
PITCH = "+8Hz"
SAMPLE_TITLE = "Raanjhanaa - Title Track"

RATES = [
    ("-10%", "1 — slower than normal"),
    ("0%",   "2 — natural pace"),
    ("+10%", "3 — slightly upbeat"),
    ("+20%", "4 — noticeably faster"),
]


async def _generate(text: str, rate: str, path: str) -> None:
    communicate = edge_tts.Communicate(text=text, voice=VOICE, rate=rate, pitch=PITCH)
    await communicate.save(path)


def play(rate: str, label: str) -> None:
    print(f"\n▶  Playing {label}  (rate={rate}) ...")
    text = f"अगला गाना है… {SAMPLE_TITLE}!"
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp = f.name
    try:
        asyncio.run(_generate(text, rate, tmp))
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp],
            timeout=30,
        )
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


if __name__ == "__main__":
    print("TTS rate sampler — listening for the best speed.")
    print("=" * 52)
    for rate, label in RATES:
        play(rate, label)
    print("\nDone! Tell Claude which number sounded best.")
