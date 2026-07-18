"""
Centralized SQLite database replacing all JSON file storage.

WAL mode enables safe concurrent access from the API and consumer processes.
Tables are created on first import; existing JSON data is migrated once.
"""
import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager

from config import settings

logger = logging.getLogger(__name__)

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(settings.db_file, isolation_level=None, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return _local.conn


@contextmanager
def transaction():
    conn = get_conn()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue_items (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    video_id TEXT,
    title TEXT,
    uploader TEXT,
    duration REAL,
    status TEXT NOT NULL DEFAULT 'queued',
    source TEXT,
    requester_id TEXT,
    started_at REAL,
    seek_offset REAL DEFAULT 0,
    paused_duration REAL DEFAULT 0,
    paused_at REAL,
    skip_requested INTEGER DEFAULT 0,
    position INTEGER NOT NULL,
    dedication TEXT
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    video_id TEXT,
    title TEXT,
    uploader TEXT,
    duration REAL,
    source TEXT,
    played_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    played_at REAL NOT NULL,
    video_id TEXT,
    url TEXT,
    title TEXT,
    uploader TEXT,
    duration REAL DEFAULT 0,
    source TEXT,
    requester_id TEXT
);

CREATE TABLE IF NOT EXISTS playlists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    next_index INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS playlist_songs (
    id TEXT NOT NULL,
    playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    title TEXT,
    uploader TEXT,
    duration REAL,
    position INTEGER NOT NULL,
    PRIMARY KEY (id, playlist_id)
);

CREATE TABLE IF NOT EXISTS blacklist_videos (
    video_id TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS blacklist_requesters (
    requester_id TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS runtime_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    requester_id TEXT NOT NULL,
    text TEXT NOT NULL,
    timestamp REAL NOT NULL,
    read INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    requester_id TEXT NOT NULL,
    text TEXT NOT NULL,
    timestamp REAL NOT NULL,
    delivered INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _read_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _migrate_from_json(conn):
    """Import data from legacy JSON files on first run."""
    row = conn.execute(
        "SELECT value FROM app_state WHERE key='migrated_from_json'"
    ).fetchone()
    if row:
        return

    base_dir = os.path.dirname(os.path.abspath(__file__))
    migrated = False

    conn.execute("BEGIN IMMEDIATE")
    try:
        # Queue state
        data = _read_json(settings.queue_state_file)
        if data and data.get("items"):
            for i, item in enumerate(data["items"]):
                conn.execute(
                    "INSERT OR IGNORE INTO queue_items "
                    "(id, url, video_id, title, uploader, duration, status, source, "
                    "requester_id, started_at, seek_offset, paused_duration, paused_at, "
                    "skip_requested, position) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item["id"], item["url"], item.get("video_id"),
                        item.get("title"), item.get("uploader"), item.get("duration"),
                        item["status"], item.get("source"), item.get("requester_id"),
                        item.get("started_at"), item.get("seek_offset", 0),
                        item.get("paused_duration", 0), item.get("paused_at"),
                        1 if item.get("skip_requested") else 0, i,
                    ),
                )
            migrated = True

        # History
        data = _read_json(settings.history_file)
        if data and data.get("songs"):
            for song in data["songs"]:
                conn.execute(
                    "INSERT INTO history "
                    "(url, video_id, title, uploader, duration, source, played_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        song["url"], song.get("video_id"), song.get("title"),
                        song.get("uploader"), song.get("duration"),
                        song.get("source"), song.get("played_at", 0),
                    ),
                )
            migrated = True

        # Analytics events
        data = _read_json(settings.analytics_file)
        if data and data.get("events"):
            for event in data["events"]:
                conn.execute(
                    "INSERT INTO analytics_events "
                    "(played_at, video_id, url, title, uploader, duration, source, requester_id) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        event.get("played_at", 0), event.get("video_id"),
                        event.get("url"), event.get("title"), event.get("uploader"),
                        event.get("duration", 0), event.get("source"),
                        event.get("requester_id"),
                    ),
                )
            migrated = True

        # Default playlists
        data = _read_json(settings.default_playlist_file)
        if data:
            for pid, playlist in data.get("playlists", {}).items():
                conn.execute(
                    "INSERT OR IGNORE INTO playlists (id, name, next_index) VALUES (?,?,?)",
                    (pid, playlist["name"], playlist.get("next_index", 0)),
                )
                for i, item in enumerate(playlist.get("items", [])):
                    conn.execute(
                        "INSERT OR IGNORE INTO playlist_songs "
                        "(id, playlist_id, url, title, uploader, duration, position) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (
                            item["id"], pid, item["url"], item.get("title"),
                            item.get("uploader"), item.get("duration"), i,
                        ),
                    )
            if data.get("active_playlist_id"):
                conn.execute(
                    "INSERT OR REPLACE INTO app_state (key, value) VALUES "
                    "('active_playlist_id', ?)",
                    (data["active_playlist_id"],),
                )
            if data.get("now_playing"):
                conn.execute(
                    "INSERT OR REPLACE INTO app_state (key, value) VALUES "
                    "('default_playlist_now_playing', ?)",
                    (json.dumps(data["now_playing"]),),
                )
            migrated = True

        # Blacklist
        data = _read_json(settings.blacklist_file)
        if data:
            for vid in data.get("video_ids", []):
                conn.execute(
                    "INSERT OR IGNORE INTO blacklist_videos (video_id) VALUES (?)",
                    (vid,),
                )
            for rid in data.get("requesters", []):
                conn.execute(
                    "INSERT OR IGNORE INTO blacklist_requesters (requester_id) VALUES (?)",
                    (rid,),
                )
            migrated = True

        # Runtime config
        data = _read_json(settings.runtime_config_file)
        if data and isinstance(data, dict):
            for key, value in data.items():
                conn.execute(
                    "INSERT OR REPLACE INTO runtime_config (key, value) VALUES (?,?)",
                    (key, json.dumps(value)),
                )
            migrated = True

        # Messages
        messages_file = os.path.join(base_dir, ".messages.json")
        data = _read_json(messages_file)
        if data and isinstance(data, list):
            for msg in data:
                conn.execute(
                    "INSERT OR IGNORE INTO messages "
                    "(id, source, requester_id, text, timestamp, read) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        msg["id"], msg["source"], msg["requester_id"],
                        msg["text"], msg["timestamp"],
                        1 if msg.get("read") else 0,
                    ),
                )
            migrated = True

        # Outbox
        outbox_file = os.path.join(base_dir, ".outbox.json")
        data = _read_json(outbox_file)
        if data and isinstance(data, list):
            for reply in data:
                conn.execute(
                    "INSERT OR IGNORE INTO outbox "
                    "(id, source, requester_id, text, timestamp, delivered) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        reply["id"], reply["source"], reply["requester_id"],
                        reply["text"], reply["timestamp"],
                        1 if reply.get("delivered") else 0,
                    ),
                )
            migrated = True

        conn.execute(
            "INSERT INTO app_state (key, value) VALUES ('migrated_from_json', '1')"
        )
        conn.execute("COMMIT")

        if migrated:
            logger.info("Migrated legacy JSON data to SQLite database")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _run_migrations(conn) -> None:
    """Schema migrations for existing databases."""
    try:
        conn.execute("ALTER TABLE queue_items ADD COLUMN dedication TEXT")
    except sqlite3.OperationalError:
        pass


def init_db() -> None:
    conn = get_conn()
    conn.executescript(_SCHEMA)
    _run_migrations(conn)
    _migrate_from_json(conn)


init_db()
