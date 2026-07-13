"""
Redis-backed per-requester rate limit: at most settings.rate_limit_max_songs
successfully-enqueued songs per settings.rate_limit_window_seconds, keyed by
requester id (phone number, Telegram id, or IP for plain web requests — see
identity.py). A simple fixed-window counter (INCR + EXPIRE on first hit) is
plenty accurate for this use case; no need for a sliding-window log.

If Redis is unreachable, checks fail OPEN (request allowed, not counted) and
log a warning — a rate-limiter outage shouldn't be able to stop the music.
"""
import logging
from typing import Optional, Tuple

import redis

import runtime_config
from config import settings

logger = logging.getLogger("real_time_validation.rate_limiter")

_client: Optional[redis.Redis] = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _client


def _key(requester_id: str) -> str:
    return f"ratelimit:songs:{requester_id}"


def check(requester_id: str) -> Tuple[bool, int]:
    """
    Returns (allowed, retry_after_seconds). retry_after_seconds is 0 when
    allowed. Does not itself count against the limit — call record() once
    the song actually passes every other check.
    """
    try:
        client = _get_client()
        key = _key(requester_id)
        count = int(client.get(key) or 0)
        if count >= runtime_config.get("rate_limit_max_songs"):
            return False, max(client.ttl(key), 0)
        return True, 0
    except redis.RedisError as exc:
        logger.warning("Rate limiter unavailable (%s) — allowing request for %s", exc, requester_id)
        return True, 0


def record(requester_id: str) -> None:
    """Counts one song against requester_id's window, starting a new window on the first song."""
    try:
        client = _get_client()
        key = _key(requester_id)
        new_count = client.incr(key)
        if new_count == 1:
            client.expire(key, runtime_config.get("rate_limit_window_seconds"))
    except redis.RedisError as exc:
        logger.warning("Rate limiter unavailable (%s) — song not counted for %s", exc, requester_id)
