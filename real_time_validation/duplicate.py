import re
from typing import Optional

import queue_state

# Same URL shapes producer_api.py accepts requests for.
YOUTUBE_URL_RE = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[\w-]+"
)

# Same URL shapes as YOUTUBE_URL_RE, but captures the video ID so different
# URLs pointing at the same video (e.g. with/without a "?si=" share token)
# can be recognized as duplicates.
VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([\w-]+)"
)


def is_valid_youtube_url(url: str) -> bool:
    """Cheap, no-network check: is this even shaped like a YouTube URL?"""
    return bool(YOUTUBE_URL_RE.match(url))


def extract_video_id(url: str) -> Optional[str]:
    match = VIDEO_ID_RE.search(url)
    return match.group(1) if match else None


def duplicate_reason(video_id: Optional[str]) -> Optional[str]:
    """Returns a rejection reason if this video is already queued, else None."""
    if not video_id:
        return None
    duplicate = queue_state.find_by_video_id(video_id)
    if (duplicate is not None
            and duplicate.get("status") == "queued"
            and not duplicate.get("skip_requested")):
        wait_str = queue_state.format_duration(duplicate["estimated_wait_seconds"])
        return f"Your requested song is already in queue, will play after {wait_str}"
    return None
