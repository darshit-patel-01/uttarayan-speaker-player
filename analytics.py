"""
Append-only play-analytics log powering the admin dashboard.

One event is recorded the moment a real-queue song starts playing (from
queue_state.mark_playing — the same hook that writes history, but unlike
history this log is NOT deduped: every play is its own event, which is what
lets us count "most-requested song" and "who requested the most"). Default-
playlist songs are not recorded — they aren't anyone's request.

Same file-based IPC pattern as queue_state.py / default_playlist.py: written
by the consumer process (mark_playing), read by the API process (GET /stats).
The file holds requester ids (which include phone numbers), so it's
gitignored and only ever exposed through the admin-only /stats endpoint.
"""
import json
import os
import threading
import time
from collections import defaultdict
from typing import Optional

from config import settings

_lock = threading.Lock()


def _empty() -> dict:
    return {"events": []}


def _load() -> dict:
    if not os.path.exists(settings.analytics_file):
        return _empty()
    try:
        with open(settings.analytics_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("events", [])
            return data
    except (json.JSONDecodeError, OSError):
        return _empty()


def _save(data: dict) -> None:
    tmp_path = settings.analytics_file + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, settings.analytics_file)


def record_play(item: dict) -> None:
    """Record one play. `item` is a queue_state song item (has url, video_id,
    title, uploader, duration, source, requester_id)."""
    with _lock:
        data = _load()
        data["events"].append({
            "played_at": time.time(),
            "video_id": item.get("video_id"),
            "url": item.get("url"),
            "title": item.get("title"),
            "uploader": item.get("uploader"),
            "duration": item.get("duration") or 0,
            "source": item.get("source"),
            "requester_id": item.get("requester_id"),
        })
        data["events"] = data["events"][-settings.analytics_max_events:]
        _save(data)


def _split_requester(requester_id: Optional[str]) -> dict:
    """'whatsapp:15551234567' -> {source, value}. Unknown ids fall back to
    source 'unknown'."""
    if not requester_id:
        return {"source": "unknown", "value": ""}
    source, _, value = requester_id.partition(":")
    return {"source": source or "unknown", "value": value}


def get_stats(top_n: int = 10) -> dict:
    """Aggregates the event log into the numbers the dashboard shows."""
    with _lock:
        events = list(_load()["events"])

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
            # Keep the latest-seen title/url for display.
            song_titles[video_id] = {"title": e.get("title"), "url": e.get("url")}

    top_requesters = []
    for requester_id, count in sorted(requester_counts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]:
        parts = _split_requester(requester_id)
        top_requesters.append({
            "source": parts["source"],
            "value": parts["value"],
            "count": count,
            "playtime_seconds": round(requester_playtime[requester_id]),
        })

    top_songs = []
    for video_id, count in sorted(song_counts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]:
        info = song_titles.get(video_id, {})
        top_songs.append({
            "video_id": video_id,
            "title": info.get("title"),
            "url": info.get("url"),
            "count": count,
        })

    by_source = []
    for source in sorted(playtime_by_source, key=lambda s: playtime_by_source[s], reverse=True):
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
