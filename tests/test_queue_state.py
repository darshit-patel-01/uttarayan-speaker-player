"""Tests for queue_state — the highest-connectivity module in the codebase."""
import time

import queue_state


def test_add_song_returns_id_position_wait():
    song_id, pos, wait = queue_state.add_song(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        duration=212.0,
        title="Never Gonna Give You Up",
        video_id="dQw4w9WgXcQ",
    )
    assert isinstance(song_id, str)
    assert len(song_id) == 4
    assert pos == 1
    assert wait == 0.0


def test_add_multiple_songs_increments_position():
    queue_state.add_song(url="https://youtu.be/abc1", duration=100)
    _, pos2, wait2 = queue_state.add_song(url="https://youtu.be/abc2", duration=200)
    assert pos2 == 2
    assert wait2 >= 99


def test_get_status_returns_none_for_missing():
    assert queue_state.get_status("nonexistent") is None


def test_get_status_returns_correct_data():
    sid, _, _ = queue_state.add_song(
        url="https://youtu.be/xyz", duration=60, title="Test Song"
    )
    status = queue_state.get_status(sid)
    assert status is not None
    assert status["id"] == sid
    assert status["status"] == "queued"
    assert status["title"] == "Test Song"
    assert status["position_in_queue"] == 1


def test_list_queue_returns_all_songs():
    queue_state.add_song(url="https://youtu.be/a", duration=30, title="Song A")
    queue_state.add_song(url="https://youtu.be/b", duration=40, title="Song B")
    items = queue_state.list_queue()
    assert len(items) == 2
    assert items[0]["title"] == "Song A"
    assert items[1]["title"] == "Song B"


def test_list_queue_skips_skip_requested_queued():
    sid1, _, _ = queue_state.add_song(url="https://youtu.be/a", duration=30)
    queue_state.add_song(url="https://youtu.be/b", duration=40)
    queue_state.mark_skip_requested(sid1)
    items = queue_state.list_queue()
    assert len(items) == 1


def test_get_next_queued():
    queue_state.add_song(url="https://youtu.be/first", duration=30, title="First")
    queue_state.add_song(url="https://youtu.be/second", duration=40, title="Second")
    nxt = queue_state.get_next_queued()
    assert nxt is not None
    assert nxt["title"] == "First"


def test_mark_downloading_and_playing():
    sid, _, _ = queue_state.add_song(url="https://youtu.be/dl", duration=120)
    queue_state.mark_downloading(sid)
    status = queue_state.get_status(sid)
    assert status["status"] == "downloading"

    queue_state.reset_started_at(sid)
    status = queue_state.get_status(sid)
    assert status["status"] == "playing"


def test_mark_done_removes_from_queue():
    sid, _, _ = queue_state.add_song(url="https://youtu.be/done", duration=60)
    queue_state.mark_done(sid)
    assert queue_state.get_status(sid) is None
    assert not queue_state.has_pending_songs()


def test_mark_paused_and_resumed():
    sid, _, _ = queue_state.add_song(url="https://youtu.be/pr", duration=60)
    queue_state.mark_playing(sid)
    queue_state.mark_paused(sid)
    progress = queue_state.get_playing_progress()
    assert progress is not None
    assert progress["is_paused"] is True

    queue_state.mark_resumed(sid)
    progress = queue_state.get_playing_progress()
    assert progress["is_paused"] is False


def test_reorder_queue():
    sid1, _, _ = queue_state.add_song(url="https://youtu.be/r1", duration=30, title="R1")
    sid2, _, _ = queue_state.add_song(url="https://youtu.be/r2", duration=30, title="R2")
    sid3, _, _ = queue_state.add_song(url="https://youtu.be/r3", duration=30, title="R3")
    queue_state.reorder_queue([sid3, sid1, sid2])
    items = queue_state.list_queue()
    assert [i["id"] for i in items] == [sid3, sid1, sid2]


def test_bump_to_front():
    sid1, _, _ = queue_state.add_song(url="https://youtu.be/b1", duration=30, title="B1")
    sid2, _, _ = queue_state.add_song(url="https://youtu.be/b2", duration=30, title="B2")
    sid3, _, _ = queue_state.add_song(url="https://youtu.be/b3", duration=30, title="B3")
    assert queue_state.bump_to_front(sid3) is True
    nxt = queue_state.get_next_queued()
    assert nxt["id"] == sid3


def test_clear_queued():
    queue_state.add_song(url="https://youtu.be/c1", duration=30)
    queue_state.add_song(url="https://youtu.be/c2", duration=30)
    removed = queue_state.clear_queued()
    assert removed == 2
    assert not queue_state.has_pending_songs()


def test_reset_stale_playing():
    sid, _, _ = queue_state.add_song(url="https://youtu.be/stale", duration=60)
    queue_state.mark_playing(sid)
    queue_state.reset_stale_playing()
    status = queue_state.get_status(sid)
    assert status["status"] == "queued"


def test_find_by_video_id():
    queue_state.add_song(
        url="https://youtu.be/abc", duration=60,
        title="ABC", video_id="abc",
    )
    found = queue_state.find_by_video_id("abc")
    assert found is not None
    assert found["title"] == "ABC"
    assert queue_state.find_by_video_id("nonexistent") is None


def test_current_wait():
    queue_state.add_song(url="https://youtu.be/w1", duration=100)
    queue_state.add_song(url="https://youtu.be/w2", duration=200)
    length, total_wait = queue_state.current_wait()
    assert length == 2
    assert total_wait >= 299


def test_history_recorded_on_play():
    sid, _, _ = queue_state.add_song(
        url="https://youtu.be/hist", duration=60, title="History Song", video_id="hist"
    )
    queue_state.mark_playing(sid)
    queue_state.mark_done(sid)
    history = queue_state.get_history()
    assert history["total"] >= 1
    assert any(s["title"] == "History Song" for s in history["songs"])


def test_dedication_fields_stored():
    sid, _, _ = queue_state.add_song(
        url="https://youtu.be/ded", duration=60,
        dedication="Mom", dedication_name="Darsh",
    )
    items = queue_state.list_queue()
    item = next(i for i in items if i["id"] == sid)
    assert item["dedication"] == "Mom"
    assert item["dedication_name"] == "Darsh"


def test_format_duration():
    assert queue_state.format_duration(None) == "unknown"
    assert queue_state.format_duration(65) == "1m 5s"
    assert queue_state.format_duration(3661) == "1h 1m 1s"
    assert queue_state.format_duration(45) == "45s"


def test_format_duration_hm():
    assert queue_state.format_duration_hm(None) == "unknown"
    assert queue_state.format_duration_hm(3661) == "1h 1m"
    assert queue_state.format_duration_hm(120) == "2m"
