"""
Tracks queue position and playback progress so the API can report a random
song ID, queue position, and estimated wait time.

producer_api.py (the API process) and consumer_worker.py (the player
process) are separate processes, so state is shared via a small JSON file
on disk rather than in-memory. This is simple file-based IPC, not a proper
database — fine for a single local user, not safe under heavy concurrent
writes.
"""
import json
import os
import random
import string
import threading
import time
from typing import Optional, Tuple

from config import settings

_ID_ALPHABET = string.ascii_letters + string.digits
_ID_LENGTH = 4

# Only guards against races within a single process (e.g. concurrent FastAPI
# requests). It does nothing to prevent a race with the separate consumer
# process, which is an accepted limitation for this local, single-user setup.
_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(settings.queue_state_file):
        return {"items": []}
    try:
        with open(settings.queue_state_file, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"items": []}


def _save(data: dict) -> None:
    tmp_path = settings.queue_state_file + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f)
    os.replace(tmp_path, settings.queue_state_file)


def _remaining_seconds(item: dict) -> float:
    duration = item.get("duration") or 0
    if item.get("status") == "playing" and item.get("started_at"):
        elapsed = time.time() - item["started_at"]
        return max(duration - elapsed, 0)
    return duration


def _generate_song_id(existing_items: list) -> str:
    """4-char random alphanumeric ID, regenerated on the rare collision with
    an ID already in the (small, short-lived) current queue."""
    existing_ids = {item["id"] for item in existing_items}
    while True:
        song_id = "".join(random.choices(_ID_ALPHABET, k=_ID_LENGTH))
        if song_id not in existing_ids:
            return song_id


def add_song(
    url: str,
    duration: Optional[float],
    title: Optional[str] = None,
    uploader: Optional[str] = None,
    video_id: Optional[str] = None,
) -> Tuple[str, int, float]:
    """
    Registers a newly-enqueued song and returns:
      (song_id, position_in_queue, estimated_wait_seconds)

    song_id is a random 4-character alphanumeric string, generated fresh per
    song rather than derived from the YouTube URL/video ID, so it's short
    and easy to hand back to the caller for later status lookups.

    video_id (the YouTube video ID, separate from song_id) is stored so
    find_by_video_id() can detect the same video being enqueued twice.

    position_in_queue is 1-indexed (1 means "plays next"). estimated_wait_seconds
    is the sum of the (remaining) durations of every song currently ahead of it.
    """
    with _lock:
        data = _load()
        song_id = _generate_song_id(data["items"])

        songs_ahead = data["items"]
        estimated_wait_seconds = sum(_remaining_seconds(item) for item in songs_ahead)
        position_in_queue = len(songs_ahead) + 1

        data["items"].append(
            {
                "id": song_id,
                "url": url,
                "video_id": video_id,
                "title": title,
                "uploader": uploader,
                "duration": duration,
                "status": "queued",
                "started_at": None,
            }
        )
        _save(data)
        return song_id, position_in_queue, estimated_wait_seconds


def get_status(song_id: str) -> Optional[dict]:
    """
    Looks up a song by ID and returns its current status, position in queue,
    and estimated wait time — or None if the ID is unknown (never enqueued,
    or already finished playing).
    """
    with _lock:
        data = _load()
        items = data["items"]
        for index, item in enumerate(items):
            if item["id"] == song_id:
                songs_ahead = items[:index]
                estimated_wait_seconds = sum(_remaining_seconds(s) for s in songs_ahead)
                return {
                    "id": song_id,
                    "url": item["url"],
                    "title": item.get("title"),
                    "uploader": item.get("uploader"),
                    "status": item["status"],
                    "position_in_queue": index + 1,
                    "duration_seconds": item.get("duration"),
                    "estimated_wait_seconds": estimated_wait_seconds,
                }
        return None


def find_by_video_id(video_id: str) -> Optional[dict]:
    """
    Looks up a song already in the queue (queued or playing) by its YouTube
    video ID, to detect the same video being enqueued twice. Same return
    shape as get_status(), or None if no match.
    """
    with _lock:
        data = _load()
        items = data["items"]
        for index, item in enumerate(items):
            if item.get("video_id") and item["video_id"] == video_id:
                songs_ahead = items[:index]
                estimated_wait_seconds = sum(_remaining_seconds(s) for s in songs_ahead)
                return {
                    "id": item["id"],
                    "url": item["url"],
                    "title": item.get("title"),
                    "uploader": item.get("uploader"),
                    "status": item["status"],
                    "position_in_queue": index + 1,
                    "duration_seconds": item.get("duration"),
                    "estimated_wait_seconds": estimated_wait_seconds,
                }
        return None


def current_wait() -> Tuple[int, float]:
    """
    Returns (queue_length, estimated_wait_seconds): how many songs are
    currently queued/playing, and how long a song enqueued right now would
    have to wait before it starts (the same calculation add_song() would
    produce, without actually adding anything).
    """
    with _lock:
        data = _load()
        items = data["items"]
        return len(items), sum(_remaining_seconds(item) for item in items)


def list_queue() -> list:
    """
    Returns every song currently in the queue, in play order, each annotated
    with its position and estimated wait time (same shape as get_status()).
    """
    with _lock:
        data = _load()
        items = data["items"]
        result = []
        songs_ahead = []
        for index, item in enumerate(items):
            estimated_wait_seconds = sum(_remaining_seconds(s) for s in songs_ahead)
            result.append(
                {
                    "id": item["id"],
                    "url": item["url"],
                    "title": item.get("title"),
                    "uploader": item.get("uploader"),
                    "status": item["status"],
                    "position_in_queue": index + 1,
                    "duration_seconds": item.get("duration"),
                    "estimated_wait_seconds": estimated_wait_seconds,
                }
            )
            songs_ahead.append(item)
        return result


def has_pending_songs() -> bool:
    """True if there's at least one real (Kafka-backed) song queued or
    playing. Used to decide whether to fall back to the default playlist,
    and as the interrupt check while a default-playlist song is playing."""
    with _lock:
        return len(_load()["items"]) > 0


def mark_skip_requested(song_id: str) -> Optional[str]:
    """
    Flags a song to be skipped and returns its status at the time of the
    request ("queued" or "playing"), or None if the ID is unknown.

    For a "playing" song, the caller still needs to trigger the ffplay-level
    skip (playback.request_skip()) since that's what actually stops audio
    that's already streaming. For a "queued" song, this flag alone is enough:
    consumer_worker.py checks it before playing a message and, if set, marks
    the song done and moves on without ever starting playback.
    """
    with _lock:
        data = _load()
        for item in data["items"]:
            if item["id"] == song_id:
                item["skip_requested"] = True
                _save(data)
                return item["status"]
        return None


def is_skip_requested(song_id: str) -> bool:
    with _lock:
        data = _load()
        for item in data["items"]:
            if item["id"] == song_id:
                return bool(item.get("skip_requested"))
        return False


def mark_playing(song_id: str) -> None:
    with _lock:
        data = _load()
        for item in data["items"]:
            if item["id"] == song_id:
                item["status"] = "playing"
                item["started_at"] = time.time()
                break
        _save(data)


def mark_done(song_id: str) -> None:
    with _lock:
        data = _load()
        data["items"] = [item for item in data["items"] if item["id"] != song_id]
        _save(data)


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    seconds = int(round(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
