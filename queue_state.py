"""
Tracks queue position and playback progress so the API can report a random
song ID, queue position, and estimated wait time.

Uses SQLite (via db module) for persistent storage, with WAL mode for safe
concurrent access from the API and consumer processes.
"""
import random
import re
import string
import time
from typing import Optional, Tuple

import analytics
import db

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


def _elapsed_seconds(item: dict) -> float:
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


def _generate_song_id(conn) -> str:
    existing = {r[0] for r in conn.execute("SELECT id FROM queue_items").fetchall()}
    while True:
        song_id = "".join(random.choices(_ID_ALPHABET, k=_ID_LENGTH))
        if song_id not in existing:
            return song_id


def _items_ordered(conn) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM queue_items ORDER BY position"
    ).fetchall()]


def add_song(
    url: str,
    duration: Optional[float],
    title: Optional[str] = None,
    uploader: Optional[str] = None,
    video_id: Optional[str] = None,
    source: Optional[str] = None,
    requester_id: Optional[str] = None,
    dedication: Optional[str] = None,
) -> Tuple[str, int, float]:
    with db.transaction() as conn:
        song_id = _generate_song_id(conn)
        items = _items_ordered(conn)
        estimated_wait_seconds = sum(_remaining_seconds(item) for item in items)
        position_in_queue = len(items) + 1
        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM queue_items"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO queue_items "
            "(id, url, video_id, title, uploader, duration, status, source, "
            "requester_id, started_at, seek_offset, paused_duration, paused_at, "
            "skip_requested, position, dedication) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                song_id, url, video_id, title, uploader, duration, "queued",
                source, requester_id, None, 0, 0, None, 0, max_pos + 1,
                dedication,
            ),
        )
    return song_id, position_in_queue, estimated_wait_seconds


def get_status(song_id: str) -> Optional[dict]:
    conn = db.get_conn()
    items = _items_ordered(conn)
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
    conn = db.get_conn()
    items = _items_ordered(conn)
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
                "skip_requested": bool(item.get("skip_requested")),
                "position_in_queue": index + 1,
                "duration_seconds": item.get("duration"),
                "estimated_wait_seconds": estimated_wait_seconds,
            }
    return None


def current_wait() -> Tuple[int, float]:
    conn = db.get_conn()
    items = _items_ordered(conn)
    return len(items), sum(_remaining_seconds(item) for item in items)


def list_queue() -> list:
    conn = db.get_conn()
    items = _items_ordered(conn)
    result = []
    songs_ahead = []
    for index, item in enumerate(items):
        if item.get("skip_requested") and item["status"] != "playing":
            continue
        estimated_wait_seconds = sum(_remaining_seconds(s) for s in songs_ahead)
        result.append({
            "id": item["id"],
            "url": item["url"],
            "title": item.get("title"),
            "uploader": item.get("uploader"),
            "status": item["status"],
            "position_in_queue": index + 1,
            "duration_seconds": item.get("duration"),
            "estimated_wait_seconds": estimated_wait_seconds,
            "source": item.get("source"),
            "dedication": item.get("dedication"),
        })
        songs_ahead.append(item)
    return result


def get_playing_progress() -> Optional[dict]:
    conn = db.get_conn()
    row = conn.execute(
        "SELECT * FROM queue_items WHERE status='playing' LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    return {
        "elapsed_seconds": round(_elapsed_seconds(item), 1),
        "duration_seconds": item.get("duration"),
        "is_paused": item.get("paused_at") is not None,
    }


def has_pending_songs() -> bool:
    conn = db.get_conn()
    row = conn.execute("SELECT COUNT(*) FROM queue_items").fetchone()
    return row[0] > 0


def get_next_queued() -> Optional[dict]:
    conn = db.get_conn()
    row = conn.execute(
        "SELECT * FROM queue_items WHERE status='queued' ORDER BY position LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "url": row["url"],
        "title": row["title"],
        "duration": row["duration"],
        "dedication": row["dedication"],
    }


def mark_skip_requested(song_id: str) -> Optional[str]:
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT status FROM queue_items WHERE id=?", (song_id,)
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE queue_items SET skip_requested=1 WHERE id=?", (song_id,)
        )
    return row["status"]


def is_skip_requested(song_id: str) -> bool:
    conn = db.get_conn()
    row = conn.execute(
        "SELECT skip_requested FROM queue_items WHERE id=?", (song_id,)
    ).fetchone()
    return bool(row["skip_requested"]) if row else False


