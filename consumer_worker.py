import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from confluent_kafka import Consumer, KafkaError

from config import settings
import default_playlist
from playback import download_audio, is_stopped, play_youtube_audio
import queue_state

# ---------------------------------------------------------------------------
# Pre-fetch cache: download the next song in the background while the
# current one is playing, eliminating the silent gap between tracks.
# ---------------------------------------------------------------------------
_prefetch: dict = {}   # song_id -> (tmp_dir, local_file_path)
_prefetch_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Crossfade: id of whichever song was already announced during the *previous*
# song's tail (via _announce_upcoming, playback.py's on_near_end hook), so
# the main loop doesn't announce it a second time right before it plays.
# Written and read from the same (main) thread, so no lock needed.
# ---------------------------------------------------------------------------
_last_announced_song_id: str | None = None


def _start_prefetch(song_id: str, url: str) -> None:
    """Kick off a background thread to download `url` for `song_id`."""
    def _worker():
        tmp_dir = tempfile.mkdtemp(prefix="ytplayer_pre_")
        try:
            path = download_audio(url, tmp_dir)
            with _prefetch_lock:
                # If already superseded, clean up immediately
                if song_id in _prefetch:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                else:
                    _prefetch[song_id] = (tmp_dir, path)
            logger.info("Pre-fetched id=%s", song_id)
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            logger.warning("Pre-fetch failed for id=%s", song_id)

    threading.Thread(target=_worker, daemon=True, name=f"prefetch-{song_id}").start()


def _pop_prefetch(song_id: str):
    """Return (tmp_dir, path) and remove from cache, or (None, None)."""
    with _prefetch_lock:
        return _prefetch.pop(song_id, (None, None))


def _discard_prefetch(song_id: str) -> None:
    """Drop a pre-fetched entry and delete its temp dir."""
    tmp_dir, _ = _pop_prefetch(song_id)
    if tmp_dir:
        shutil.rmtree(tmp_dir, ignore_errors=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("consumer_worker")

# ---------------------------------------------------------------------------
# TTS pre-announcement — cheerful Hindi female voice between songs.
# Uses edge-tts (Microsoft neural TTS, free, no API key).
# Voice: hi-IN-SwaraNeural (warm female Hindi voice).
# rate="+25%" and pitch="+8Hz" give it an upbeat, energetic feel.
# ---------------------------------------------------------------------------
def _tts_announce(title: str) -> None:
    import asyncio
    import edge_tts

    async def _generate(path: str) -> None:
        communicate = edge_tts.Communicate(
            text=f"अगला गाना है… {title}!",
            voice="hi-IN-SwaraNeural",
            rate="-10%",
            pitch="+8Hz",
        )
        await communicate.save(path)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp_path = f.name
        asyncio.run(_generate(tmp_path))
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp_path],
            timeout=30,
        )
    except Exception:
        logger.warning("TTS announcement failed, continuing without it")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _tts_announce_async(title: str) -> None:
    """Fire-and-forget variant for crossfading: runs _tts_announce on a
    background thread so it can overlap the tail of the still-playing
    current song instead of blocking the main playback loop."""
    threading.Thread(target=_tts_announce, args=(title,), daemon=True, name="tts-crossfade").start()


def _announce_upcoming(current_song_id: str):
    """
    Built as playback.py's on_near_end callback for a given song: re-checks
    (at the trigger point, not when the song started, so reordering/new
    arrivals mid-song are picked up correctly) what's next in the real queue
    and, if there is one, speaks its announcement now so it overlaps the
    tail of current_song_id instead of playing into silence after it ends.
    """
    def _fire():
        global _last_announced_song_id
        upcoming = queue_state.get_next_queued()
        if upcoming and upcoming["id"] != current_song_id:
            _last_announced_song_id = upcoming["id"]
            logger.info(
                "Crossfade: announcing upcoming id=%s during tail of id=%s",
                upcoming["id"], current_song_id,
            )
            _tts_announce_async(upcoming.get("title") or "the next song")
    return _fire


_shutdown = False


def _handle_shutdown(signum, frame):
    global _shutdown
    logger.info("Shutdown requested, will stop after the current video finishes...")
    _shutdown = True


