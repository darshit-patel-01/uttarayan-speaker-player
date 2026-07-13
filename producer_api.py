import asyncio
import json
import logging
import secrets
from typing import List, Optional

from confluent_kafka import Producer
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

import analytics
from config import settings
import default_playlist
import runtime_config
from playback import (
    get_volume, set_volume,
    is_paused, is_stopped,
    request_pause, request_resume, request_seek, request_skip, request_stop,
)
import queue_state
from real_time_validation import (
    blacklist,
    detect_requester_id,
    detect_source,
    extract_video_id,
    is_valid_youtube_url,
    validate_song_request,
)
from real_time_validation.content import validate_song_content

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("producer_api")

app = FastAPI(title="YouTube Audio Queue")

_producer: Optional[Producer] = None


def get_producer() -> Producer:
    global _producer
    if _producer is None:
        _producer = Producer({"bootstrap.servers": settings.kafka_bootstrap_servers})
    return _producer


security = HTTPBasic()
optional_security = HTTPBasic(auto_error=False)


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    valid_username = secrets.compare_digest(credentials.username, settings.admin_username)
    valid_password = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (valid_username and valid_password):
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    return credentials.username


def optional_admin(credentials: Optional[HTTPBasicCredentials] = Depends(optional_security)) -> Optional[str]:
    """
    Like require_admin, but for endpoints that are open to everyone: returns
    the username if valid admin credentials were sent, None if none were
    sent, and 401s only if credentials were sent but are wrong (so a typo'd
    admin login doesn't silently fall back to being treated as a stranger).
    """
    if credentials is None:
        return None
    valid_username = secrets.compare_digest(credentials.username, settings.admin_username)
    valid_password = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (valid_username and valid_password):
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    return credentials.username


class EnqueueRequest(BaseModel):
    urls: List[str]

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, urls: List[str]) -> List[str]:
        # Cheap, no-network check: is this even shaped like a YouTube URL?
        # Content-level checks (adult / song-only) happen in the endpoint,
        # since they require probing YouTube and should be reported per-URL
        # rather than failing the whole request.
        if not urls:
            raise ValueError("urls must contain at least one YouTube URL")
        for url in urls:
            if not is_valid_youtube_url(url):
                raise ValueError(f"Not a valid YouTube URL: {url}")
        return urls


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/history")
def history(page: int = 1, per_page: int = 10, q: str = ""):
    """
    Returns paginated playback history (last 100 played songs), newest first.
    per_page must be one of 5, 10, or 20. Optional q filters by title/uploader.
    """
    if per_page not in (5, 10, 20):
        raise HTTPException(status_code=422, detail="per_page must be 5, 10, or 20")
    result = queue_state.get_history(page=page, per_page=per_page, q=q)
    for song in result["songs"]:
        song["duration_fmt"] = queue_state.format_duration(song.get("duration"))
    return result


@app.post("/bump/{song_id}")
def bump_song(song_id: str, admin: str = Depends(require_admin)):
    """Moves a queued song to play next, right after the currently playing song."""
    success = queue_state.bump_to_front(song_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"No queued song with id {song_id}")
    logger.info("Song %s bumped to front by %s", song_id, admin)
    return {"status": "bumped"}


@app.get("/wait-time")
def wait_time():
    """
    Current estimated wait time for a song enqueued right now — the summed
    remaining duration of everything currently queued/playing. Public (no
    admin needed) since it's what anyone considering enqueueing wants to see.
    """
    queue_length, estimated_wait_seconds = queue_state.current_wait()
    return {
        "queue_length": queue_length,
        "estimated_wait_seconds": round(estimated_wait_seconds),
        "estimated_wait": queue_state.format_duration(estimated_wait_seconds),
    }


import time as _time


def _song_summary(song: dict, source: str, progress: Optional[dict] = None) -> dict:
    result = {
        "id": song.get("id"),
        "title": song.get("title"),
        "uploader": song.get("uploader"),
        "url": song["url"],
        "source": source,
    }
    if progress:
        result.update(progress)
    return result


def _default_playlist_progress(np: dict) -> dict:
    """Compute elapsed/duration/is_paused from a default-playlist now_playing entry."""
    seek_offset = np.get("seek_offset") or 0
    started_at = np.get("started_at") or _time.time()
    paused_duration = np.get("paused_duration") or 0
    paused_at = np.get("paused_at")
    duration = np.get("duration")

    elapsed = seek_offset + (_time.time() - started_at) - paused_duration
    if paused_at:
        elapsed -= (_time.time() - paused_at)
    elapsed = max(0.0, elapsed)
    if duration:
        elapsed = min(elapsed, duration)

    return {
        "elapsed_seconds": round(elapsed, 1),
        "duration_seconds": duration,
        "is_paused": paused_at is not None,
    }