def mark_playing(song_id: str) -> None:
    played_item = None
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM queue_items WHERE id=?", (song_id,)
        ).fetchone()
        if row:
            now = time.time()
            conn.execute(
                "UPDATE queue_items SET status='playing', started_at=?, "
                "seek_offset=0, paused_duration=0, paused_at=NULL WHERE id=?",
                (now, song_id),
            )
            played_item = dict(row)
            played_item["status"] = "playing"
            played_item["started_at"] = now
            _append_to_history(conn, played_item)
    if played_item is not None:
        analytics.record_play(played_item)


def mark_paused(song_id: str) -> None:
    conn = db.get_conn()
    conn.execute(
        "UPDATE queue_items SET paused_at=? WHERE id=? AND paused_at IS NULL",
        (time.time(), song_id),
    )


def mark_resumed(song_id: str) -> None:
    now = time.time()
    conn = db.get_conn()
    conn.execute(
        "UPDATE queue_items SET "
        "paused_duration = paused_duration + (? - paused_at), "
        "paused_at = NULL "
        "WHERE id = ? AND paused_at IS NOT NULL",
        (now, song_id),
    )


def mark_seeked(song_id: str, offset: float) -> None:
    conn = db.get_conn()
    conn.execute(
        "UPDATE queue_items SET seek_offset=?, started_at=?, "
        "paused_duration=0, paused_at=NULL WHERE id=?",
        (offset, time.time(), song_id),
    )


def _append_to_history(conn, item: dict) -> None:
    url = item["url"]
    video_id = item.get("video_id")
    key = _video_key(url, video_id)

    existing = conn.execute("SELECT id, url, video_id FROM history").fetchall()
    for row in existing:
        if _video_key(row["url"], row["video_id"]) == key:
            conn.execute("DELETE FROM history WHERE id=?", (row["id"],))

    conn.execute(
        "INSERT INTO history "
        "(url, video_id, title, uploader, duration, source, played_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            url, video_id, item.get("title"), item.get("uploader"),
            item.get("duration"), item.get("source"), time.time(),
        ),
    )

    count = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    if count > 100:
        conn.execute(
            "DELETE FROM history WHERE id IN "
            "(SELECT id FROM history ORDER BY played_at ASC LIMIT ?)",
            (count - 100,),
        )


def get_history(page: int = 1, per_page: int = 10, q: str = "") -> dict:
    conn = db.get_conn()
    songs = [dict(r) for r in conn.execute(
        "SELECT * FROM history ORDER BY played_at DESC"
    ).fetchall()]

    if q:
        ql = q.lower()
        songs = [
            s for s in songs
            if ql in (s.get("title") or "").lower()
            or ql in (s.get("uploader") or "").lower()
        ]

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
    with db.transaction() as conn:
        max_non_queued = conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM queue_items "
            "WHERE status != 'queued'"
        ).fetchone()[0]

        queued = conn.execute(
            "SELECT id FROM queue_items WHERE status='queued' ORDER BY position"
        ).fetchall()
        queued_ids = {r["id"] for r in queued}

        new_order = [sid for sid in ordered_ids if sid in queued_ids]
        included = set(new_order)
        for r in queued:
            if r["id"] not in included:
                new_order.append(r["id"])

        for i, sid in enumerate(new_order):
            conn.execute(
                "UPDATE queue_items SET position=? WHERE id=?",
                (max_non_queued + 1 + i, sid),
            )


def bump_to_front(song_id: str) -> bool:
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT id FROM queue_items WHERE id=? AND status='queued'",
            (song_id,),
        ).fetchone()
        if row is None:
            return False

        min_queued_pos = conn.execute(
            "SELECT COALESCE(MIN(position), 0) FROM queue_items "
            "WHERE status='queued'"
        ).fetchone()[0]

        conn.execute(
            "UPDATE queue_items SET position=? WHERE id=?",
            (min_queued_pos - 1, song_id),
        )
    return True


def mark_done(song_id: str) -> None:
    conn = db.get_conn()
    conn.execute("DELETE FROM queue_items WHERE id=?", (song_id,))


def clear_queued() -> int:
    conn = db.get_conn()
    cursor = conn.execute("DELETE FROM queue_items WHERE status='queued'")
    return cursor.rowcount


def reset_stale_playing() -> None:
    conn = db.get_conn()
    conn.execute(
        "UPDATE queue_items SET status='queued', started_at=NULL, "
        "paused_at=NULL, paused_duration=0, seek_offset=0 "
        "WHERE status='playing'"
    )


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


def format_duration_hm(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    seconds = int(round(seconds))
    minutes, _ = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
