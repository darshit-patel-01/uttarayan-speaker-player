"""
Admin announcements: the spool, the /announce endpoint, and the pause ->
play -> resume interrupt inside the player loop.
"""
import base64
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import announcements
from config import settings


@pytest.fixture(autouse=True)
def _isolated_spool(tmp_path, monkeypatch):
    monkeypatch.setattr(announcements, "SPOOL_DIR", str(tmp_path / "spool"))
    yield


@pytest.fixture()
def client():
    from producer_api import app
    return TestClient(app, raise_server_exceptions=False)


def _basic():
    raw = f"{settings.admin_username}:{settings.admin_password}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


# --- spool -----------------------------------------------------------------

def test_spool_starts_empty():
    assert announcements.pending() is False
    assert announcements._pop_next() is None


def test_clip_is_spooled_with_audio_and_sidecar():
    aid = announcements.enqueue_clip(b"OggS-fake", "audio/ogg", sender="admin")
    assert announcements.pending() is True
    meta = announcements._pop_next()
    assert meta["id"] == aid and meta["kind"] == "clip" and meta["sender"] == "admin"
    assert meta["audio"].endswith(".ogg") and os.path.exists(meta["audio"])
    assert announcements.pending() is False


@pytest.mark.parametrize("ctype, ext", [
    ("audio/ogg", ".ogg"), ("audio/opus", ".ogg"), ("audio/mpeg", ".mp3"),
    ("audio/mp4", ".m4a"), ("audio/aac", ".m4a"), ("audio/wav", ".wav"),
])
def test_clip_extension_follows_content_type(ctype, ext):
    announcements.enqueue_clip(b"x", ctype, sender="a")
    assert announcements._pop_next()["audio"].endswith(ext)


def test_announcements_play_in_arrival_order():
    announcements.enqueue_text("first", sender="a")
    announcements.enqueue_text("second", sender="a")
    announcements.enqueue_text("third", sender="a")
    assert [announcements._pop_next()["text"] for _ in range(3)] == ["first", "second", "third"]


def test_play_all_speaks_leadin_then_content_and_cleans_up():
    """Each announcement is: spoken lead-in, then clip or text. Clip files
    are deleted afterwards so the spool doesn't fill the disk."""
    announcements.enqueue_clip(b"OggS", "audio/ogg", sender="a")
    announcements.enqueue_text("kitchen closes soon", sender="a")
    clip_path = next(
        os.path.join(announcements.SPOOL_DIR, n) for n in os.listdir(announcements.SPOOL_DIR) if n.endswith(".ogg")
    )
    calls = []
    with patch.object(announcements, "_speak", lambda t: calls.append(("speak", t))), \
         patch.object(announcements, "_ffplay", lambda p, timeout=None: calls.append(("play", os.path.basename(p)))):
        played = announcements.play_all_pending()
    assert played == 2
    assert calls[0][0] == "speak" and "घोषणा" in calls[0][1]          # lead-in (Hindi default)
    assert calls[1] == ("play", os.path.basename(clip_path))
    assert calls[2][0] == "speak" and "घोषणा" in calls[2][1]
    assert calls[3] == ("speak", "kitchen closes soon")
    assert not os.path.exists(clip_path)
    assert announcements.pending() is False


def test_announcement_language_is_its_own_setting():
    """Song intros can be Hindi while admin announcements are English (or
    vice versa) — the lead-in and the voice follow announcement_language,
    not tts_language."""
    import runtime_config
    runtime_config.update({"tts_language": "hi", "announcement_language": "en"})
    try:
        announcements.enqueue_text("last orders", sender="a")
        spoken = []
        with patch.object(announcements, "_speak", lambda t: spoken.append(t)):
            announcements.play_all_pending()
        assert spoken == ["Admin announcement.", "last orders"]
        assert announcements._language() == "en"
        assert announcements.VOICES[announcements._language()].startswith("en-")
    finally:
        runtime_config.reset()


def test_unknown_announcement_language_falls_back_to_hindi():
    import runtime_config
    runtime_config.update({"announcement_language": "hi"})   # valid; then poke an invalid value straight in
    try:
        with patch.object(runtime_config, "get", lambda k: "xx" if k == "announcement_language" else None):
            assert announcements._language() == "hi"
    finally:
        runtime_config.reset()


def test_a_failing_announcement_does_not_block_the_next():
    announcements.enqueue_text("bad", sender="a")
    announcements.enqueue_text("good", sender="a")
    spoken = []

    def _speak(t):
        if t == "bad":
            raise RuntimeError("tts down")
        spoken.append(t)

    with patch.object(announcements, "_speak", _speak):
        played = announcements.play_all_pending()
    assert played == 1
    assert "good" in spoken
    assert announcements.pending() is False


# --- /announce endpoint ----------------------------------------------------

def test_text_announcement_is_queued(client):
    res = client.post("/announce", json={"text": "  Last   orders in 10 minutes  "}, headers=_basic())
    assert res.status_code == 200
    assert res.json()["kind"] == "text"
    assert announcements._pop_next()["text"] == "Last orders in 10 minutes"


def test_audio_announcement_is_queued(client):
    res = client.post("/announce", content=b"OggS-voice-note", headers={**_basic(), "Content-Type": "audio/ogg; codecs=opus"})
    assert res.status_code == 200
    assert res.json()["kind"] == "clip"
    meta = announcements._pop_next()
    assert meta["kind"] == "clip" and open(meta["audio"], "rb").read() == b"OggS-voice-note"


def test_announce_requires_admin(client):
    assert client.post("/announce", json={"text": "hi"}).status_code == 401