def _now_playing_payload() -> dict:
    """
    What's playing right now and what plays next, whether that's a real
    (enqueued) song or a default-playlist fallback song. Progress fields
    (elapsed_seconds, duration_seconds, is_paused) are included for the
    playing song so the UI can render a seek bar. Shared by GET
    /now-playing and the WebSocket broadcaster below so both ways of
    getting this data can never drift apart.
    """
    songs = queue_state.list_queue()
    playing = next((s for s in songs if s["status"] == "playing"), None)
    upcoming = [s for s in songs if s["status"] == "queued"]

    if playing is not None:
        progress = queue_state.get_playing_progress()
        playing_summary = _song_summary(playing, "queue", progress)
    else:
        fallback_playing = default_playlist.get_now_playing()
        if fallback_playing:
            progress = _default_playlist_progress(fallback_playing)
            playing_summary = _song_summary(fallback_playing, "playlist", progress)
        else:
            playing_summary = None

    if upcoming:
        next_summary = _song_summary(upcoming[0], "queue")
    else:
        fallback_next = default_playlist.peek_next_song()
        next_summary = _song_summary(fallback_next, "playlist") if fallback_next else None

    return {"playing": playing_summary, "next": next_summary}


@app.get("/now-playing")
def now_playing():
    """
    Public — this is meant to be shown on the main UI for anyone to see, no
    login needed. See WebSocket /ws/now-playing for the live-push version
    of the same data (used by the frontend instead of polling this).
    """
    return _now_playing_payload()


# ---------------------------------------------------------------------------
# WebSocket live updates — replaces client-side polling of GET /now-playing.
# A single background loop recomputes the payload once a second and pushes
# it to every connected client, so song changes, skips, pauses, seeks, and
# queue changes all show up within one second regardless of how many
# browser tabs are watching, without each tab making its own HTTP round trip.
# ---------------------------------------------------------------------------
NOW_PLAYING_BROADCAST_INTERVAL_SECONDS = 1

_ws_clients: set = set()


async def _broadcast_now_playing() -> None:
    if not _ws_clients:
        return
    payload = await asyncio.to_thread(_now_playing_payload)
    message = json.dumps(payload)
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


async def _now_playing_broadcast_loop() -> None:
    while True:
        try:
            await _broadcast_now_playing()
        except Exception:
            logger.exception("now-playing broadcast loop failed")
        await asyncio.sleep(NOW_PLAYING_BROADCAST_INTERVAL_SECONDS)


@app.on_event("startup")
async def _start_broadcast_loop() -> None:
    asyncio.create_task(_now_playing_broadcast_loop())


@app.websocket("/ws/now-playing")
async def ws_now_playing(websocket: WebSocket) -> None:
    """
    Live-push version of GET /now-playing. Sends the current payload
    immediately on connect, then again every
    NOW_PLAYING_BROADCAST_INTERVAL_SECONDS for as long as the client stays
    connected. Public, same as the GET endpoint — no admin login needed.
    """
    await websocket.accept()
    _ws_clients.add(websocket)
    try:
        payload = await asyncio.to_thread(_now_playing_payload)
        await websocket.send_text(json.dumps(payload))
        while True:
            # The client never sends anything; this just blocks until the
            # connection closes so we notice disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)


@app.post("/login")
def login(admin: str = Depends(require_admin)):
    """Validates admin credentials. Returns 401 (via require_admin) if they're wrong."""
    return {"status": "ok", "username": admin}


@app.get("/volume")
def volume_get(admin: str = Depends(require_admin)):
    """Returns the current volume as a 0–100 integer percentage."""
    return {"volume": round(get_volume() * 100)}


class VolumeRequest(BaseModel):
    volume: int  # 0–100

    @field_validator("volume")
    @classmethod
    def validate_volume(cls, v: int) -> int:
        if not (0 <= v <= 100):
            raise ValueError("volume must be between 0 and 100")
        return v


