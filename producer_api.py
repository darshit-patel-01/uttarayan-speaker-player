import json
import logging
import re
import secrets
from typing import List, Optional

from confluent_kafka import Producer
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from config import settings
import default_playlist
from playback import request_skip
import queue_state
from validators import validate_song_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("producer_api")

app = FastAPI(title="YouTube Audio Queue")

_producer: Optional[Producer] = None

YOUTUBE_URL_RE = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[\w-]+"
)

# Same URL shapes as YOUTUBE_URL_RE, but captures the video ID so different
# URLs pointing at the same video (e.g. with/without a "?si=" share token)
# can be recognized as duplicates.
VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([\w-]+)"
)


def extract_video_id(url: str) -> Optional[str]:
    match = VIDEO_ID_RE.search(url)
    return match.group(1) if match else None


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
            if not YOUTUBE_URL_RE.match(url):
                raise ValueError(f"Not a valid YouTube URL: {url}")
        return urls


@app.get("/health")
def health():
    return {"status": "ok"}


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


def _song_summary(song: dict, source: str) -> dict:
    return {
        "title": song.get("title"),
        "uploader": song.get("uploader"),
        "url": song["url"],
        "source": source,
    }


@app.get("/now-playing")
def now_playing():
    """
    What's playing right now and what plays next, whether that's a real
    (enqueued) song or a default-playlist fallback song. Public — this is
    meant to be shown on the main UI for anyone to see, no login needed.
    """
    songs = queue_state.list_queue()
    playing = next((s for s in songs if s["status"] == "playing"), None)
    upcoming = [s for s in songs if s["status"] == "queued"]

    if playing is not None:
        playing_summary = _song_summary(playing, "queue")
    else:
        fallback_playing = default_playlist.get_now_playing()
        playing_summary = _song_summary(fallback_playing, "playlist") if fallback_playing else None

    if upcoming:
        next_summary = _song_summary(upcoming[0], "queue")
    else:
        fallback_next = default_playlist.peek_next_song()
        next_summary = _song_summary(fallback_next, "playlist") if fallback_next else None

    return {"playing": playing_summary, "next": next_summary}


@app.post("/login")
def login(admin: str = Depends(require_admin)):
    """Validates admin credentials. Returns 401 (via require_admin) if they're wrong."""
    return {"status": "ok", "username": admin}


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


@app.get("/queue")
def queue(admin: str = Depends(require_admin)):
    """Lists every song currently queued, in play order, with position and estimated wait."""
    songs = queue_state.list_queue()
    for song in songs:
        song["duration"] = queue_state.format_duration(song["duration_seconds"])
        song["estimated_wait"] = queue_state.format_duration(song["estimated_wait_seconds"])
    return {"queue": songs}


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
        is_valid, reason, metadata = validate_song_url(url, skip_content_checks=True)
        if not is_valid:
            rejected.append({"url": url, "reason": reason})
            continue
        item = default_playlist.add_song(
            playlist_id, url, metadata.get("title"), metadata.get("uploader"), metadata.get("duration")
        )
        if item is None:
            raise HTTPException(status_code=404, detail=f"No playlist with id {playlist_id}")
        item["duration"] = queue_state.format_duration(item.get("duration"))
        added.append(item)

    logger.info(
        "Playlist %s: added %d, rejected %d (by %s)", playlist_id, len(added), len(rejected), admin
    )
    return {"added": added, "rejected": rejected}


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
def enqueue(req: EnqueueRequest, admin: Optional[str] = Depends(optional_admin)):
    """
    Accepts one or more YouTube URLs. Each is probed and only pushed onto the
    Kafka queue if it passes validation:
      - not age-restricted / adult content
      - actually a music/song video
      - under the 2 hour duration limit

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

    def _on_delivery(err, msg, song_id, url):
        if err is not None:
            delivery_failures.append({"id": song_id, "url": url, "reason": f"Kafka delivery failed: {err}"})

    for url in req.urls:
        video_id = extract_video_id(url)
        duplicate = queue_state.find_by_video_id(video_id) if video_id else None
        if duplicate is not None:
            wait_str = queue_state.format_duration(duplicate["estimated_wait_seconds"])
            rejected.append(
                {"url": url, "reason": f"Your requested song is already in queue, will play after {wait_str}"}
            )
            continue

        is_valid, reason, metadata = validate_song_url(url, skip_content_checks=admin is not None)
        if not is_valid:
            rejected.append({"url": url, "reason": reason})
            continue

        duration = metadata.get("duration")
        title = metadata.get("title")
        uploader = metadata.get("uploader")

        song_id, position, wait_seconds = queue_state.add_song(url, duration, title, uploader, video_id)

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
