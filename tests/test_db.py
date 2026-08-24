"""Tests for db module — connection health check and transaction semantics."""
import sqlite3

import db


def test_get_conn_returns_working_connection():
    conn = db.get_conn()
    assert conn is not None
    result = conn.execute("SELECT 1").fetchone()
    assert result[0] == 1


def test_get_conn_same_thread_reuses_connection():
    c1 = db.get_conn()
    c2 = db.get_conn()
    assert c1 is c2


def test_get_conn_recovers_from_closed_connection():
    conn = db.get_conn()
    conn.close()
    new_conn = db.get_conn()
    assert new_conn is not conn
    result = new_conn.execute("SELECT 1").fetchone()
    assert result[0] == 1


def test_transaction_commits_on_success():
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO app_state (key, value) VALUES ('test_key', 'test_val')"
        )
    row = db.get_conn().execute(
        "SELECT value FROM app_state WHERE key='test_key'"
    ).fetchone()
    assert row is not None
    assert row["value"] == "test_val"


def test_transaction_rolls_back_on_error():
    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO app_state (key, value) VALUES ('rollback_key', 'val')"
            )
            raise ValueError("force rollback")
    except ValueError:
        pass
    row = db.get_conn().execute(
        "SELECT value FROM app_state WHERE key='rollback_key'"
    ).fetchone()
    assert row is None


def test_schema_tables_exist():
    conn = db.get_conn()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    for expected in (
        "queue_items", "history", "analytics_events",
        "playlists", "playlist_songs", "blacklist_videos",
        "blacklist_requesters", "runtime_config", "messages", "outbox", "app_state",
    ):
        assert expected in tables, f"Missing table: {expected}"