@app.post("/volume")
def volume_set(req: VolumeRequest, admin: str = Depends(require_admin)):
    """Sets playback volume (0–100%). Takes effect within ~200 ms without stopping the song."""
    level = req.volume / 100.0
    set_volume(level)
    logger.info("Volume set to %d%% by %s", req.volume, admin)
    return {"volume": req.volume}


@app.post("/pause")
def pause(admin: str = Depends(require_admin)):
    """Pauses the currently playing track."""
    request_pause()
    logger.info("Pause requested via API by %s", admin)
    return {"status": "paused"}


@app.post("/resume")
def resume(admin: str = Depends(require_admin)):
    """Resumes a paused or stopped track."""
    request_resume()  # clears both pause and stop signals
    logger.info("Resume requested via API by %s", admin)
    return {"status": "resumed"}


@app.post("/stop")
def stop(admin: str = Depends(require_admin)):
    """
    Stops all playback immediately and blocks the consumer from auto-advancing
    to the next song. POST /resume to start playing again.
    """
    request_stop()
    request_skip()   # kills the currently playing song
    logger.info("Stop requested via API by %s", admin)
    return {"status": "stopped"}


class SeekRequest(BaseModel):
    seconds: float


@app.post("/seek")
def seek(req: SeekRequest, admin: str = Depends(require_admin)):
    """
    Seeks to `seconds` into the currently playing track. The track restarts
    playback from that position. Clears any pause state (seek always plays).
    """
    if req.seconds < 0:
        raise HTTPException(status_code=422, detail="seconds must be >= 0")
    request_seek(req.seconds)
    logger.info("Seek to %.1fs requested via API by %s", req.seconds, admin)
    return {"status": "seek requested", "seconds": req.seconds}


@app.get("/playback-status")
def playback_status(admin: str = Depends(require_admin)):
    """Returns the current paused/stopped state."""
    return {"paused": is_paused(), "stopped": is_stopped()}


@app.post("/skip")
def skip(admin: str = Depends(require_admin)):
    """
    Signals the consumer to stop whatever is currently playing and move on
    to the next song in the queue. If nothing is currently playing, this has
    no effect (the signal is discarded before the next track starts).
    """
    request_skip()
    logger.info("Skip requested via API by %s", admin)
    return {"status": "skip requested"}


@app.post("/skip/{song_id}")
def skip_song(song_id: str, admin: str = Depends(require_admin)):
    """
    Skips a specific song by ID. If it's the one currently playing, this
    stops it immediately (same as POST /skip); if it's still queued, it's
    marked to be skipped without ever playing once its turn comes.
    """
    song_status = queue_state.mark_skip_requested(song_id)
    if song_status is None:
        raise HTTPException(status_code=404, detail=f"No song with id {song_id} (unknown, or already finished)")

    if song_status == "playing":
        request_skip()

    logger.info("Skip requested for id=%s (status=%s) via API by %s", song_id, song_status, admin)
    return {"status": "skip requested", "song_status": song_status}


class ReorderQueueRequest(BaseModel):
    song_ids: List[str]


@app.put("/queue/reorder")
def reorder_queue_endpoint(req: ReorderQueueRequest, admin: str = Depends(require_admin)):
    """Reorders the queued songs (not the currently playing one) to the given order."""
    queue_state.reorder_queue(req.song_ids)
    logger.info("Queue reordered by %s", admin)
    return {"status": "reordered"}


@app.get("/queue")
def queue(admin: str = Depends(require_admin)):
    """Lists every song currently queued, in play order, with position and estimated wait."""
    songs = queue_state.list_queue()
    for song in songs:
        song["duration"] = queue_state.format_duration(song["duration_seconds"])
        song["estimated_wait"] = queue_state.format_duration(song["estimated_wait_seconds"])
    return {"queue": songs}


# ---------------------------------------------------------------------------
# Blacklist (admin-only) — banned video IDs and banned requesters (phone
# numbers / Telegram ids / IPs). Enforced first in real_time_validation for
# every non-admin enqueue, regardless of source (web / WhatsApp / Telegram).
# ---------------------------------------------------------------------------
BLACKLIST_SOURCES = ("whatsapp", "telegram", "ip")


