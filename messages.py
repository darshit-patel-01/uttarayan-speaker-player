"""
Appeal messages from blocked users, persisted to SQLite.

When a blocked user tries to request a song, the bridges offer them
a one-shot reply window to send a message to the admin.  Those messages
land here and are surfaced in the admin UI's "Messages" tab.

Admin replies are stored in the outbox table.  The bridges poll
for pending replies, deliver them, and mark them as delivered.
"""
import time
import uuid

import db

MAX_MESSAGE_LENGTH = 400


def add_message(source: str, requester_id: str, text: str) -> dict:
    text = (text or "").strip()[:MAX_MESSAGE_LENGTH]
    if not text:
        return {}
    entry_id = uuid.uuid4().hex[:12]
    ts = time.time()
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO messages (id, source, requester_id, text, timestamp, read) "
        "VALUES (?,?,?,?,?,0)",
        (entry_id, source, requester_id, text, ts),
    )
    return {
        "id": entry_id,
        "source": source,
        "requester_id": requester_id,
        "text": text,
        "timestamp": ts,
        "read": False,
    }


def list_messages() -> list:
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM messages ORDER BY timestamp").fetchall()
    return [{**dict(r), "read": bool(r["read"])} for r in rows]


def mark_read(message_id: str) -> bool:
    conn = db.get_conn()
    cursor = conn.execute("UPDATE messages SET read=1 WHERE id=?", (message_id,))
    return cursor.rowcount > 0


def delete_message(message_id: str) -> bool:
    conn = db.get_conn()
    cursor = conn.execute("DELETE FROM messages WHERE id=?", (message_id,))
    return cursor.rowcount > 0


def unread_count() -> int:
    conn = db.get_conn()
    return conn.execute("SELECT COUNT(*) FROM messages WHERE read=0").fetchone()[0]


# --- Outbox: admin replies to be delivered by bridges ---------------------

def add_reply(source: str, requester_id: str, text: str) -> dict:
    text = (text or "").strip()[:MAX_MESSAGE_LENGTH]
    if not text:
        return {}
    entry_id = uuid.uuid4().hex[:12]
    ts = time.time()
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO outbox (id, source, requester_id, text, timestamp, delivered) "
        "VALUES (?,?,?,?,?,0)",
        (entry_id, source, requester_id, text, ts),
    )
    return {
        "id": entry_id,
        "source": source,
        "requester_id": requester_id,
        "text": text,
        "timestamp": ts,
        "delivered": False,
    }


def pending_replies(source: str) -> list:
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM outbox WHERE source=? AND delivered=0 ORDER BY timestamp",
        (source,),
    ).fetchall()
    return [{**dict(r), "delivered": bool(r["delivered"])} for r in rows]


def mark_delivered(reply_id: str) -> bool:
    conn = db.get_conn()
    cursor = conn.execute("UPDATE outbox SET delivered=1 WHERE id=?", (reply_id,))
    return cursor.rowcount > 0
