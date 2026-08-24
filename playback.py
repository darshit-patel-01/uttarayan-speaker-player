import ctypes
from ctypes import wintypes
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time

import yt_dlp

import runtime_config
from config import settings

logger = logging.getLogger("playback")

_MPV_EXE = shutil.which("mpv") or r"C:\Program Files\MPV Player\mpv.exe"

_COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")

YDL_DOWNLOAD_OPTS = {
    "format": "bestaudio*/best*",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "extractor_args": {
        "youtube": {
            "player_client": ["ios", "android", "web"],
        }
    },
    **({"cookiefile": _COOKIES_FILE} if os.path.exists(_COOKIES_FILE) else {}),
}

_POLL_INTERVAL_SECONDS = 0.2
_active_mpv: subprocess.Popen | None = None


def kill_active_player() -> None:
    if _active_mpv is not None and _active_mpv.poll() is None:
        _active_mpv.terminate()
        try:
            _active_mpv.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _active_mpv.kill()

_download_progress: dict = {"percent": 0}


def get_download_progress() -> dict:
    return dict(_download_progress)


def _dl_progress_hook(d: dict) -> None:
    if d["status"] == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded_bytes", 0)
        _download_progress["percent"] = round(downloaded / total * 100) if total else 0
    elif d["status"] == "finished":
        _download_progress["percent"] = 100


def download_audio(youtube_url: str, dest_dir: str) -> str:
    """Public wrapper — downloads best audio to dest_dir and returns the local path."""
    return _download_audio(youtube_url, dest_dir)


def _download_audio(youtube_url: str, dest_dir: str, track_progress: bool = False) -> str:
    outtmpl = os.path.join(dest_dir, "%(id)s.%(ext)s")
    opts = {**YDL_DOWNLOAD_OPTS, "outtmpl": outtmpl}
    if track_progress:
        _download_progress["percent"] = 0
        opts["progress_hooks"] = [_dl_progress_hook]
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
    with open(settings.skip_signal_file, "w") as f:
        f.write("skip")


def request_pause() -> None:
    with open(settings.pause_signal_file, "w") as f:
        f.write("pause")


def request_resume() -> None:
    try:
        os.remove(settings.pause_signal_file)
    except FileNotFoundError:
        pass
    clear_stop()


def is_paused() -> bool:
    return os.path.exists(settings.pause_signal_file)


def request_seek(seconds: float) -> None:
    with open(settings.seek_signal_file, "w") as f:
        f.write(str(seconds))


def _get_seek_target() -> float | None:
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
    with open(settings.stop_signal_file, "w") as f:
        f.write("stop")


def clear_stop() -> None:
    try:
        os.remove(settings.stop_signal_file)
    except FileNotFoundError:
        pass


def is_stopped() -> bool:
    return os.path.exists(settings.stop_signal_file)


# ---------------------------------------------------------------------------
# Volume helpers
# ---------------------------------------------------------------------------

def get_volume() -> float:
    try:
        with open(settings.volume_file, "r") as f:
            v = float(f.read().strip())
            return max(0.0, min(1.5, v))
    except (FileNotFoundError, ValueError):
        return 1.0


def set_volume(level: float) -> None:
    level = max(0.0, min(1.5, level))
    with open(settings.volume_file, "w") as f:
        f.write(str(level))


# ---------------------------------------------------------------------------
# mpv IPC — send commands via Windows named pipe (no external deps)
# ---------------------------------------------------------------------------

_mpv_pipe_seq = 0
_k32 = ctypes.WinDLL('kernel32', use_last_error=True)
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
_k32.WriteFile.restype = wintypes.BOOL
_k32.WriteFile.argtypes = [
    wintypes.HANDLE, ctypes.c_char_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
]
_INVALID_HANDLE = wintypes.HANDLE(-1).value
_GENERIC_RW = 0x80000000 | 0x40000000
_OPEN_EXISTING = 3


def _next_pipe_path() -> str:
    global _mpv_pipe_seq
    _mpv_pipe_seq += 1
    return rf"\\.\pipe\mpv-uttarayan-{os.getpid()}-{_mpv_pipe_seq}"


def _mpv_connect(pipe_path: str, timeout: float = 5.0):
    end = time.time() + timeout
    while time.time() < end:
        h = _k32.CreateFileW(pipe_path, _GENERIC_RW, 0, None, _OPEN_EXISTING, 0, None)
        if h != _INVALID_HANDLE:
            return h
        time.sleep(0.15)
    logger.warning("Could not connect to mpv IPC pipe: %s", pipe_path)
    return None


def _mpv_cmd(handle, cmd: list) -> None:
    if handle is None:
        return
    try:
        data = json.dumps({"command": cmd}).encode() + b"\n"
        written = wintypes.DWORD()
        _k32.WriteFile(handle, data, len(data), ctypes.byref(written), None)
    except Exception:
        pass


