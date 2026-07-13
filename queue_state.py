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
import re
import string
import threading
import time
from typing import Optional, Tuple

import analytics
from config import settings

# Same URL shapes as producer_api/real_time_validation, kept local to avoid a
# circular import (real_time_validation imports this module). Used to collapse
# different URL forms of the same video (?si=, ?v=, bare, …) in history.
_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([\w-]+)"
)


def _video_key(url: Optional[str], video_id: Optional[str]) -> str:
    """A stable per-video key: the video_id if known, else derived from the
    URL, else the raw URL as a last resort."""
    if video_id:
        return video_id
    match = _VIDEO_ID_RE.search(url or "")
    return match.group(1) if match else (url or "")

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


def _elapsed_seconds(item: dict) -> float:
    """
    Computes how many seconds of the song have actually been heard, accounting
    for seeks (seek_offset), paused time (paused_duration + current pause),
    and the wall-clock start time of the current playback run (started_at).
    """
    seek_offset = item.get("seek_offset") or 0
    started_at = item.get("started_at") or time.time()
    paused_duration = item.get("paused_duration") or 0
    paused_at = item.get("paused_at")

    elapsed = seek_offset + (time.time() - started_at) - paused_duration
    if paused_at:
        elapsed -= (time.time() - paused_at)
    return max(0.0, elapsed)


