"""
Admin announcements: an audio clip (WhatsApp voice note) or a line of text
that interrupts whatever is playing, is heard, and then lets the song carry
on from where it was.

The API drops each announcement into a spool directory as a JSON sidecar
(+ the audio file, for clips). The player polls the spool from inside its
mpv loop, so a clip cuts in mid-song within a poll interval rather than
waiting for the song to end. Between songs, consumer_worker drains the
spool too, so an announcement never waits for the next track to start.

Playback of the announcement itself goes through ffplay — separate from the
mpv process that is holding the paused song — so resume is a single IPC
command with no gap.
"""
import itertools
import json
import logging
import os
import subprocess
import tempfile
import time
from typing import Optional

from config import settings

logger = logging.getLogger("announcements")

SPOOL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".announcements")
MAX_CLIP_BYTES = 8 * 1024 * 1024
ALLOWED_TYPES = {
    "audio/ogg", "audio/opus", "audio/mpeg", "audio/mp4", "audio/m4a", "audio/x-m4a",
    "audio/aac", "audio/wav", "audio/x-wav", "audio/webm",
}
MAX_TEXT_CHARS = 300
CLIP_TIMEOUT_SECONDS = 120

# Ids sort in arrival order even when two announcements land in the same
# millisecond: a zero-padded timestamp, then a per-process counter.
_seq = itertools.count()


def _ensure_dir() -> None:
    os.makedirs(SPOOL_DIR, exist_ok=True)


def _new_id() -> str:
    return f"{int(time.time() * 1000):015d}-{next(_seq):06d}"


def enqueue_clip(data: bytes, content_type: str, sender: str) -> str:
    """Spools an audio clip. Returns the announcement id."""
    _ensure_dir()
    aid = _new_id()
    ext = ".ogg" if "ogg" in content_type or "opus" in content_type else \
          ".mp3" if "mpeg" in content_type else \
          ".m4a" if "mp4" in content_type or "m4a" in content_type or "aac" in content_type else \
          ".wav" if "wav" in content_type else ".webm"
    audio_path = os.path.join(SPOOL_DIR, aid + ext)
    with open(audio_path, "wb") as f:
        f.write(data)
    _write_sidecar(aid, {"id": aid, "kind": "clip", "audio": audio_path, "sender": sender, "created_at": time.time()})
    logger.info("Spooled announcement clip %s (%d bytes) from %s", aid, len(data), sender)
    return aid


def enqueue_text(text: str, sender: str) -> str:
    """Spools a text announcement to be spoken via TTS. Returns the announcement id."""
    _ensure_dir()
    aid = _new_id()
    _write_sidecar(aid, {"id": aid, "kind": "text", "text": text, "sender": sender, "created_at": time.time()})
    logger.info("Spooled text announcement %s from %s: %r", aid, sender, text[:60])
    return aid


def _write_sidecar(aid: str, meta: dict) -> None:
    # Write then rename so the player never reads a half-written sidecar.
    tmp = os.path.join(SPOOL_DIR, aid + ".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    os.replace(tmp, os.path.join(SPOOL_DIR, aid + ".json"))


def pending() -> bool:
    """Cheap check for the player's poll loop."""
    try:
        return any(n.endswith(".json") for n in os.listdir(SPOOL_DIR))
    except FileNotFoundError:
        return False


def _pop_next() -> Optional[dict]:
    try:
        names = sorted(n for n in os.listdir(SPOOL_DIR) if n.endswith(".json"))
    except FileNotFoundError:
        return None
    for name in names:
        path = os.path.join(SPOOL_DIR, name)
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
            os.remove(path)
            return meta
        except (OSError, json.JSONDecodeError):
            logger.warning("Dropping unreadable announcement sidecar %s", name)
            try:
                os.remove(path)
            except OSError:
                pass
    return None


def _ffplay(path: str, timeout: float = CLIP_TIMEOUT_SECONDS) -> None:
    # loudnorm so a quiet voice note still cuts through a bar system.
    subprocess.run(
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
         "-af", "loudnorm=I=-14:TP=-1.0:LRA=7", path],
        timeout=timeout,
    )


def _speak(text: str) -> None:
    """TTS a line using the same voice settings as song announcements."""
    import asyncio
    import edge_tts
    import runtime_config

    lang = runtime_config.get("tts_language") or "hi"
    voice = "en-IN-NeerjaNeural" if lang == "en" else "hi-IN-SwaraNeural"
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp = f.name
        asyncio.run(edge_tts.Communicate(text=text, voice=voice, rate="-5%").save(tmp))
        _ffplay(tmp, timeout=60)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def play_all_pending() -> int:
    """
    Plays every spooled announcement in order: a spoken "Admin announcement"
    lead-in, then the clip or the text. Blocks until done. Returns how many
    were played. Failures in one announcement never block the next.
    """
    import runtime_config

    played = 0
    while True:
        meta = _pop_next()
        if meta is None:
            return played
        try:
            lang = runtime_config.get("tts_language") or "hi"
            _speak("एडमिन की घोषणा।" if lang != "en" else "Admin announcement.")
            if meta["kind"] == "clip":
                _ffplay(meta["audio"])
            else:
                _speak(meta["text"])
            played += 1
            logger.info("Played announcement %s (%s) from %s", meta["id"], meta["kind"], meta.get("sender"))
        except Exception:
            logger.exception("Announcement %s failed", meta.get("id"))
        finally:
            audio = meta.get("audio")
            if audio:
                try:
                    os.unlink(audio)
                except OSError:
                    pass
