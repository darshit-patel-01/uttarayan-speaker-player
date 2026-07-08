"""
Admin-managed fallback playlists: named, looping lists of songs. Whichever
one is marked active is what the consumer plays automatically whenever the
real (Kafka-backed) queue is empty. Any real song that gets enqueued
interrupts whatever default song is currently playing almost immediately —
see the interrupt_check hookup in playback.py and its use in
consumer_worker.py.

Same file-based IPC approach as queue_state.py: simple, fine for a single
local user, not safe under heavy concurrent writes.
"""
import json
import os
import random
import string
import threading
from typing import List, Optional

from config import settings

_ID_ALPHABET = string.ascii_letters + string.digits
_ID_LENGTH = 4

_lock = threading.Lock()


def _empty_state() -> dict:
    return {"playlists": {}, "active_playlist_id": None, "now_playing": None}


def _load() -> dict:
    if not os.path.exists(settings.default_playlist_file):
        return _empty_state()
    try:
        with open(settings.default_playlist_file, "r") as f:
            data = json.load(f)
            data.setdefault("playlists", {})
            data.setdefault("active_playlist_id", None)
            data.setdefault("now_playing", None)
            return data
    except (json.JSONDecodeError, OSError):
        return _empty_state()


def _save(data: dict) -> None:
    tmp_path = settings.default_playlist_file + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f)
    os.replace(tmp_path, settings.default_playlist_file)


def _generate_id(existing_ids) -> str:
    existing_ids = set(existing_ids)
    while True:
        candidate = "".join(random.choices(_ID_ALPHABET, k=_ID_LENGTH))
        if candidate not in existing_ids:
            return candidate


def create_playlist(name: str) -> dict:
    with _lock:
        data = _load()
        playlist_id = _generate_id(data["playlists"].keys())
        playlist = {"id": playlist_id, "name": name, "items": [], "next_index": 0}
        data["playlists"][playlist_id] = playlist
        # Not activated automatically — a playlist only plays once an admin
        # explicitly activates it. No active playlist just means the
        # consumer plays nothing while the real queue is empty.
        _save(data)
        return {"id": playlist_id, "name": name}


def list_playlists() -> List[dict]:
    with _lock:
        data = _load()
        active_id = data["active_playlist_id"]
        return [
            {
                "id": playlist["id"],
                "name": playlist["name"],
                "song_count": len(playlist["items"]),
                "is_active": playlist["id"] == active_id,
            }
            for playlist in data["playlists"].values()
        ]


def delete_playlist(playlist_id: str) -> bool:
    with _lock:
        data = _load()
        if playlist_id not in data["playlists"]:
            return False
        del data["playlists"][playlist_id]
        if data["active_playlist_id"] == playlist_id:
            data["active_playlist_id"] = None
        _save(data)
        return True


def set_active_playlist(playlist_id: str) -> bool:
    with _lock:
        data = _load()
        if playlist_id not in data["playlists"]:
            return False
        data["active_playlist_id"] = playlist_id
        _save(data)
        return True


def deactivate_playlist(playlist_id: str) -> bool:
    """Clears the active playlist, but only if playlist_id is actually the active one."""
    with _lock:
        data = _load()
        if data["active_playlist_id"] != playlist_id:
            return False
        data["active_playlist_id"] = None
        _save(data)
        return True


def get_active_playlist() -> Optional[dict]:
    with _lock:
        data = _load()
        active_id = data["active_playlist_id"]
        if active_id is None or active_id not in data["playlists"]:
            return None
        playlist = data["playlists"][active_id]
        return {"id": playlist["id"], "name": playlist["name"]}


def list_songs(playlist_id: str) -> Optional[List[dict]]:
    with _lock:
        data = _load()
        playlist = data["playlists"].get(playlist_id)
        if playlist is None:
            return None
        return list(playlist["items"])


def add_song(
    playlist_id: str, url: str, title: Optional[str], uploader: Optional[str], duration: Optional[float]
) -> Optional[dict]:
    with _lock:
        data = _load()
        playlist = data["playlists"].get(playlist_id)
        if playlist is None:
            return None
        existing_song_ids = {item["id"] for item in playlist["items"]}
        item = {
            "id": _generate_id(existing_song_ids),
            "url": url,
            "title": title,
            "uploader": uploader,
            "duration": duration,
        }
        playlist["items"].append(item)
        _save(data)
        return item


def remove_song(playlist_id: str, song_id: str) -> bool:
    with _lock:
        data = _load()
        playlist = data["playlists"].get(playlist_id)
        if playlist is None:
            return False
        before = len(playlist["items"])
        playlist["items"] = [item for item in playlist["items"] if item["id"] != song_id]
        if len(playlist["items"]) == before:
            return False
        if playlist["items"]:
            playlist["next_index"] = playlist["next_index"] % len(playlist["items"])
        else:
            playlist["next_index"] = 0
        _save(data)
        return True


def next_song() -> Optional[dict]:
    """
    Returns the next song from the active playlist in round-robin order and
    advances its pointer, wrapping back to the start once the end is
    reached. None if there's no active playlist, or it's empty.
    """
    with _lock:
        data = _load()
        active_id = data["active_playlist_id"]
        if active_id is None or active_id not in data["playlists"]:
            return None
        playlist = data["playlists"][active_id]
        items = playlist["items"]
        if not items:
            return None
        index = playlist["next_index"] % len(items)
        song = items[index]
        playlist["next_index"] = (index + 1) % len(items)
        _save(data)
        return song


def peek_next_song() -> Optional[dict]:
    """
    Like next_song(), but doesn't advance the pointer — for display purposes
    (e.g. "up next" on the UI) without disturbing actual playback order.
    """
    with _lock:
        data = _load()
        active_id = data["active_playlist_id"]
        if active_id is None or active_id not in data["playlists"]:
            return None
        playlist = data["playlists"][active_id]
        items = playlist["items"]
        if not items:
            return None
        return items[playlist["next_index"] % len(items)]


def set_now_playing(song: dict) -> None:
    """Called by the consumer right before it starts playing a default-playlist song."""
    with _lock:
        data = _load()
        data["now_playing"] = {
            "id": song["id"],
            "url": song["url"],
            "title": song.get("title"),
            "uploader": song.get("uploader"),
        }
        _save(data)


def clear_now_playing() -> None:
    """Called by the consumer once a default-playlist song stops playing, however it stopped."""
    with _lock:
        data = _load()
        data["now_playing"] = None
        _save(data)


def get_now_playing() -> Optional[dict]:
    with _lock:
        return _load()["now_playing"]
