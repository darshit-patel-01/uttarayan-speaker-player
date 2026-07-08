import json
import logging
import signal

from confluent_kafka import Consumer, KafkaError

from config import settings
import default_playlist
from playback import play_youtube_audio
import queue_state

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("consumer_worker")

_shutdown = False


def _handle_shutdown(signum, frame):
    global _shutdown
    logger.info("Shutdown requested, will stop after the current video finishes...")
    _shutdown = True


def _play_default_song():
    """
    Called when the real queue is empty. Plays the next song from whichever
    admin-managed playlist is currently marked active (round-robin,
    looping), and bails out almost immediately if a real song gets
    enqueued mid-playback. No-op if there's no active playlist, or it's
    empty.
    """
    song = default_playlist.next_song()
    if song is None:
        return

    logger.info("Queue empty, playing default playlist song: %s", song.get("title") or song["url"])
    default_playlist.set_now_playing(song)
    try:
        finished = play_youtube_audio(song["url"], interrupt_check=queue_state.has_pending_songs)
        if finished:
            logger.info("Finished default playlist song: %s", song["url"])
        else:
            logger.info("Default playlist song interrupted: %s", song["url"])
    except Exception:
        logger.exception("Failed to play default playlist song %s, skipping to next", song["url"])
    finally:
        default_playlist.clear_now_playing()


def main():
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": settings.kafka_group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            "max.poll.interval.ms": settings.kafka_max_poll_interval_ms,
        }
    )
    consumer.subscribe([settings.kafka_topic])

    logger.info("Consumer started. Listening on topic '%s'...", settings.kafka_topic)

    try:
        while not _shutdown:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                if not queue_state.has_pending_songs():
                    _play_default_song()
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("Kafka error: %s", msg.error())
                continue

            try:
                value = json.loads(msg.value().decode("utf-8"))
            except Exception:
                logger.exception("Skipping unreadable message")
                consumer.commit(msg)
                continue

            url = value.get("url")
            song_id = value.get("id")
            if not url:
                logger.warning("Skipping message with no url: %s", value)
                consumer.commit(msg)
                continue

            if song_id is not None and queue_state.is_skip_requested(song_id):
                logger.info("Skip requested for id=%s before it started, skipping", song_id)
                queue_state.mark_done(song_id)
                consumer.commit(msg)
                continue

            if song_id is not None:
                queue_state.mark_playing(song_id)

            logger.info("Now playing (id=%s): %s", song_id, url)
            try:
                play_youtube_audio(url)
                logger.info("Finished: %s", url)
            except Exception:
                logger.exception("Failed to play %s, skipping to next", url)
            finally:
                if song_id is not None:
                    queue_state.mark_done(song_id)
                # Commit only after the playback attempt completes, so a message
                # is never marked done until its audio has actually finished (or failed).
                consumer.commit(msg)
    finally:
        consumer.close()
        logger.info("Consumer stopped.")


if __name__ == "__main__":
    main()
