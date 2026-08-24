"""
Admin-managed fallback playlists: named, looping lists of songs. Whichever
one is marked active is what the consumer plays automatically whenever the
real (Kafka-backed) queue is empty.

Uses SQLite (via db module) for persistent storage.
"""
import json
import random
import string
import time
from typing import List, Optional

import db

_ID_ALPHABET = string.ascii_letters + string.digits
_ID_LENGTH = 4


def _generate_id(existing_ids) -> str:
    existing_ids = set(existing_ids)
    while True:
        candidate = "".join(random.choices(_ID_ALPHABET, k=_ID_LENGTH))
        if candidate not in existing_ids:
            return candidate


def create_playlist(name: str) -> dict:
    conn = db.get_conn()
    existing_ids = [r[0] for r in conn.execute("SELECT id FROM playlists").fetchall()]
    playlist_id = _generate_id(existing_ids)
    conn.execute(
        "INSERT INTO playlists (id, name, next_index) VALUES (?,?,0)",
        (playlist_id, name),
    )
    return {"id": playlist_id, "name": name}


def list_playlists() -> List[dict]:
    conn = db.get_conn()
    active_row = conn.execute(
        "SELECT value FROM app_state WHERE key='active_playlist_id'"
    ).fetchone()
    active_id = active_row["value"] if active_row else None

    rows = conn.execute("SELECT id, name FROM playlists").fetchall()
    result = []
    for r in rows:
        count = conn.execute(
            "SELECT COUNT(*) FROM playlist_songs WHERE playlist_id=?", (r["id"],)
        ).fetchone()[0]
        result.append({
            "id": r["id"],
            "name": r["name"],
            "song_count": count,
            "is_active": r["id"] == active_id,
        })
    return result


def delete_playlist(playlist_id: str) -> bool:
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT id FROM playlists WHERE id=?", (playlist_id,)
        ).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
        active_row = conn.execute(
            "SELECT value FROM app_state WHERE key='active_playlist_id'"
        ).fetchone()
        if active_row and active_row["value"] == playlist_id:
            conn.execute("DELETE FROM app_state WHERE key='active_playlist_id'")
    return True


def set_active_playlist(playlist_id: str) -> bool:
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT id FROM playlists WHERE id=?", (playlist_id,)
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "INSERT OR REPLACE INTO app_state (key, value) "
            "VALUES ('active_playlist_id', ?)",
            (playlist_id,),
        )
    return True


def deactivate_playlist(playlist_id: str) -> bool:
    with db.transaction() as conn:
        active_row = conn.execute(
            "SELECT value FROM app_state WHERE key='active_playlist_id'"
        ).fetchone()
        if not active_row or active_row["value"] != playlist_id:
            return False
        conn.execute("DELETE FROM app_state WHERE key='active_playlist_id'")
    return True


def get_active_playlist() -> Optional[dict]:
    conn = db.get_conn()
    active_row = conn.execute(
        "SELECT value FROM app_state WHERE key='active_playlist_id'"
    ).fetchone()
    if not active_row:
        return None
    row = conn.execute(
        "SELECT id, name FROM playlists WHERE id=?", (active_row["value"],)
    ).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "name": row["name"]}


def list_songs(playlist_id: str) -> Optional[List[dict]]:
    conn = db.get_conn()
    row = conn.execute(
        "SELECT id FROM playlists WHERE id=?", (playlist_id,)
    ).fetchone()
    if row is None:
        return None
    songs = conn.execute(
        "SELECT id, url, title, uploader, duration FROM playlist_songs "
        "WHERE playlist_id=? ORDER BY position",
        (playlist_id,),
    ).fetchall()
    return [dict(s) for s in songs]


def add_song(
    playlist_id: str,
    url: str,
    title: Optional[str],
    uploader: Optional[str],
    duration: Optional[float],
) -> Optional[dict]:
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT id FROM playlists WHERE id=?", (playlist_id,)
        ).fetchone()
        if row is None:
            return None
        dup = conn.execute(
            "SELECT title FROM playlist_songs WHERE playlist_id=? AND url=?",
            (playlist_id, url),
        ).fetchone()
        if dup:
            return {"duplicate": True, "url": url, "title": dup["title"]}

        existing_ids = [
            r[0] for r in conn.execute(
                "SELECT id FROM playlist_songs WHERE playlist_id=?", (playlist_id,)
            ).fetchall()
        ]
        song_id = _generate_id(existing_ids)

        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM playlist_songs "
            "WHERE playlist_id=?",
            (playlist_id,),
        ).fetchone()[0]

        conn.execute(
            "INSERT INTO playlist_songs "
            "(id, playlist_id, url, title, uploader, duration, position) "
            "VALUES (?,?,?,?,?,?,?)",
            (song_id, playlist_id, url, title, uploader, duration, max_pos + 1),
        )
    return {
        "id": song_id, "url": url, "title": title,
        "uploader": uploader, "duration": duration,
    }