class BlacklistVideoRequest(BaseModel):
    # Accepts either a bare video ID or a full YouTube URL (normalized below).
    video_id: str

    @field_validator("video_id")
    @classmethod
    def normalize(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("video_id must not be empty")
        return extract_video_id(value) or value


class BlacklistRequesterRequest(BaseModel):
    source: str   # "whatsapp" | "telegram" | "ip"
    value: str    # phone number / Telegram user id / IP address

    @field_validator("source")
    @classmethod
    def validate_source(cls, source: str) -> str:
        source = source.strip().lower()
        if source not in BLACKLIST_SOURCES:
            raise ValueError(f"source must be one of {', '.join(BLACKLIST_SOURCES)}")
        return source

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value

    def requester_key(self) -> str:
        return f"{self.source}:{self.value}"


def _split_requester_key(key: str) -> dict:
    """Splits a stored 'source:value' key back into parts for the UI."""
    source, _, value = key.partition(":")
    return {"key": key, "source": source, "value": value}


@app.get("/blacklist")
def get_blacklist(admin: str = Depends(require_admin)):
    """Returns the current blacklist: banned video IDs and requesters."""
    data = blacklist.list_all()
    return {
        "video_ids": data["video_ids"],
        "requesters": [_split_requester_key(k) for k in data["requesters"]],
    }


@app.post("/blacklist/video")
def blacklist_add_video(req: BlacklistVideoRequest, admin: str = Depends(require_admin)):
    """Bans a YouTube video ID from being enqueued by non-admins."""
    added = blacklist.add_video(req.video_id)
    logger.info("Blacklist: video %s %s by %s", req.video_id, "added" if added else "already present", admin)
    return {"status": "added" if added else "already_present", "video_id": req.video_id}


@app.delete("/blacklist/video/{video_id}")
def blacklist_remove_video(video_id: str, admin: str = Depends(require_admin)):
    """Un-bans a previously blacklisted video ID."""
    removed = blacklist.remove_video(video_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Video {video_id} is not blacklisted")
    logger.info("Blacklist: video %s removed by %s", video_id, admin)
    return {"status": "removed", "video_id": video_id}


@app.post("/blacklist/requester")
def blacklist_add_requester(req: BlacklistRequesterRequest, admin: str = Depends(require_admin)):
    """Bans a requester (phone number / Telegram id / IP) from enqueueing."""
    key = req.requester_key()
    added = blacklist.add_requester(key)
    logger.info("Blacklist: requester %s %s by %s", key, "added" if added else "already present", admin)
    return {"status": "added" if added else "already_present", "requester": _split_requester_key(key)}


@app.delete("/blacklist/requester")
def blacklist_remove_requester(source: str, value: str, admin: str = Depends(require_admin)):
    """Un-bans a requester. Pass the same source + value used to add it."""
    key = f"{source.strip().lower()}:{value.strip()}"
    removed = blacklist.remove_requester(key)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Requester {key} is not blacklisted")
    logger.info("Blacklist: requester %s removed by %s", key, admin)
    return {"status": "removed", "requester": _split_requester_key(key)}


# ---------------------------------------------------------------------------
# Admin dashboard stats — aggregated from the append-only analytics log
# (one event per real-queue song that started playing). Default-playlist
# songs are excluded (they aren't anyone's request).
# ---------------------------------------------------------------------------
@app.get("/stats")
def stats(admin: str = Depends(require_admin)):
    """
    Dashboard numbers: total plays + playtime, playtime/plays grouped by
    source (whatsapp / telegram / web), the top requesters, and the
    most-requested songs. Durations are also returned pre-formatted.
    """
    data = analytics.get_stats()
    data["total_playtime"] = queue_state.format_duration(data["total_playtime_seconds"])
    for row in data["by_source"]:
        row["playtime"] = queue_state.format_duration(row["playtime_seconds"])
    for row in data["top_requesters"]:
        row["playtime"] = queue_state.format_duration(row["playtime_seconds"])
    return data


# ---------------------------------------------------------------------------
# Runtime config (admin-only) — tunable settings (rate limit, queue-wait cap,
# max duration, normalization, crossfade) that both the API and consumer read
# live, so changes take effect without a restart. See runtime_config.py.
# ---------------------------------------------------------------------------
class ConfigUpdateRequest(BaseModel):
    changes: dict


@app.get("/config")
def get_config(admin: str = Depends(require_admin)):
    """Every tunable setting with its current value, default, and UI metadata."""
    return {"settings": runtime_config.get_all()}


@app.put("/config")
def update_config(req: ConfigUpdateRequest, admin: str = Depends(require_admin)):
    """Apply a batch of setting changes. 422 on an unknown key / out-of-range value."""
    try:
        updated = runtime_config.update(req.changes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info("Config updated by %s: %s", admin, req.changes)
    return {"settings": updated}


@app.post("/config/reset")
def reset_config(admin: str = Depends(require_admin)):
    """Clear all overrides — every setting falls back to its .env default."""
    logger.info("Config reset to defaults by %s", admin)
    return {"settings": runtime_config.reset()}


class CreatePlaylistRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str) -> str:
        name = name.strip()
        if not name:
            raise ValueError("name must not be empty")
        return name


@app.get("/playlists")
def get_playlists(admin: str = Depends(require_admin)):
    """
    Lists the admin-managed fallback playlists. Whichever one is active is
    what the consumer loops through (in order, wrapping at the end)
    whenever the real queue is empty; any real song enqueued interrupts it
    almost immediately.
    """
    return {"playlists": default_playlist.list_playlists()}


@app.post("/playlists")
def create_playlist(req: CreatePlaylistRequest, admin: str = Depends(require_admin)):
    playlist = default_playlist.create_playlist(req.name)
    logger.info("Playlist created: %s (%s) by %s", playlist["name"], playlist["id"], admin)
    return playlist


@app.delete("/playlists/{playlist_id}")
def delete_playlist(playlist_id: str, admin: str = Depends(require_admin)):
    removed = default_playlist.delete_playlist(playlist_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No playlist with id {playlist_id}")
    logger.info("Playlist deleted: %s by %s", playlist_id, admin)
    return {"status": "deleted"}


@app.post("/playlists/{playlist_id}/activate")
def activate_playlist(playlist_id: str, admin: str = Depends(require_admin)):
    """Marks this playlist as the one the consumer plays when the real queue is empty."""
    activated = default_playlist.set_active_playlist(playlist_id)
    if not activated:
        raise HTTPException(status_code=404, detail=f"No playlist with id {playlist_id}")
    logger.info("Playlist activated: %s by %s", playlist_id, admin)
    return {"status": "activated"}


@app.post("/playlists/{playlist_id}/deactivate")
def deactivate_playlist(playlist_id: str, admin: str = Depends(require_admin)):
    """
    Clears the active playlist so the consumer plays nothing while the real
    queue is empty. 404 if this playlist isn't the currently active one
    (covers both "unknown id" and "some other playlist is active").
    """
    deactivated = default_playlist.deactivate_playlist(playlist_id)
    if not deactivated:
        raise HTTPException(status_code=404, detail=f"Playlist {playlist_id} is not the active playlist")
    logger.info("Playlist deactivated: %s by %s", playlist_id, admin)
    return {"status": "deactivated"}


@app.get("/playlists/{playlist_id}/songs")
def get_playlist_songs(playlist_id: str, admin: str = Depends(require_admin)):
    songs = default_playlist.list_songs(playlist_id)
    if songs is None:
        raise HTTPException(status_code=404, detail=f"No playlist with id {playlist_id}")
    for song in songs:
        song["duration"] = queue_state.format_duration(song.get("duration"))
    return {"songs": songs}


@app.post("/playlists/{playlist_id}/songs")
def add_playlist_songs(playlist_id: str, req: EnqueueRequest, admin: str = Depends(require_admin)):
    """Appends one or more songs to a playlist. Admin-only, so content checks (age/category/duration) are skipped."""
    added = []
    rejected = []
    for url in req.urls:
        is_valid, reason, metadata = validate_song_content(url, skip_content_checks=True)
        if not is_valid:
            rejected.append({"url": url, "reason": reason})
            continue
        item = default_playlist.add_song(
            playlist_id, url, metadata.get("title"), metadata.get("uploader"), metadata.get("duration")
        )
        if item is None:
            raise HTTPException(status_code=404, detail=f"No playlist with id {playlist_id}")
        if item.get("duplicate"):
            rejected.append({"url": url, "reason": "Song already in this playlist"})
            continue
        item["duration"] = queue_state.format_duration(item.get("duration"))
        added.append(item)

    logger.info(
        "Playlist %s: added %d, rejected %d (by %s)", playlist_id, len(added), len(rejected), admin
    )
    return {"added": added, "rejected": rejected}


class ReorderSongsRequest(BaseModel):
    song_ids: List[str]


@app.put("/playlists/{playlist_id}/songs/reorder")
def reorder_playlist_songs(playlist_id: str, req: ReorderSongsRequest, admin: str = Depends(require_admin)):
    """Reorders a playlist's songs to match the provided ordered list of song IDs."""
    success = default_playlist.reorder_songs(playlist_id, req.song_ids)
    if not success:
        raise HTTPException(status_code=404, detail=f"No playlist with id {playlist_id}")
    logger.info("Playlist %s: reordered songs (by %s)", playlist_id, admin)
    return {"status": "reordered"}


@app.delete("/playlists/{playlist_id}/songs/{song_id}")
def remove_playlist_song(playlist_id: str, song_id: str, admin: str = Depends(require_admin)):
    removed = default_playlist.remove_song(playlist_id, song_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No song with id {song_id} in playlist {playlist_id}")
    logger.info("Playlist %s: removed song %s (by %s)", playlist_id, song_id, admin)
    return {"status": "removed"}


@app.get("/status/{song_id}")
def status(song_id: str):
    """
    Looks up a previously enqueued song by its ID and reports its current
    status, position in queue, and estimated wait time. 404 if the ID is
    unknown (never enqueued, or already finished playing).
    """
    song = queue_state.get_status(song_id)
    if song is None:
        raise HTTPException(status_code=404, detail=f"No song with id {song_id} (unknown, or already finished)")

    song["estimated_wait"] = queue_state.format_duration(song["estimated_wait_seconds"])
    return song


@app.post("/enqueue")
def enqueue(req: EnqueueRequest, request: Request, admin: Optional[str] = Depends(optional_admin)):
    """
    Accepts one or more YouTube URLs. Each is run through
    real_time_validation.validate_song_request() and only pushed onto the
    Kafka queue if it passes: not already queued, within the requester's
    rate limit, not age-restricted, actually a music video, and under the
    duration limit.

    URLs that fail validation are skipped and reported back in "rejected"
    instead of failing the whole request. Each accepted song gets an
    incremental ID plus its position in the queue and an estimated wait time
    (the summed duration of every song currently ahead of it).

    Requests authenticated as admin skip all of the above checks (metadata
    is still probed, so queue/wait-time accounting is unaffected).
    """
    producer = get_producer()
    enqueued = []
    rejected = []
    delivery_failures = []  # keyed by song_id
    is_admin = admin is not None
    source = detect_source(request)
    requester_id = detect_requester_id(request, source)

    def _on_delivery(err, msg, song_id, url):
        if err is not None:
            delivery_failures.append({"id": song_id, "url": url, "reason": f"Kafka delivery failed: {err}"})

    for url in req.urls:
        result = validate_song_request(url, requester_id=requester_id, is_admin=is_admin)
        if not result.is_valid:
            rejected.append({"url": url, "reason": result.reason})
            continue

        duration = result.metadata.get("duration")
        title = result.metadata.get("title")
        uploader = result.metadata.get("uploader")

        song_id, position, wait_seconds = queue_state.add_song(
            url, duration, title, uploader, result.video_id,
            source=source, requester_id=requester_id,
        )

        try:
            producer.produce(
                settings.kafka_topic,
                value=json.dumps({"id": song_id, "url": url}).encode("utf-8"),
                callback=lambda err, msg, song_id=song_id, url=url: _on_delivery(err, msg, song_id, url),
            )
            producer.poll(0)
            enqueued.append(
                {
                    "id": song_id,
                    "url": url,
                    "title": title,
                    "uploader": uploader,
                    "source": source,
                    "position_in_queue": position,
                    "duration_seconds": duration,
                    "duration": queue_state.format_duration(duration),
                    "estimated_wait_seconds": round(wait_seconds),
                    "estimated_wait": queue_state.format_duration(wait_seconds),
                }
            )
        except Exception as exc:
            logger.exception("Failed to enqueue %s", url)
            queue_state.mark_done(song_id)
            rejected.append({"url": url, "reason": f"Kafka error: {exc}"})

    try:
        producer.flush(10)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Kafka flush error: {exc}") from exc

    # Anything that failed delivery during flush should move from enqueued to
    # rejected, and be removed from the shared queue state since it never
    # actually made it onto the Kafka queue.
    if delivery_failures:
        failed_ids = {d["id"] for d in delivery_failures}
        enqueued = [item for item in enqueued if item["id"] not in failed_ids]
        for failure in delivery_failures:
            queue_state.mark_done(failure["id"])
            rejected.append({"url": failure["url"], "reason": failure["reason"]})

    logger.info("Enqueued %d url(s), rejected %d", len(enqueued), len(rejected))
    return {"enqueued": enqueued, "rejected": rejected}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