def _wait_while_stopped() -> None:
    """Block here while the stop signal is active (e.g. admin pressed Stop)."""
    if not is_stopped():
        return
    logger.info("Playback stopped — waiting for resume signal.")
    while is_stopped() and not _shutdown:
        time.sleep(0.5)
    if not _shutdown:
        logger.info("Stop signal cleared, resuming.")


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
        finished = play_youtube_audio(
            song["url"],
            interrupt_check=queue_state.has_pending_songs,
            on_pause=default_playlist.mark_now_playing_paused,
            on_resume=default_playlist.mark_now_playing_resumed,
            on_seek=default_playlist.mark_now_playing_seeked,
        )
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

    # Reset any items left as 'playing' from a previous crashed/killed run.
    # Without this, get_next_queued() skips them (only looks for 'queued'),
    # has_pending_songs() returns True, and the consumer loops forever doing
    # nothing — then marks the next real song as playing on top of the stale one.
    queue_state.reset_stale_playing()

    try:
        while not _shutdown:
            # Block here if admin pressed Stop
            _wait_while_stopped()
            if _shutdown:
                break

            # ---------------------------------------------------------------------------
            # Phase 1: drain one Kafka message (non-blocking).
            #
            # Kafka is used only for reliable delivery of new song requests; the URL
            # and ordering live in queue_state.json (written by the API before Kafka
            # produce).  We commit each message as soon as it arrives so Kafka doesn't
            # re-deliver it on restart.  queue_state.json is the authoritative queue
            # (file-persisted, survives restarts) and the source of play order.
            # ---------------------------------------------------------------------------
            msg = consumer.poll(timeout=0.2)
            if msg is not None:
                if msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        logger.error("Kafka error: %s", msg.error())
                else:
                    try:
                        consumer.commit(message=msg)
                    except Exception:
                        logger.exception("Failed to commit Kafka message")

            # ---------------------------------------------------------------------------
            # Phase 2: find the next song queue_state wants us to play.
            #
            # get_next_queued() returns songs in the order stored in queue_state.json,
            # which the user may have reordered via drag-and-drop.  This is what makes
            # drag reordering actually affect play order.
            # ---------------------------------------------------------------------------
            next_item = queue_state.get_next_queued()
            if next_item is None:
                if not queue_state.has_pending_songs():
                    _play_default_song()
                # else: a song is playing right now (status="playing"); keep looping
                continue

            song_id = next_item["id"]
            url = next_item["url"]

            # Handle skip-before-play
            if queue_state.is_skip_requested(song_id):
                logger.info("Skip requested for id=%s before it started, discarding", song_id)
                _discard_prefetch(song_id)
                queue_state.mark_done(song_id)
                continue

            queue_state.mark_playing(song_id)

            # Normally this song was already announced during the previous
            # song's tail (crossfade, see _announce_upcoming below) — only
            # fall back to a blocking pre-announce here if that didn't
            # happen (first song after startup/idle, after the default
            # playlist, or after a reorder that skipped past it).
            global _last_announced_song_id
            if song_id != _last_announced_song_id:
                _tts_announce(next_item.get("title") or "the next song")
            _last_announced_song_id = None

            # Start pre-fetching the next queued song while this one plays
            next_queued = queue_state.get_next_queued()
            if next_queued:
                with _prefetch_lock:
                    already = next_queued["id"] in _prefetch
                if not already:
                    _start_prefetch(next_queued["id"], next_queued["url"])

            # Use pre-fetched file if ready, else play_youtube_audio downloads
            pre_tmp, pre_path = _pop_prefetch(song_id)

            logger.info("Now playing (id=%s): %s", song_id, url)
            try:
                play_youtube_audio(
                    url,
                    on_pause=lambda: queue_state.mark_paused(song_id),
                    on_resume=lambda: queue_state.mark_resumed(song_id),
                    on_seek=lambda offset: queue_state.mark_seeked(song_id, offset),
                    prefetched_path=pre_path,
                    duration=next_item.get("duration"),
                    on_near_end=_announce_upcoming(song_id),
                )
                logger.info("Finished: %s", url)
            except Exception:
                logger.exception("Failed to play %s, skipping to next", url)
            finally:
                if pre_tmp:
                    shutil.rmtree(pre_tmp, ignore_errors=True)
                queue_state.mark_done(song_id)

            # Block here if Stop was pressed while the song was playing
            _wait_while_stopped()

    finally:
        consumer.close()
        logger.info("Consumer stopped.")


if __name__ == "__main__":
    main()