def remove_song(playlist_id: str, song_id: str) -> bool:
    with db.transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM playlist_songs WHERE id=? AND playlist_id=?",
            (song_id, playlist_id),
        )
        if cursor.rowcount == 0:
            return False
        count = conn.execute(
            "SELECT COUNT(*) FROM playlist_songs WHERE playlist_id=?",
            (playlist_id,),
        ).fetchone()[0]
        if count > 0:
            pl = conn.execute(
                "SELECT next_index FROM playlists WHERE id=?", (playlist_id,)
            ).fetchone()
            if pl:
                conn.execute(
                    "UPDATE playlists SET next_index=? WHERE id=?",
                    (pl["next_index"] % count, playlist_id),
                )
        else:
            conn.execute(
                "UPDATE playlists SET next_index=0 WHERE id=?", (playlist_id,)
            )
    return True


def next_song() -> Optional[dict]:
    with db.transaction() as conn:
        active_row = conn.execute(
            "SELECT value FROM app_state WHERE key='active_playlist_id'"
        ).fetchone()
        if not active_row:
            return None
        playlist_id = active_row["value"]

        pl = conn.execute(
            "SELECT next_index FROM playlists WHERE id=?", (playlist_id,)
        ).fetchone()
        if pl is None:
            return None

        songs = conn.execute(
            "SELECT id, url, title, uploader, duration FROM playlist_songs "
            "WHERE playlist_id=? ORDER BY position",
            (playlist_id,),
        ).fetchall()
        if not songs:
            return None

        index = pl["next_index"] % len(songs)
        song = dict(songs[index])
        conn.execute(
            "UPDATE playlists SET next_index=? WHERE id=?",
            ((index + 1) % len(songs), playlist_id),
        )
    return song


def peek_next_song() -> Optional[dict]:
    conn = db.get_conn()
    active_row = conn.execute(
        "SELECT value FROM app_state WHERE key='active_playlist_id'"
    ).fetchone()
    if not active_row:
        return None
    playlist_id = active_row["value"]

    pl = conn.execute(
        "SELECT next_index FROM playlists WHERE id=?", (playlist_id,)
    ).fetchone()
    if pl is None:
        return None

    songs = conn.execute(
        "SELECT id, url, title, uploader, duration FROM playlist_songs "
        "WHERE playlist_id=? ORDER BY position",
        (playlist_id,),
    ).fetchall()
    if not songs:
        return None

    return dict(songs[pl["next_index"] % len(songs)])


def set_now_playing(song: dict) -> None:
    now_playing = {
        "id": song["id"],
        "url": song["url"],
        "title": song.get("title"),
        "uploader": song.get("uploader"),
        "duration": song.get("duration"),
        "started_at": time.time(),
        "seek_offset": 0,
        "paused_duration": 0,
        "paused_at": None,
    }
    conn = db.get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO app_state (key, value) "
        "VALUES ('default_playlist_now_playing', ?)",
        (json.dumps(now_playing),),
    )


def _get_now_playing_data(conn) -> Optional[dict]:
    row = conn.execute(
        "SELECT value FROM app_state WHERE key='default_playlist_now_playing'"
    ).fetchone()
    if not row or not row["value"]:
        return None
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return None


def mark_now_playing_paused() -> None:
    with db.transaction() as conn:
        np = _get_now_playing_data(conn)
        if np and np.get("paused_at") is None:
            np["paused_at"] = time.time()
            conn.execute(
                "UPDATE app_state SET value=? "
                "WHERE key='default_playlist_now_playing'",
                (json.dumps(np),),
            )


def mark_now_playing_resumed() -> None:
    with db.transaction() as conn:
        np = _get_now_playing_data(conn)
        if np and np.get("paused_at") is not None:
            np["paused_duration"] = (np.get("paused_duration") or 0) + (
                time.time() - np["paused_at"]
            )
            np["paused_at"] = None
            conn.execute(
                "UPDATE app_state SET value=? "
                "WHERE key='default_playlist_now_playing'",
                (json.dumps(np),),
            )


def mark_now_playing_seeked(offset: float) -> None:
    with db.transaction() as conn:
        np = _get_now_playing_data(conn)
        if np:
            np["seek_offset"] = offset
            np["started_at"] = time.time()
            np["paused_duration"] = 0
            np["paused_at"] = None
            conn.execute(
                "UPDATE app_state SET value=? "
                "WHERE key='default_playlist_now_playing'",
                (json.dumps(np),),
            )


def clear_now_playing() -> None:
    conn = db.get_conn()
    conn.execute("DELETE FROM app_state WHERE key='default_playlist_now_playing'")


def reorder_songs(playlist_id: str, ordered_ids: List[str]) -> bool:
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT id, next_index FROM playlists WHERE id=?", (playlist_id,)
        ).fetchone()
        if row is None:
            return False

        songs = conn.execute(
            "SELECT id FROM playlist_songs WHERE playlist_id=? ORDER BY position",
            (playlist_id,),
        ).fetchall()
        song_ids = {r["id"] for r in songs}

        new_order = [sid for sid in ordered_ids if sid in song_ids]
        included = set(new_order)
        for s in songs:
            if s["id"] not in included:
                new_order.append(s["id"])

        for i, sid in enumerate(new_order):
            conn.execute(
                "UPDATE playlist_songs SET position=? "
                "WHERE id=? AND playlist_id=?",
                (i, sid, playlist_id),
            )

        if new_order:
            conn.execute(
                "UPDATE playlists SET next_index=? WHERE id=?",
                (row["next_index"] % len(new_order), playlist_id),
            )
    return True


def get_now_playing() -> Optional[dict]:
    conn = db.get_conn()
    return _get_now_playing_data(conn)