def _remaining_seconds(item: dict) -> float:
    duration = item.get("duration") or 0
    if item.get("status") == "playing" and item.get("started_at"):
        return max(duration - _elapsed_seconds(item), 0)
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
    source: Optional[str] = None,
    requester_id: Optional[str] = None,
) -> Tuple[str, int, float]:
    """
    Registers a newly-enqueued song and returns:
      (song_id, position_in_queue, estimated_wait_seconds)

    requester_id (e.g. "whatsapp:15551234567") is stored for analytics only;
    it is never included in the public serializers (list_queue, now-playing,
    status), so it doesn't leak to non-admins.
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
                "source": source,   # "web" | "whatsapp" | "telegram" | "api"
                "requester_id": requester_id,
                "started_at": None,
                "seek_offset": 0,
                "paused_duration": 0,
                "paused_at": None,
            }
        )
        _save(data)
        return song_id, position_in_queue, estimated_wait_seconds


def get_status(song_id: str) -> Optional[dict]:
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
                    "skip_requested": item.get("skip_requested", False),
                    "position_in_queue": index + 1,
                    "duration_seconds": item.get("duration"),
                    "estimated_wait_seconds": estimated_wait_seconds,
                }
        return None


def current_wait() -> Tuple[int, float]:
    with _lock:
        data = _load()
        items = data["items"]
        return len(items), sum(_remaining_seconds(item) for item in items)


def list_queue() -> list:
    with _lock:
        data = _load()
        items = data["items"]
        result = []
        songs_ahead = []
        for index, item in enumerate(items):
            # Hide songs already marked for skip — they're gone from the
            # user's perspective; the consumer will discard them when it
            # pulls their Kafka message.
            if item.get("skip_requested") and item["status"] != "playing":
                continue
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
                    "source": item.get("source"),
                }
            )
            songs_ahead.append(item)
        return result


def get_playing_progress() -> Optional[dict]:
    """
    Returns progress info for the currently playing song, or None if nothing
    is playing. Used by /now-playing to include elapsed/duration/paused state.
    """
    with _lock:
        data = _load()
        for item in data["items"]:
            if item.get("status") == "playing":
                return {
                    "elapsed_seconds": round(_elapsed_seconds(item), 1),
                    "duration_seconds": item.get("duration"),
                    "is_paused": item.get("paused_at") is not None,
                }
        return None


def has_pending_songs() -> bool:
    with _lock:
        return len(_load()["items"]) > 0


def get_next_queued() -> Optional[dict]:
    """
    Returns the first queued song as a plain dict with at least "id" and "url",
    or None if there's nothing waiting to play.
    Includes skip_requested items so the consumer can call mark_done() on them
    and clean them out of queue_state — otherwise they become zombie entries that
    block re-enqueue and are never removed.
    """
    with _lock:
        data = _load()
        for item in data["items"]:
            if item["status"] == "queued":
                return {
                    "id": item["id"],
                    "url": item["url"],
                    "title": item.get("title"),
                    "duration": item.get("duration"),
                }
        return None


def mark_skip_requested(song_id: str) -> Optional[str]:
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
    played_item = None
    with _lock:
        data = _load()
        for item in data["items"]:
            if item["id"] == song_id:
                item["status"] = "playing"
                item["started_at"] = time.time()
                item["seek_offset"] = 0
                item["paused_duration"] = 0
                item["paused_at"] = None
                # Record in history the moment a song starts — not when it
                # ends — so skipped and currently-playing songs appear too.
                _append_to_history(item)
                played_item = dict(item)  # snapshot for analytics (below)
                break
        _save(data)
    # Record the play for the admin dashboard outside the lock (analytics has
    # its own lock and does its own file I/O).
    if played_item is not None:
        analytics.record_play(played_item)


def mark_paused(song_id: str) -> None:
    """Record the timestamp at which the song was paused."""
    with _lock:
        data = _load()
        for item in data["items"]:
            if item["id"] == song_id and item.get("paused_at") is None:
                item["paused_at"] = time.time()
                break
        _save(data)


def mark_resumed(song_id: str) -> None:
    """Accumulate how long the song was paused, then clear the paused_at marker."""
    with _lock:
        data = _load()
        for item in data["items"]:
            if item["id"] == song_id and item.get("paused_at") is not None:
                item["paused_duration"] = (item.get("paused_duration") or 0) + (
                    time.time() - item["paused_at"]
                )
                item["paused_at"] = None
                break
        _save(data)


def mark_seeked(song_id: str, offset: float) -> None:
    """
    Record that the song was seeked to `offset` seconds. Resets the
    playback clock so elapsed is computed from the new position.
    """
    with _lock:
        data = _load()
        for item in data["items"]:
            if item["id"] == song_id:
                item["seek_offset"] = offset
                item["started_at"] = time.time()
                item["paused_duration"] = 0
                item["paused_at"] = None
                break
        _save(data)


def _load_history() -> dict:
    if not os.path.exists(settings.history_file):
        return {"songs": []}
    try:
        with open(settings.history_file, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"songs": []}


def _save_history(data: dict) -> None:
    tmp = settings.history_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, settings.history_file)


def _append_to_history(item: dict) -> None:
    """
    Adds a song to history. If the same video already exists (matched by video
    ID, so different URL forms of one video — ?si=, ?v=, bare — collapse to a
    single row), the old entry is removed first so only the most recent play
    is kept. Caps the list at 100.
    """
    history = _load_history()
    url = item["url"]
    video_id = item.get("video_id")
    key = _video_key(url, video_id)
    # Remove any previous entry for this video
    history["songs"] = [
        s for s in history["songs"]
        if _video_key(s.get("url"), s.get("video_id")) != key
    ]
    history["songs"].append({
        "url": url,
        "video_id": video_id,
        "title": item.get("title"),
        "uploader": item.get("uploader"),
        "duration": item.get("duration"),
        "source": item.get("source"),
        "played_at": time.time(),
    })
    # Keep only the last 100
    history["songs"] = history["songs"][-100:]
    _save_history(history)


def get_history(page: int = 1, per_page: int = 10, q: str = "") -> dict:
    """Returns paginated playback history, newest first. Optionally filtered by q."""
    history = _load_history()
    songs = list(reversed(history.get("songs", [])))
    if q:
        ql = q.lower()
        songs = [s for s in songs if ql in (s.get("title") or "").lower()
                 or ql in (s.get("uploader") or "").lower()]
    total = len(songs)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    return {
        "songs": songs[start:start + per_page],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }


def reorder_queue(ordered_ids: list) -> None:
    """
    Reorders the queued (not yet playing) songs to match ordered_ids.
    The playing song stays in place. IDs not in the list are appended at the end.
    """
    with _lock:
        data = _load()
        items = data["items"]
        playing = [it for it in items if it["status"] != "queued"]
        queued = [it for it in items if it["status"] == "queued"]
        by_id = {it["id"]: it for it in queued}
        new_queued = [by_id[sid] for sid in ordered_ids if sid in by_id]
        included = {sid for sid in ordered_ids if sid in by_id}
        for it in queued:
            if it["id"] not in included:
                new_queued.append(it)
        data["items"] = playing + new_queued
        _save(data)


def bump_to_front(song_id: str) -> bool:
    """Moves a queued song to play next (right after the currently playing song)."""
    with _lock:
        data = _load()
        items = data["items"]
        idx = next(
            (i for i, item in enumerate(items)
             if item["id"] == song_id and item["status"] == "queued"),
            None,
        )
        if idx is None:
            return False
        item = items.pop(idx)
        # Insert at the first queued position (after any playing song)
        insert_at = next(
            (i for i, it in enumerate(items) if it["status"] == "queued"),
            len(items),
        )
        items.insert(insert_at, item)
        _save(data)
        return True


def mark_done(song_id: str) -> None:
    with _lock:
        data = _load()
        data["items"] = [item for item in data["items"] if item["id"] != song_id]
        _save(data)


def reset_stale_playing() -> None:
    """
    On consumer startup, reset any item stuck as 'playing' back to 'queued'.
    This happens when the consumer process crashes or is killed mid-song —
    mark_done() never runs, so the item stays 'playing' forever and blocks
    get_next_queued() from seeing it.
    """
    with _lock:
        data = _load()
        changed = False
        for item in data["items"]:
            if item["status"] == "playing":
                item["status"] = "queued"
                item["started_at"] = None
                item["paused_at"] = None
                item["paused_duration"] = 0
                item["seek_offset"] = 0
                changed = True
        if changed:
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
