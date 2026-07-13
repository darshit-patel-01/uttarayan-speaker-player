"""
Admin-managed blacklist, persisted to a small JSON file (same file-based IPC
pattern as queue_state.py / default_playlist.py — the API process writes it,
this module reads it during validation).

Two independent lists:
  - video_ids:  YouTube video IDs that may never be enqueued.
  - requesters: requester keys (as produced by identity.detect_requester_id,
                e.g. "whatsapp:15551234567", "telegram:999888777",
                "ip:1.2.3.4") whose requests are refused outright.

Blacklist checks run first in real_time_validation and apply to non-admin
requests only, consistent with how the other validation checks are skipped
for admins — an admin is never blacklisting themselves, and staying
admin-exempt keeps blacklisting from ever locking the operator out.
"""
import json
import os
import threading
from typing import List

from config import settings

_lock = threading.Lock()


def _empty() -> dict:
    return {"video_ids": [], "requesters": []}


def _load() -> dict:
    if not os.path.exists(settings.blacklist_file):
        return _empty()
    try:
        with open(settings.blacklist_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("video_ids", [])
            data.setdefault("requesters", [])
            return data
    except (json.JSONDecodeError, OSError):
        return _empty()


def _save(data: dict) -> None:
    tmp_path = settings.blacklist_file + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, settings.blacklist_file)


def _add(key: str, value: str) -> bool:
    """Add value to list `key`. Returns True if added, False if already present."""
    value = (value or "").strip()
    if not value:
        return False
    with _lock:
        data = _load()
        if value in data[key]:
            return False
        data[key].append(value)
        _save(data)
        return True


def _remove(key: str, value: str) -> bool:
    """Remove value from list `key`. Returns True if removed, False if absent."""
    value = (value or "").strip()
    with _lock:
        data = _load()
        if value not in data[key]:
            return False
        data[key].remove(value)
        _save(data)
        return True


# --- Video IDs -------------------------------------------------------------

def is_video_blacklisted(video_id: str) -> bool:
    if not video_id:
        return False
    with _lock:
        return video_id in _load()["video_ids"]


def add_video(video_id: str) -> bool:
    return _add("video_ids", video_id)


def remove_video(video_id: str) -> bool:
    return _remove("video_ids", video_id)


# --- Requesters (phone number / Telegram id / IP) --------------------------

def is_requester_blacklisted(requester_id: str) -> bool:
    if not requester_id:
        return False
    with _lock:
        return requester_id in _load()["requesters"]


def add_requester(requester_id: str) -> bool:
    return _add("requesters", requester_id)


def remove_requester(requester_id: str) -> bool:
    return _remove("requesters", requester_id)


# --- Read-only listing (for the admin UI) ----------------------------------

def list_all() -> dict:
    with _lock:
        data = _load()
        return {
            "video_ids": list(data["video_ids"]),
            "requesters": list(data["requesters"]),
        }
