"""
Single entry point for validating a song request, regardless of whether it
came from the web UI, the WhatsApp bridge, or the Telegram bridge — all
three call POST /enqueue, and producer_api.py hands every URL in that
request to validate_song_request() here.

Checks run in cheapest-first order so a request that's going to be rejected
fails fast, before paying for a yt-dlp network probe:
  1. Blacklisted video? (blacklist.py — local file, no network)
  2. Blacklisted requester? (blacklist.py — local file, no network)
  3. Already queued? (duplicate.py — local file, no network)
  4. Queue full? (total estimated wait over max_queue_wait_seconds — local file)
  5. Rate limit headroom? (rate_limiter.py — one Redis round trip)
  6. Content checks: age-restriction / category / duration (content.py —
     the expensive yt-dlp probe).

Admin requests bypass checks 2 and 4–6, but NOT 1 or 3: a video blacklist is
a hard content ban that applies to everyone (blocking a video means it never
plays, admin included), and the duplicate check always runs so admins can't
double-queue the same video. The requester blacklist stays admin-exempt so
the operator can never lock themselves out.

The rate limit is only actually counted (record()) once a song clears every
check, so a batch of rejected URLs never eats into a requester's quota.
"""
from dataclasses import dataclass
from typing import Optional

from . import blacklist, rate_limiter
from .content import validate_song_content
from .duplicate import duplicate_reason, extract_video_id, is_valid_youtube_url
from .identity import detect_requester_id, detect_source
import queue_state
import runtime_config

__all__ = [
    "ValidationResult",
    "validate_song_request",
    "is_valid_youtube_url",
    "extract_video_id",
    "detect_source",
    "detect_requester_id",
    "blacklist",
]


@dataclass
class ValidationResult:
    is_valid: bool
    reason: str
    metadata: dict
    video_id: Optional[str]


def validate_song_request(url: str, *, requester_id: str, is_admin: bool) -> ValidationResult:
    video_id = extract_video_id(url)

    # Video blacklist is a hard content ban — it applies to EVERYONE, admins
    # included. Blocking a video means it never plays; an admin who changes
    # their mind un-blocks it from the Blacklist tab first. (Admins still
    # bypass the requester blacklist, rate limit, and content checks below.)
    if blacklist.is_video_blacklisted(video_id):
        return ValidationResult(
            False, "This song is blocked. Remove it from the blacklist to play it.", {}, video_id
        )

    # Requester blacklist bans a guest by phone/Telegram id/IP. Admins are
    # exempt so the operator can never accidentally lock themselves out.
    if not is_admin and blacklist.is_requester_blacklisted(requester_id):
        return ValidationResult(
            False, "You are not allowed to request songs.", {}, video_id
        )

    dup_reason = duplicate_reason(video_id)
    if dup_reason:
        return ValidationResult(False, dup_reason, {}, video_id)

    if not is_admin:
        # Queue-full: reject when the total estimated wait already exceeds the
        # configured cap (0 disables). Cheap local-file read, no network.
        max_wait = runtime_config.get("max_queue_wait_seconds")
        if max_wait and max_wait > 0:
            _, total_wait = queue_state.current_wait()
            if total_wait >= max_wait:
                return ValidationResult(
                    False,
                    f"The queue is full (over {int(max_wait // 60)} min wait). Please try again later.",
                    {},
                    video_id,
                )

        allowed, retry_after = rate_limiter.check(requester_id)
        if not allowed:
            minutes = max(1, retry_after // 60)
            max_songs = runtime_config.get("rate_limit_max_songs")
            window_min = max(1, runtime_config.get("rate_limit_window_seconds") // 60)
            return ValidationResult(
                False,
                f"Rate limit exceeded: max {max_songs} songs per {window_min} min. "
                f"Try again in about {minutes} min.",
                {},
                video_id,
            )

    is_valid, reason, metadata = validate_song_content(url, skip_content_checks=is_admin)
    if not is_valid:
        return ValidationResult(False, reason, metadata, video_id)

    if not is_admin:
        rate_limiter.record(requester_id)

    return ValidationResult(True, "", metadata, video_id)
