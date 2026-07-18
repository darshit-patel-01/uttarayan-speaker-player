"""
Append-only play-analytics log powering the admin dashboard.

One event is recorded the moment a real-queue song starts playing (from
queue_state.mark_playing). Default-playlist songs are not recorded.

Uses SQLite (via db module) for persistent storage.
"""
import time
from collections import defaultdict
from typing import Optional

import db
from config import settings


def record_play(item: dict) -> None:
    """Record one play. `item` is a queue_state song item."""
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO analytics_events "
        "(played_at, video_id, url, title, uploader, duration, source, requester_id) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            time.time(), item.get("video_id"), item.get("url"),
            item.get("title"), item.get("uploader"),
            item.get("duration") or 0, item.get("source"),
            item.get("requester_id"),
        ),
    )
    count = conn.execute("SELECT COUNT(*) FROM analytics_events").fetchone()[0]
    if count > settings.analytics_max_events:
        excess = count - settings.analytics_max_events
        conn.execute(
            "DELETE FROM analytics_events WHERE id IN "
            "(SELECT id FROM analytics_events ORDER BY id ASC LIMIT ?)",
            (excess,),
        )


def _split_requester(requester_id: Optional[str]) -> dict:
    if not requester_id:
        return {"source": "unknown", "value": ""}
    source, _, value = requester_id.partition(":")
    return {"source": source or "unknown", "value": value}


def get_stats(top_n: int = 10) -> dict:
    """Aggregates the event log into the numbers the dashboard shows."""
    conn = db.get_conn()
    events = [dict(r) for r in conn.execute(
        "SELECT * FROM analytics_events"
    ).fetchall()]

    total_plays = len(events)
    total_playtime = 0.0
    playtime_by_source: dict = defaultdict(float)
    plays_by_source: dict = defaultdict(int)
    requester_counts: dict = defaultdict(int)
    requester_playtime: dict = defaultdict(float)
    song_counts: dict = defaultdict(int)
    song_titles: dict = {}

    for e in events:
        duration = e.get("duration") or 0
        source = e.get("source") or "unknown"
        total_playtime += duration
        playtime_by_source[source] += duration
        plays_by_source[source] += 1

        requester_id = e.get("requester_id")
        if requester_id:
            requester_counts[requester_id] += 1
            requester_playtime[requester_id] += duration

        video_id = e.get("video_id") or e.get("url")
        if video_id:
            song_counts[video_id] += 1
            song_titles[video_id] = {"title": e.get("title"), "url": e.get("url")}

    top_requesters = []
    for requester_id, count in sorted(
        requester_counts.items(), key=lambda kv: kv[1], reverse=True
    )[:top_n]:
        parts = _split_requester(requester_id)
        top_requesters.append({
            "source": parts["source"],
            "value": parts["value"],
            "count": count,
            "playtime_seconds": round(requester_playtime[requester_id]),
        })

    top_songs = []
    for video_id, count in sorted(
        song_counts.items(), key=lambda kv: kv[1], reverse=True
    )[:top_n]:
        info = song_titles.get(video_id, {})
        top_songs.append({
            "video_id": video_id,
            "title": info.get("title"),
            "url": info.get("url"),
            "count": count,
        })

    by_source = []
    for source in sorted(
        playtime_by_source, key=lambda s: playtime_by_source[s], reverse=True
    ):
        by_source.append({
            "source": source,
            "plays": plays_by_source[source],
            "playtime_seconds": round(playtime_by_source[source]),
        })

    return {
        "total_plays": total_plays,
        "total_playtime_seconds": round(total_playtime),
        "by_source": by_source,
        "top_requesters": top_requesters,
        "top_songs": top_songs,
    }
