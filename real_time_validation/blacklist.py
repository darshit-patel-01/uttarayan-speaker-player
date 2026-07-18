"""
Admin-managed blacklist, persisted to SQLite.

Two independent lists:
  - video_ids:  YouTube video IDs that may never be enqueued.
  - requesters: requester keys (e.g. "whatsapp:15551234567") whose
                requests are refused outright.
"""
import sqlite3
from typing import List

import db


# --- Video IDs -------------------------------------------------------------

def is_video_blacklisted(video_id: str) -> bool:
    if not video_id:
        return False
    conn = db.get_conn()
    row = conn.execute(
        "SELECT 1 FROM blacklist_videos WHERE video_id=?", (video_id,)
    ).fetchone()
    return row is not None


def add_video(video_id: str) -> bool:
    video_id = (video_id or "").strip()
    if not video_id:
        return False
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO blacklist_videos (video_id) VALUES (?)", (video_id,)
        )
        return True
    except sqlite3.IntegrityError:
        return False


def remove_video(video_id: str) -> bool:
    video_id = (video_id or "").strip()
    conn = db.get_conn()
    cursor = conn.execute(
        "DELETE FROM blacklist_videos WHERE video_id=?", (video_id,)
    )
    return cursor.rowcount > 0


# --- Requesters (phone number / Telegram id / IP) --------------------------

def is_requester_blacklisted(requester_id: str) -> bool:
    if not requester_id:
        return False
    conn = db.get_conn()
    row = conn.execute(
        "SELECT 1 FROM blacklist_requesters WHERE requester_id=?",
        (requester_id,),
    ).fetchone()
    return row is not None


def add_requester(requester_id: str) -> bool:
    requester_id = (requester_id or "").strip()
    if not requester_id:
        return False
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO blacklist_requesters (requester_id) VALUES (?)",
            (requester_id,),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def remove_requester(requester_id: str) -> bool:
    requester_id = (requester_id or "").strip()
    conn = db.get_conn()
    cursor = conn.execute(
        "DELETE FROM blacklist_requesters WHERE requester_id=?",
        (requester_id,),
    )
    return cursor.rowcount > 0


# --- Read-only listing (for the admin UI) ----------------------------------

def list_all() -> dict:
    conn = db.get_conn()
    videos = [
        r["video_id"]
        for r in conn.execute("SELECT video_id FROM blacklist_videos").fetchall()
    ]
    requesters = [
        r["requester_id"]
        for r in conn.execute(
            "SELECT requester_id FROM blacklist_requesters"
        ).fetchall()
    ]
    return {"video_ids": videos, "requesters": requesters}
