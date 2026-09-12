"""
The "Song announcements" switch. Every TTS trigger (pre-play, playlist,
crossfade thread) funnels through consumer_worker._tts_announce, so gating
that one function is what makes the admin toggle actually silence the voice.
"""
from unittest.mock import patch

import pytest

import consumer_worker
import runtime_config


@pytest.fixture(autouse=True)
def _clean_config():
    yield
    runtime_config.reset()


def test_announcements_default_on():
    assert runtime_config.get("announcements_enabled") is True


def test_off_skips_speech_entirely():
    """With the toggle off nothing must be synthesised or played — no TTS
    request, no ffplay, no temp file."""
    runtime_config.update({"announcements_enabled": False})
    with patch.object(consumer_worker.subprocess, "run") as speak, \
         patch.object(consumer_worker.tempfile, "NamedTemporaryFile") as tmp:
        consumer_worker._tts_announce("Some Song", "for", "me")
    speak.assert_not_called()
    tmp.assert_not_called()


def _speaker_stub(played):
    """Records the ffplay invocation but lets every other subprocess call
    (edge_tts's import probes platform.uname() on Windows) run for real."""
    real_run = consumer_worker.subprocess.run

    def run(cmd, *a, **k):
        if cmd and cmd[0] == "ffplay":
            played.append(cmd[0])
            return None
        return real_run(cmd, *a, **k)
    return run


def test_on_reaches_synthesis():
    """Sanity check that the gate lets announcements through when enabled:
    with a stubbed synthesiser the speaker is invoked."""
    played = []
    with patch.object(consumer_worker.subprocess, "run", _speaker_stub(played)), \
         patch("asyncio.run", lambda coro: coro.close()):
        consumer_worker._tts_announce("Some Song")
    assert played == ["ffplay"]


def test_toggle_takes_effect_on_next_call_without_restart():
    """The setting is read per call, so an admin flipping it mid-session
    changes the very next announcement."""
    played = []
    with patch.object(consumer_worker.subprocess, "run", _speaker_stub(played)), \
         patch("asyncio.run", lambda coro: coro.close()):
        runtime_config.update({"announcements_enabled": False})
        consumer_worker._tts_announce("First")
        assert played == []
        runtime_config.update({"announcements_enabled": True})
        consumer_worker._tts_announce("Second")
        assert played == ["ffplay"]
