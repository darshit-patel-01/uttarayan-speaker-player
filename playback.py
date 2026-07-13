import logging
import os
import shutil
import subprocess
import tempfile
import time

import psutil
import yt_dlp

import runtime_config
from config import settings

logger = logging.getLogger("playback")

_COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")

YDL_DOWNLOAD_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    # Use the iOS player client — it returns CDN URLs that don't 403.
    # The web client's download URLs are increasingly blocked by YouTube's
    # bot-detection even when cookies are present; iOS bypasses this.
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web"],
        }
    },
    # Place a cookies.txt (Netscape format) exported from your browser in the
    # project root to bypass YouTube 403s.  Export it once using the
    # "Get cookies.txt LOCALLY" Chrome extension while logged into YouTube.
    # If the file doesn't exist, yt-dlp proceeds without cookies.
    **({"cookiefile": _COOKIES_FILE} if os.path.exists(_COOKIES_FILE) else {}),
}

_POLL_INTERVAL_SECONDS = 0.2


def download_audio(youtube_url: str, dest_dir: str) -> str:
    """Public wrapper — downloads best audio to dest_dir and returns the local path."""
    return _download_audio(youtube_url, dest_dir)


def _download_audio(youtube_url: str, dest_dir: str) -> str:
    """
    Downloads the best audio track to dest_dir and returns the local file path.

    Downloading first (instead of streaming straight from YouTube's CDN into
    ffplay) trades a short startup delay for reliability: yt-dlp's downloader
    retries properly on a dropped connection, whereas ffplay reading directly
    off the CDN would just cut the song short (TLS/IO error -10054) with no
    way to recover mid-stream.
    """
    outtmpl = os.path.join(dest_dir, "%(id)s.%(ext)s")
    opts = {**YDL_DOWNLOAD_OPTS, "outtmpl": outtmpl}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(youtube_url, download=True)
        return ydl.prepare_filename(info)


# ---------------------------------------------------------------------------
# Signal helpers — file-based IPC between the API process and the consumer
# ---------------------------------------------------------------------------

def _clear_skip_signal() -> None:
    try:
        os.remove(settings.skip_signal_file)
    except FileNotFoundError:
        pass


def request_skip() -> None:
    """Called by the API to signal that the currently playing track should be skipped."""
    with open(settings.skip_signal_file, "w") as f:
        f.write("skip")


def request_pause() -> None:
    """Called by the API to pause the currently playing track."""
    with open(settings.pause_signal_file, "w") as f:
        f.write("pause")


def request_resume() -> None:
    """Called by the API to resume a paused or stopped track."""
    try:
        os.remove(settings.pause_signal_file)
    except FileNotFoundError:
        pass
    clear_stop()


def is_paused() -> bool:
    """Returns True if a pause signal is currently active."""
    return os.path.exists(settings.pause_signal_file)


def request_seek(seconds: float) -> None:
    """Called by the API to seek to `seconds` into the current track."""
    with open(settings.seek_signal_file, "w") as f:
        f.write(str(seconds))


