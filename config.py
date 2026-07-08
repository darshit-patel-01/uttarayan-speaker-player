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

    # Simple file-based signal: the API touches this file to request a skip,
    # and the consumer (polling while ffplay runs) deletes it once handled.
    # Both processes run on the same machine, so a shared file is enough IPC.
    skip_signal_file: str = os.getenv(
        "SKIP_SIGNAL_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".skip_signal"),
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


settings = Settings()
