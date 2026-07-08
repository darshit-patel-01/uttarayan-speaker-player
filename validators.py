import logging
from typing import Optional, Tuple

import yt_dlp

logger = logging.getLogger("validators")

_PROBE_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "skip_download": True,
}

# YouTube has no public "is this adult content" flag. The closest available
# signal is age_limit, which yt-dlp/YouTube set to 18 for age-restricted
# videos (this covers explicit/mature content flagged by the uploader or
# YouTube's own review). Anything at or above this is rejected.
MAX_ALLOWED_AGE_LIMIT = 17

# Long videos (mixes, live streams, full albums) risk running past
# max.poll.interval.ms and getting redelivered/duplicated by Kafka — see
# consumer_worker.py. Reject anything over 2 hours up front instead.
MAX_ALLOWED_DURATION_SECONDS = 2 * 60 * 60


def validate_song_url(url: str, skip_content_checks: bool = False) -> Tuple[bool, str, dict]:
    """
    Probes a YouTube URL's metadata (no download) and, unless
    skip_content_checks is set, checks:
      1. It is not age-restricted / mature (adult) content.
      2. It is actually a music/song video, not an arbitrary video.
      3. It is not longer than MAX_ALLOWED_DURATION_SECONDS.

    skip_content_checks is for admin-submitted songs: metadata is still
    probed (duration is needed for queue/wait-time accounting), but none of
    the above checks are enforced, so admins can enqueue whatever they like.

    Returns (is_valid, reason, metadata). `reason` is empty when is_valid is
    True. `metadata` holds whatever was resolved before the check that
    rejected it (duration, title, uploader) — fields are None if extraction
    failed before they could be read (e.g. the URL didn't resolve at all).
    """
    try:
        with yt_dlp.YoutubeDL(_PROBE_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        return False, f"Could not fetch video info: {exc}", {"duration": None, "title": None, "uploader": None}

    metadata = {
        "duration": info.get("duration"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
    }

    if skip_content_checks:
        return True, "", metadata

    age_limit = info.get("age_limit") or 0
    if age_limit > MAX_ALLOWED_AGE_LIMIT:
        return False, "Rejected: video is age-restricted / adult content", metadata

    categories = info.get("categories") or []
    is_music_category = any(str(c).lower() == "music" for c in categories)

    # Videos ingested as YouTube Music tracks also carry track/artist tags,
    # which is a useful fallback when "categories" is missing or generic.
    has_track_metadata = bool(info.get("track")) or bool(info.get("artist"))

    if not (is_music_category or has_track_metadata):
        return False, "Rejected: video does not appear to be a song (category is not Music)", metadata

    duration = metadata.get("duration")
    if duration is not None and duration > MAX_ALLOWED_DURATION_SECONDS:
        return False, "Rejected: video is longer than the 2 hour limit", metadata

    return True, "", metadata
