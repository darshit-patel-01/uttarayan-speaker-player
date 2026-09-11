"""
Per-requester rate limit: at most settings.rate_limit_max_songs
successfully-enqueued songs per settings.rate_limit_window_seconds, keyed by
requester id (phone number, Telegram id, or IP for plain web requests — see
identity.py). A simple fixed-window counter is plenty accurate for this use
case; no need for a sliding-window log.

Backed by the app's SQLite database (the `rate_limits` table) so it needs no
extra service. Expired windows are simply overwritten on the next hit, so
the table never needs a sweeper.

If the database is unreachable, checks fail OPEN (request allowed, not
counted) and log a warning — a rate-limiter outage shouldn't be able to
stop the music.
"""
import logging
import sqlite3
import time
from typing import Tuple

import db
import runtime_config

logger = logging.getLogger("real_time_validation.rate_limiter")


def check(requester_id: str) -> Tuple[bool, int]:
    """
    Returns (allowed, retry_after_seconds). retry_after_seconds is 0 when
    allowed. Does not itself count against the limit — call record() once
    the song actually passes every other check.
    """
    try:
        row = db.get_conn().execute(
            "SELECT count, window_ends_at FROM rate_limits WHERE requester_id = ?",
            (requester_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        logger.warning("Rate limiter unavailable (%s) — allowing request for %s", exc, requester_id)
        return True, 0

    now = time.time()
    if row is None or row["window_ends_at"] <= now:
        return True, 0
    if row["count"] >= runtime_config.get("rate_limit_max_songs"):
        return False, max(int(row["window_ends_at"] - now), 0)
    return True, 0


def record(requester_id: str) -> None:
    """Counts one song against requester_id's window, starting a new window on the first song."""
    now = time.time()
    try:
        with db.transaction() as conn:
            row = conn.execute(
                "SELECT count, window_ends_at FROM rate_limits WHERE requester_id = ?",
                (requester_id,),
            ).fetchone()
            if row is None or row["window_ends_at"] <= now:
                conn.execute(
                    "INSERT OR REPLACE INTO rate_limits (requester_id, count, window_ends_at) "
                    "VALUES (?, 1, ?)",
                    (requester_id, now + runtime_config.get("rate_limit_window_seconds")),
                )
            else:
                conn.execute(
                    "UPDATE rate_limits SET count = count + 1 WHERE requester_id = ?",
                    (requester_id,),
                )
    except sqlite3.Error as exc:
        logger.warning("Rate limiter unavailable (%s) — song not counted for %s", exc, requester_id)
