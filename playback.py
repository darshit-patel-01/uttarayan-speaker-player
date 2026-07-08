import logging
import os
import subprocess
import tempfile
import time

import yt_dlp

from config import settings

logger = logging.getLogger("playback")

YDL_DOWNLOAD_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
}

_POLL_INTERVAL_SECONDS = 0.2


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


def _clear_skip_signal() -> None:
    try:
        os.remove(settings.skip_signal_file)
    except FileNotFoundError:
        pass


def request_skip() -> None:
    """Called by the API to signal that the currently playing track should be skipped."""
    with open(settings.skip_signal_file, "w") as f:
        f.write("skip")


def play_youtube_audio(youtube_url: str, interrupt_check=None) -> bool:
    """
    Downloads the audio locally, then plays it via ffplay, blocking until
    playback finishes, a skip is requested via request_skip(), or
    interrupt_check() (if given) returns True — whichever comes first. The
    downloaded file lives in a temp directory that's removed once playback
    ends, however it ends.

    interrupt_check is polled alongside the skip signal; it's used by
    consumer_worker.py to cut short a default-playlist song the instant a
    real song gets enqueued, without needing the skip-signal-file mechanism.

    Returns True if playback completed normally, False if it was cut short
    (skip requested or interrupt_check() returned True).
    """
    # Clear any stale skip request left over from before this track started
    # (e.g. a skip that arrived while nothing was playing).
    _clear_skip_signal()

    with tempfile.TemporaryDirectory(prefix="ytplayer_") as tmp_dir:
        local_path = _download_audio(youtube_url, tmp_dir)

        cmd = [
            "ffplay",
            "-nodisp",
            "-autoexit",
            "-loglevel", "error",
            local_path,
        ]
        process = subprocess.Popen(cmd)

        def _stop_early(reason: str) -> bool:
            logger.info("%s, stopping current playback of %s", reason, youtube_url)
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            return False

        try:
            while True:
                ret = process.poll()
                if ret is not None:
                    if ret != 0:
                        raise RuntimeError(f"ffplay exited with code {ret} for {youtube_url}")
                    return True

                if os.path.exists(settings.skip_signal_file):
                    return _stop_early("Skip requested")

                if interrupt_check is not None and interrupt_check():
                    return _stop_early("Real song enqueued")

                time.sleep(_POLL_INTERVAL_SECONDS)
        finally:
            _clear_skip_signal()
