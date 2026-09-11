"""
The SQLite-backed per-requester rate limiter that replaced the Redis one.

Fixed-window semantics: the first counted song opens a window, later songs
increment it, and once it expires the next song silently starts a fresh one.
"""
import sqlite3

import pytest

import runtime_config
from real_time_validation import rate_limiter


@pytest.fixture(autouse=True)
def _small_limits():
    runtime_config.update({"rate_limit_max_songs": 2, "rate_limit_window_seconds": 100})
    yield
    runtime_config.reset()


@pytest.fixture()
def clock(monkeypatch):
    """Lets a test move time forward without sleeping."""
    state = {"now": 1_000_000.0}
    monkeypatch.setattr(rate_limiter.time, "time", lambda: state["now"])
    return state


def test_fresh_requester_is_allowed(clock):
    assert rate_limiter.check("alice") == (True, 0)


def test_check_alone_does_not_consume_quota(clock):
    """check() is called before the expensive validation; only record()
    (after the song passes everything) should count."""
    for _ in range(10):
        rate_limiter.check("alice")
    assert rate_limiter.check("alice") == (True, 0)


def test_limit_is_enforced_after_max_songs(clock):
    rate_limiter.record("alice")
    assert rate_limiter.check("alice") == (True, 0)
    rate_limiter.record("alice")
    allowed, retry_after = rate_limiter.check("alice")
    assert allowed is False
    assert retry_after == 100


def test_retry_after_counts_down_as_time_passes(clock):
    rate_limiter.record("alice")
    rate_limiter.record("alice")
    clock["now"] += 30
    assert rate_limiter.check("alice") == (False, 70)


def test_window_expires_and_resets(clock):
    rate_limiter.record("alice")
    rate_limiter.record("alice")
    assert rate_limiter.check("alice")[0] is False

    clock["now"] += 100
    assert rate_limiter.check("alice") == (True, 0)

    # The next song opens a brand-new window rather than inheriting the old count.
    rate_limiter.record("alice")
    assert rate_limiter.check("alice") == (True, 0)


def test_window_starts_at_first_song_not_first_check(clock):
    rate_limiter.check("alice")
    clock["now"] += 50
    rate_limiter.record("alice")
    rate_limiter.record("alice")
    clock["now"] += 60  # 110s after the check, 60s after the first record
    assert rate_limiter.check("alice")[0] is False, "window should be anchored to record(), not check()"


def test_requesters_are_isolated(clock):
    rate_limiter.record("alice")
    rate_limiter.record("alice")
    assert rate_limiter.check("alice")[0] is False
    assert rate_limiter.check("bob") == (True, 0)


def test_limit_change_takes_effect_immediately(clock):
    """Admin edits in Settings are read live, not cached at import."""
    rate_limiter.record("alice")
    rate_limiter.record("alice")
    assert rate_limiter.check("alice")[0] is False
    runtime_config.update({"rate_limit_max_songs": 5})
    assert rate_limiter.check("alice") == (True, 0)


def test_fails_open_when_database_is_unavailable(monkeypatch, caplog):
    """A rate-limiter outage must never be able to stop the music."""
    class _BrokenDb:
        @staticmethod
        def get_conn():
            raise sqlite3.OperationalError("database is locked")

        @staticmethod
        def transaction():
            raise sqlite3.OperationalError("database is locked")

    # Swap only the limiter's view of the db module, so the rest of the test
    # session (fixture teardown included) keeps a working database.
    monkeypatch.setattr(rate_limiter, "db", _BrokenDb)

    assert rate_limiter.check("alice") == (True, 0)
    rate_limiter.record("alice")  # must not raise
    assert "Rate limiter unavailable" in caplog.text