_BAD_INPUTS = [
    (b"", "audio/ogg", 422, "empty clip"),
    (b"x" * (announcements.MAX_CLIP_BYTES + 1), "audio/ogg", 413, "oversized clip"),
    (json.dumps({"text": ""}).encode(), "application/json", 422, "blank text"),
    (json.dumps({"text": "x" * 301}).encode(), "application/json", 422, "text too long"),
    (b"hello", "text/plain", 415, "unsupported content type"),
    (b"<html>", "image/png", 415, "not audio"),
]


# ids= keeps the 8 MB payload out of the test name — pytest exports the
# current test id as an env var, and Windows caps those at 32 KB.
@pytest.mark.parametrize("payload, ctype, expected, label", _BAD_INPUTS, ids=[c[3] for c in _BAD_INPUTS])
def test_announce_rejects_bad_input(client, payload, ctype, expected, label):
    res = client.post("/announce", content=payload, headers={**_basic(), "Content-Type": ctype})
    assert res.status_code == expected, label
    assert announcements.pending() is False, f"{label}: nothing should be spooled"


# --- interrupt inside the player loop --------------------------------------

def test_player_loop_pauses_plays_and_resumes(monkeypatch):
    """Drive play_youtube_audio's poll loop with a fake mpv and confirm an
    announcement arriving mid-song produces pause -> play -> resume, with
    the song's own pause/resume callbacks fired around it."""
    import playback

    ipc = []
    events = []

    class FakeProc:
        def __init__(self):
            self.polls = 0
        def poll(self):
            self.polls += 1
            return 0 if self.polls > 6 else None      # "song ends" after a few ticks
        def wait(self, timeout=None): pass
        def terminate(self): pass
        def kill(self): pass

    monkeypatch.setattr(playback.subprocess, "Popen", lambda cmd: FakeProc())
    monkeypatch.setattr(playback, "_mpv_connect", lambda p, timeout=5.0: "pipe")
    monkeypatch.setattr(playback, "_mpv_cmd", lambda pipe, cmd: ipc.append(cmd))
    monkeypatch.setattr(playback, "_mpv_close", lambda pipe: None)
    monkeypatch.setattr(playback, "get_volume", lambda: 1.0)
    monkeypatch.setattr(playback, "is_stopped", lambda: False)
    monkeypatch.setattr(playback.time, "sleep", lambda s: None)
    monkeypatch.setattr(playback, "_POLL_INTERVAL_SECONDS", 0)

    # Announcement is pending on the 2nd poll only.
    state = {"tick": 0}
    def _pending():
        state["tick"] += 1
        return state["tick"] == 2
    monkeypatch.setattr(announcements, "pending", _pending)
    monkeypatch.setattr(announcements, "play_all_pending", lambda: events.append("announce") or 1)

    finished = playback.play_youtube_audio(
        "https://youtu.be/x",
        prefetched_path=__file__,                # any existing file will do
        on_pause=lambda: events.append("on_pause"),
        on_resume=lambda: events.append("on_resume"),
    )

    assert finished is True
    assert events == ["on_pause", "announce", "on_resume"]
    pauses = [c for c in ipc if c[:2] == ["set_property", "pause"]]
    assert pauses == [["set_property", "pause", True], ["set_property", "pause", False]]


def test_player_loop_leaves_song_paused_if_admin_paused_during_announcement(monkeypatch, tmp_path):
    """If the admin hits Pause while the announcement is playing, the song
    must stay paused afterwards instead of being force-resumed."""
    import playback

    ipc, events = [], []

    class FakeProc:
        def __init__(self): self.polls = 0
        def poll(self):
            self.polls += 1
            return 0 if self.polls > 6 else None
        def wait(self, timeout=None): pass
        def terminate(self): pass
        def kill(self): pass

    pause_file = tmp_path / ".pause"
    monkeypatch.setattr(settings, "pause_signal_file", str(pause_file))
    monkeypatch.setattr(playback.subprocess, "Popen", lambda cmd: FakeProc())
    monkeypatch.setattr(playback, "_mpv_connect", lambda p, timeout=5.0: "pipe")
    monkeypatch.setattr(playback, "_mpv_cmd", lambda pipe, cmd: ipc.append(cmd))
    monkeypatch.setattr(playback, "_mpv_close", lambda pipe: None)
    monkeypatch.setattr(playback, "get_volume", lambda: 1.0)
    monkeypatch.setattr(playback, "is_stopped", lambda: False)
    monkeypatch.setattr(playback.time, "sleep", lambda s: None)
    monkeypatch.setattr(playback, "_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(playback, "request_resume", lambda: None)

    state = {"tick": 0}
    monkeypatch.setattr(announcements, "pending", lambda: (state.__setitem__("tick", state["tick"] + 1) or state["tick"] == 2))
    def _announce():
        pause_file.write_text("")                  # admin presses Pause mid-announcement
        events.append("announce")
        return 1
    monkeypatch.setattr(announcements, "play_all_pending", _announce)

    playback.play_youtube_audio("https://youtu.be/x", prefetched_path=__file__,
                                on_pause=lambda: events.append("on_pause"),
                                on_resume=lambda: events.append("on_resume"))

    # The announcement's own resume must NOT have fired; the later regular
    # pause-handling keeps it paused (no ["pause", False] until the file goes).
    idx = events.index("announce")
    assert "on_resume" not in events[idx:idx + 2]
    assert ["set_property", "pause", False] not in ipc[: ipc.index(["set_property", "pause", True]) + 2]
