import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    kafka_bootstrap_servers: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092")
    kafka_topic: str = os.getenv("KAFKA_TOPIC", "youtube-audio-queue")
    kafka_group_id: str = os.getenv("KAFKA_GROUP_ID", "youtube-audio-player")

    # The consumer blocks for the entire length of a song between calls to
    # consumer.poll(), since play_youtube_audio() is synchronous. Kafka's
    # default max.poll.interval.ms (5 minutes) would otherwise evict the
    # consumer from its group mid-song and redeliver the same message.
    # 6 hours comfortably covers even very long tracks/mixes.
    kafka_max_poll_interval_ms: int = int(os.getenv("KAFKA_MAX_POLL_INTERVAL_MS", str(6 * 60 * 60 * 1000)))

    # Redis backs real_time_validation/'s per-requester rate limiting.
    redis_host: str = os.getenv("REDIS_HOST", "127.0.0.1")
    redis_port: int = int(os.getenv("REDIS_PORT", "6380"))
    redis_db: int = int(os.getenv("REDIS_DB", "0"))

    # Max songs a single requester (phone number, Telegram id, or IP for web)
    # may successfully enqueue within rate_limit_window_seconds. Admins are
    # exempt, same as the other validation checks below.
    rate_limit_max_songs: int = int(os.getenv("RATE_LIMIT_MAX_SONGS", "3"))
    rate_limit_window_seconds: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", str(60 * 60)))

    # Reject new non-admin requests once the queue's total estimated wait
    # already exceeds this. 0 disables the check. Default 120 minutes.
    max_queue_wait_seconds: int = int(os.getenv("MAX_QUEUE_WAIT_SECONDS", str(120 * 60)))

    # Longest song duration accepted for non-admin requests.
    max_duration_seconds: int = int(os.getenv("MAX_DURATION_SECONDS", str(2 * 60 * 60)))

    # These env values above are the *defaults*; several are admin-tunable at
    # runtime (persisted to runtime_config_file and read live by both the API
    # and consumer processes). See runtime_config.py.
    runtime_config_file: str = os.getenv(
        "RUNTIME_CONFIG_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".runtime_config.json"),
    )

    # Loudness normalization: ffplay applies a single-pass `loudnorm` filter
    # so songs at wildly different recording volumes land at a consistent
    # perceived loudness. Single-pass (not the more accurate two-pass mode,
    # which requires a separate analysis run over the whole file) trades a
    # few seconds of ramp-up accuracy at the start of each song for zero
    # added startup latency — this app already downloads-then-plays with a
    # prefetch cache specifically to avoid per-song delays, so a two-pass
    # analysis pass would undo that.
    normalize_volume: bool = os.getenv("NORMALIZE_VOLUME", "true").lower() not in ("false", "0", "no")
    loudnorm_target_lufs: float = float(os.getenv("LOUDNORM_TARGET_LUFS", "-16"))

    # Crossfade: how many seconds before a song's natural end the consumer
    # starts speaking the TTS announcement for whatever's currently next in
    # queue, so the outgoing song's tail overlaps the announcement instead
    # of playing into dead silence first. See consumer_worker._announce_upcoming.
    crossfade_lead_seconds: float = float(os.getenv("CROSSFADE_LEAD_SECONDS", "8"))

    # Simple file-based signal: the API touches this file to request a skip,
    # and the consumer (polling while ffplay runs) deletes it once handled.
    # Both processes run on the same machine, so a shared file is enough IPC.
    skip_signal_file: str = os.getenv(
        "SKIP_SIGNAL_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".skip_signal"),
    )

    # Pause/resume signal: the API creates this file to pause playback and
    # deletes it to resume. The poll loop in play_youtube_audio() suspends
    # the ffplay process while the file exists.
    pause_signal_file: str = os.getenv(
        "PAUSE_SIGNAL_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pause_signal"),
    )

    # Seek signal: the API writes the target position (seconds, as a float
    # string) here. The poll loop restarts ffplay with -ss at that position.
    seek_signal_file: str = os.getenv(
        "SEEK_SIGNAL_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".seek_signal"),
    )

    # Stop signal: the API creates this file to halt all playback. While it
    # exists the consumer won't start the next song. POST /resume clears it.
    stop_signal_file: str = os.getenv(
        "STOP_SIGNAL_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".stop_signal"),
    )

    # Playback history: last 100 played songs, newest last. Written by the
    # consumer (via queue_state.mark_done) and read by GET /history.
    history_file: str = os.getenv(
        "HISTORY_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".history.json"),
    )

    # Persistent volume state (0.0–1.5 float). The API writes here when the
    # admin changes volume; the poll loop detects the change and restarts
    # ffplay at the current position with -af volume=<level>. Persists across
    # songs so a volume change sticks for the whole session.
    volume_file: str = os.getenv(
        "VOLUME_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".volume"),
    )

    # Shared queue-state file: tracks incremental song IDs and playback
    # progress so the API can report queue position / estimated wait time.
    queue_state_file: str = os.getenv(
        "QUEUE_STATE_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".queue_state.json"),
    )

    # Admin credentials gating the skip and queue-view endpoints. Fine as a
    # single hardcoded account for this local, single-user tool.
    # change password
    admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "")

    # Admin-managed fallback playlist: loops automatically whenever the real
    # (Kafka-backed) queue is empty. See default_playlist.py.
    default_playlist_file: str = os.getenv(
        "DEFAULT_PLAYLIST_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".default_playlist.json"),
    )

    # Admin-managed blacklist: banned YouTube video IDs and banned requesters
    # (phone numbers / Telegram ids / IPs). Checked first in
    # real_time_validation before anything else. See blacklist.py.
    blacklist_file: str = os.getenv(
        "BLACKLIST_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".blacklist.json"),
    )

    # Append-only analytics log: one event per song that actually starts
    # playing from the real queue (not the default playlist), with its source
    # and requester id. Powers the admin dashboard. See analytics.py.
    analytics_file: str = os.getenv(
        "ANALYTICS_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".analytics.json"),
    )
    # Cap on retained events so the file can't grow without bound.
    analytics_max_events: int = int(os.getenv("ANALYTICS_MAX_EVENTS", "5000"))

    # SQLite database file — replaces all the JSON files above for storage.
    # The old JSON file paths are kept for one-time migration on first run.
    db_file: str = os.getenv(
        "DB_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "uttarayan.db"),
    )


settings = Settings()