def _mpv_close(handle) -> None:
    if handle is not None:
        try:
            _k32.CloseHandle(handle)
        except Exception:
            pass


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
    on_playback_start=None,
) -> bool:
    """
    Downloads the audio locally, then plays it via mpv, blocking until
    playback finishes, a skip/stop is requested, or interrupt_check() returns
    True — whichever comes first.

    Volume changes are applied instantly via mpv's IPC socket — no restart,
    no audio gap.

    Returns True if playback completed normally, False if cut short.
    """
    _clear_skip_signal()
    _clear_seek_signal()

    if prefetched_path and os.path.exists(prefetched_path):
        _tmp_dir = None
        local_path = prefetched_path
    else:
        _tmp_dir = tempfile.mkdtemp(prefix="ytplayer_")
        try:
            local_path = _download_audio(youtube_url, _tmp_dir, track_progress=True)
        except Exception:
            shutil.rmtree(_tmp_dir, ignore_errors=True)
            raise

    try:
        _current_volume = get_volume()
        _playback_offset = 0.0
        _playback_start = time.time()

        af_filters = []
        if runtime_config.get("normalize_volume"):
            af_filters.append(
                f"loudnorm=I={runtime_config.get('loudnorm_target_lufs')}:TP=-1.5:LRA=11"
            )

        pipe_path = _next_pipe_path()
        cmd = [
            _MPV_EXE,
            "--no-video", "--vo=null", "--no-terminal",
            "--audio-display=no",
            f"--input-ipc-server={pipe_path}",
            f"--volume={round(_current_volume * 100)}",
        ]
        if af_filters:
            cmd.append(f"--af={','.join(af_filters)}")
        cmd.append(local_path)

        global _active_mpv
        process = subprocess.Popen(cmd)
        _active_mpv = process
        mpv_pipe = _mpv_connect(pipe_path)

        if on_playback_start:
            on_playback_start()

        _paused = False
        _near_end_fired = False

        def _kill() -> None:
            _mpv_cmd(mpv_pipe, ["quit"])
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

        def _terminate(reason: str) -> bool:
            logger.info("%s, stopping playback of %s", reason, youtube_url)
            _kill()
            return False

        try:
            while True:
                ret = process.poll()
                if ret is not None:
                    if ret != 0:
                        raise RuntimeError(f"mpv exited with code {ret} for {youtube_url}")
                    return True

                if is_stopped():
                    return _terminate("Stop requested")

                if os.path.exists(settings.skip_signal_file):
                    return _terminate("Skip requested")

                if interrupt_check is not None and interrupt_check():
                    return _terminate("Real song enqueued")

                # --- Seek (instant via IPC, no restart) --------------------
                seek_target = _get_seek_target()
                if seek_target is not None:
                    _clear_seek_signal()
                    if _paused:
                        try:
                            os.remove(settings.pause_signal_file)
                        except FileNotFoundError:
                            pass
                        _mpv_cmd(mpv_pipe, ["set_property", "pause", False])
                        _paused = False
                        if on_resume:
                            on_resume()
                    _mpv_cmd(mpv_pipe, ["seek", seek_target, "absolute"])
                    _playback_offset = seek_target
                    _playback_start = time.time()
                    if on_seek:
                        on_seek(seek_target)
                    logger.info("Seeked to %.1fs for %s", seek_target, youtube_url)
                    continue

                # --- Volume (instant via IPC, no restart) ------------------
                new_volume = get_volume()
                if abs(new_volume - _current_volume) > 0.01:
                    _current_volume = new_volume
                    _mpv_cmd(mpv_pipe, ["set_property", "volume", round(new_volume * 100)])

                # --- Crossfade: fire on_near_end once, near the end --------
                if not _near_end_fired and not _paused and duration and on_near_end:
                    current_elapsed = _playback_offset + (time.time() - _playback_start)
                    if current_elapsed >= duration - runtime_config.get("crossfade_lead_seconds"):
                        _near_end_fired = True
                        try:
                            on_near_end()
                        except Exception:
                            logger.exception("on_near_end callback failed for %s", youtube_url)

                # --- Pause / resume (via IPC, no process suspend) ----------
                pause_wanted = os.path.exists(settings.pause_signal_file)
                if pause_wanted and not _paused:
                    _mpv_cmd(mpv_pipe, ["set_property", "pause", True])
                    logger.info("Paused %s", youtube_url)
                    _paused = True
                    if on_pause:
                        on_pause()
                elif not pause_wanted and _paused:
                    _mpv_cmd(mpv_pipe, ["set_property", "pause", False])
                    logger.info("Resumed %s", youtube_url)
                    _paused = False
                    if on_resume:
                        on_resume()

                time.sleep(_POLL_INTERVAL_SECONDS)

        finally:
            _clear_skip_signal()
            _clear_seek_signal()
            request_resume()
            _mpv_close(mpv_pipe)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    finally:
        if _tmp_dir:
            shutil.rmtree(_tmp_dir, ignore_errors=True)
