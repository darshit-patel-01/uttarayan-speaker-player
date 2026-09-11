"""Integration tests for the FastAPI enqueue-to-queue flow."""
import base64
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from config import settings


def _auth_header(username=None, password=None):
    u = username or settings.admin_username
    p = password or settings.admin_password
    token = base64.b64encode(f"{u}:{p}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture()
def client():
    from producer_api import app
    return TestClient(app, raise_server_exceptions=False)


class _FakeValidationResult:
    is_valid = True
    reason = None
    video_id = "dQw4w9WgXcQ"
    metadata = {"duration": 212.0, "title": "Test Song", "uploader": "Test Channel"}


@pytest.fixture(autouse=True)
def _mock_externals():
    with patch("producer_api.validate_song_request", return_value=_FakeValidationResult()):
        yield


def test_enqueue_adds_to_queue(client):
    res = client.post(
        "/enqueue",
        json={"urls": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]},
        headers=_auth_header(),
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["enqueued"]) == 1
    assert data["enqueued"][0]["title"] == "Test Song"
    song_id = data["enqueued"][0]["id"]

    queue_res = client.get("/queue", headers=_auth_header())
    assert queue_res.status_code == 200
    queue = queue_res.json()["queue"]
    assert any(s["id"] == song_id for s in queue)


def test_enqueue_multiple_songs(client):
    res = client.post(
        "/enqueue",
        json={"urls": [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=xxxxxxxxxxx",
        ]},
        headers=_auth_header(),
    )
    assert res.status_code == 200
    assert len(res.json()["enqueued"]) == 2


def test_enqueue_rejected_url():
    from producer_api import app
    client = TestClient(app, raise_server_exceptions=False)
    reject = _FakeValidationResult()
    reject.is_valid = False
    reject.reason = "Age-restricted"
    with patch("producer_api.validate_song_request", return_value=reject):
        res = client.post(
            "/enqueue",
            json={"urls": ["https://www.youtube.com/watch?v=blocked"]},
        )
    assert res.status_code == 200
    data = res.json()
    assert len(data["enqueued"]) == 0
    assert len(data["rejected"]) == 1


def test_queue_empty_initially(client):
    res = client.get("/queue", headers=_auth_header())
    assert res.status_code == 200
    assert res.json()["queue"] == []


def test_now_playing_empty_initially(client):
    with patch("producer_api.default_playlist") as mock_dp:
        mock_dp.get_now_playing.return_value = None
        mock_dp.peek_next_song.return_value = None
        res = client.get("/now-playing")
    assert res.status_code == 200
    data = res.json()
    assert data["playing"] is None


def test_skip_nonexistent_song(client):
    res = client.post("/skip/nonexistent", headers=_auth_header())
    assert res.status_code == 404


def test_queue_clear(client):
    client.post(
        "/enqueue",
        json={"urls": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]},
        headers=_auth_header(),
    )
    res = client.post("/queue/clear", headers=_auth_header())
    assert res.status_code == 200
    assert res.json()["removed"] >= 1

    queue_res = client.get("/queue", headers=_auth_header())
    assert queue_res.json()["queue"] == []


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_wait_time(client):
    res = client.get("/wait-time")
    assert res.status_code == 200
    data = res.json()
    assert "queue_length" in data
    assert "estimated_wait_seconds" in data


def test_admin_auth_required(client):
    res = client.get("/queue")
    assert res.status_code == 401


def test_admin_wrong_password(client):
    res = client.get("/queue", headers=_auth_header(password="wrong"))
    assert res.status_code == 401