def _get_seek_target() -> float | None:
    """Reads and returns the seek target (seconds), or None if no seek pending."""
    try:
        with open(settings.seek_signal_file, "r") as f:
            return float(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def _clear_seek_signal() -> None:
    try:
        os.remove(settings.seek_signal_file)
    except FileNotFoundError:
        pass


def request_stop() -> None:
    """Called by the API to halt all playback (current song + block next song)."""
    with open(settings.stop_signal_file, "w") as f:
        f.write("stop")


def clear_stop() -> None:
    """Clears the stop signal so the consumer resumes normal operation."""
    try:
        os.remove(settings.stop_signal_file)
    except FileNotFoundError:
        pass


def is_stopped() -> bool:
    """Returns True if the stop signal is active."""
    return os.path.exists(settings.stop_signal_file)


# ---------------------------------------------------------------------------
# Volume helpers
# ---------------------------------------------------------------------------

def get_volume() -> float:
    """Returns the current volume level (0.0–1.5, default 1.0)."""
    try:
        with open(settings.volume_file, "r") as f:
            v = float(f.read().strip())
            return max(0.0, min(1.5, v))
    except (FileNotFoundError, ValueError):
        return 1.0


def set_volume(level: float) -> None:
    """Persists the desired volume level. The poll loop picks it up automatically."""
    level = max(0.0, min(1.5, level))
    with open(settings.volume_file, "w") as f:
        f.write(str(level))


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------

def play_youtube_audio(
    youtube_url: str,
    interrupt_check=None,
    on_pause=None,
    on_resume=None,
    on_seek=None,
    prefetched_path: str | None = None,
    duration: float | None = None,
    on_near_end=None,
) -> bool:
    """
    Downloads the audio locally, then plays it via ffplay, blocking until
    playback finishes, a skip/stop is requested, or interrupt_check() returns
    True — whichever comes first.

    Callbacks (all optional, called from the consumer process):
      on_pause()         — called the moment ffplay is suspended
      on_resume()        — called the moment ffplay is resumed
      on_seek(offset)    — called after ffplay restarts at `offset` seconds
      on_near_end()      — called once, when playback reaches
                            settings.crossfade_lead_seconds before the end
                            (needs `duration`; no-op if duration is unknown).
                            Exceptions are caught and logged so a crossfade
                            failure can't take down the current song.

    Returns True if playback completed normally, False if cut short.
    """
    _clear_skip_signal()
    _clear_seek_signal()

    # Use pre-fetched file if available; otherwise download now.
    if prefetched_path and os.path.exists(prefetched_path):
        _tmp_dir = None
        local_path = prefetched_path
    else:
        _tmp_dir = tempfile.mkdtemp(prefix="ytplayer_")
        try:
            local_path = _download_audio(youtube_url, _tmp_dir)
        except Exception:
            shutil.rmtree(_tmp_dir, ignore_errors=True)
            raise

    try:
        # Track local playback position so volume changes can restart at the
        # right spot without going through the seek-signal mechanism.
        _ffplay_seek_offset = 0.0
        _ffplay_start = time.time()
        _current_volume = get_volume()

        def _start_ffplay(start_seconds: float = 0.0, volume: float = 1.0) -> subprocess.Popen:
            cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error"]
            if start_seconds > 0:
                cmd += ["-ss", str(start_seconds)]
            filters = []
            if runtime_config.get("normalize_volume"):
                filters.append(f"loudnorm=I={runtime_config.get('loudnorm_target_lufs')}:TP=-1.5:LRA=11")
            if abs(volume - 1.0) > 0.01:
                filters.append(f"volume={volume}")
            if filters:
                cmd += ["-af", ",".join(filters)]
            cmd.append(local_path)
            return subprocess.Popen(cmd)

        process = _start_ffplay(0.0, _current_volume)
        _paused = False
        _near_end_fired = False

        def _kill_process() -> None:
            """Terminate ffplay, resuming it first if suspended so it can exit cleanly."""
            nonlocal _paused
            if _paused:
                try:
                    psutil.Process(process.pid).resume()
                except psutil.NoSuchProcess:
                    pass
                _paused = False
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

        def _terminate(reason: str) -> bool:
            logger.info("%s, stopping playback of %s", reason, youtube_url)
            _kill_process()
            return False

        try:
            while True:
                ret = process.poll()
                if ret is not None:
                    if ret != 0:
                        raise RuntimeError(f"ffplay exited with code {ret} for {youtube_url}")
                    return True

                # --- Stop signal -------------------------------------------
                if is_stopped():
                    return _terminate("Stop requested")

                # --- Skip signal -------------------------------------------
                if os.path.exists(settings.skip_signal_file):
                    return _terminate("Skip requested")

                # --- Interrupt check (default-playlist interrupt) ----------
                if interrupt_check is not None and interrupt_check():
                    return _terminate("Real song enqueued")

                # --- Seek signal -------------------------------------------
                seek_target = _get_seek_target()
                if seek_target is not None:
                    _clear_seek_signal()
                    if _paused:
                        try:
                            psutil.Process(process.pid).resume()
                        except psutil.NoSuchProcess:
                            pass
                        if on_resume:
                            on_resume()
                        _paused = False
                    try:
                        os.remove(settings.pause_signal_file)
                    except FileNotFoundError:
                        pass

                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

                    _ffplay_seek_offset = seek_target
                    _ffplay_start = time.time()
                    _current_volume = get_volume()

                    if on_seek:
                        on_seek(seek_target)

                    process = _start_ffplay(seek_target, _current_volume)
                    logger.info("Seeked to %.1fs for %s", seek_target, youtube_url)
                    continue

                # --- Volume change -----------------------------------------
                new_volume = get_volume()
                if abs(new_volume - _current_volume) > 0.01:
                    # Compute where we are in the song right now
                    current_elapsed = _ffplay_seek_offset + (time.time() - _ffplay_start)

                    # Kill old process (resume first if paused so psutil is happy)
                    was_paused = _paused
                    if _paused:
                        try:
                            psutil.Process(process.pid).resume()
                        except psutil.NoSuchProcess:
                            pass
                        _paused = False

                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

                    _current_volume = new_volume
                    _ffplay_seek_offset = current_elapsed
                    _ffplay_start = time.time()

                    process = _start_ffplay(current_elapsed, _current_volume)
                    logger.info("Volume changed to %.2f, restarted at %.1fs", new_volume, current_elapsed)

                    # Re-apply pause if it was paused before
                    if was_paused:
                        time.sleep(0.1)  # let ffplay initialise before suspending
                        try:
                            psutil.Process(process.pid).suspend()
                        except psutil.NoSuchProcess:
                            pass
                        _paused = True

                    # Notify seek listeners so queue_state stays accurate
                    if on_seek:
                        on_seek(current_elapsed)

                    continue

                # --- Crossfade: fire on_near_end once, near the natural end -
                if not _near_end_fired and not _paused and duration and on_near_end:
                    current_elapsed = _ffplay_seek_offset + (time.time() - _ffplay_start)
                    if current_elapsed >= duration - runtime_config.get("crossfade_lead_seconds"):
                        _near_end_fired = True
                        try:
                            on_near_end()
                        except Exception:
                            logger.exception("on_near_end callback failed for %s", youtube_url)

                # --- Pause / resume ----------------------------------------
                pause_wanted = os.path.exists(settings.pause_signal_file)
                if pause_wanted and not _paused:
                    try:
                        psutil.Process(process.pid).suspend()
                        logger.info("Paused %s", youtube_url)
                    except psutil.NoSuchProcess:
                        pass
                    _paused = True
                    if on_pause:
                        on_pause()
                elif not pause_wanted and _paused:
                    try:
                        psutil.Process(process.pid).resume()
                        logger.info("Resumed %s", youtube_url)
                    except psutil.NoSuchProcess:
                        pass
                    _paused = False
                    if on_resume:
                        on_resume()

                time.sleep(_POLL_INTERVAL_SECONDS)

        finally:
            _clear_skip_signal()
            _clear_seek_signal()
            request_resume()
    finally:
        if _tmp_dir:
            shutil.rmtree(_tmp_dir, ignore_errors=True)
