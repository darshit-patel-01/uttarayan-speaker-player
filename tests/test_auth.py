"""
Admin authentication: the Bearer session tokens the web UI uses and the HTTP
Basic credentials the WhatsApp/Telegram bridges use.

Both schemes gate every admin endpoint through require_admin, so a regression
here is either a lockout or an auth bypass — and neither shows up by clicking
around the UI.
"""
import base64
import time

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import producer_api
from config import settings


@pytest.fixture()
def client():
    from producer_api import app
    return TestClient(app, raise_server_exceptions=False)


def _basic(username=None, password=None):
    u = settings.admin_username if username is None else username
    p = settings.admin_password if password is None else password
    return {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()}


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _signed(payload, secret=None):
    return jwt.encode(payload, secret or producer_api._SESSION_SECRET, algorithm="HS256")


def _request(headers):
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "scheme": "http",
        "server": ("testserver", 80),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    })


# --- Bearer session tokens -------------------------------------------------

def test_login_returns_a_token_that_authenticates(client):
    res = client.post("/login", headers=_basic())
    assert res.status_code == 200
    body = res.json()
    assert body["username"] == settings.admin_username
    assert body["expires_in"] == producer_api.SESSION_TTL_SECONDS

    me = client.get("/me", headers=_bearer(body["token"]))
    assert me.status_code == 200
    assert me.json()["username"] == settings.admin_username


def test_login_rejects_wrong_password(client):
    res = client.post("/login", headers=_basic(password="wrong"))
    assert res.status_code == 401


def test_session_token_unlocks_admin_endpoints(client):
    token = client.post("/login", headers=_basic()).json()["token"]
    assert client.get("/queue", headers=_bearer(token)).status_code == 200


@pytest.mark.parametrize("token, label", [
    ("", "empty"),
    ("not-a-jwt", "malformed"),
    (_signed({"sub": "admin", "exp": int(time.time()) - 1}), "expired"),
    (_signed({"sub": "admin", "exp": int(time.time()) + 3600}, secret="x" * 48), "foreign signature"),
])
def test_bad_session_tokens_are_rejected(client, token, label):
    assert client.get("/me", headers=_bearer(token)).status_code == 401, label


def test_tampered_token_is_rejected(client):
    token = client.post("/login", headers=_basic()).json()["token"]
    header, payload, signature = token.split(".")
    forged = f"{header}.{payload}.{'x' * len(signature)}"
    assert client.get("/me", headers=_bearer(forged)).status_code == 401


def test_restarting_the_server_invalidates_existing_tokens(client, monkeypatch):
    """
    The signing secret is regenerated per process, so tokens handed out before
    a restart must stop working — otherwise the UI keeps showing a stale admin
    as logged in against a server that never authenticated them.
    """
    token = client.post("/login", headers=_basic()).json()["token"]
    assert client.get("/me", headers=_bearer(token)).status_code == 200

    monkeypatch.setattr(producer_api, "_SESSION_SECRET", "secret-from-the-next-boot" + "-pad" * 8)
    assert client.get("/me", headers=_bearer(token)).status_code == 401


# --- HTTP Basic (bridges, test suite) --------------------------------------

def test_basic_credentials_still_work_for_bridges(client):
    """The bridges authenticate with credentials from .env and must survive
    restarts, so Basic stays supported alongside session tokens."""
    assert client.get("/queue", headers=_basic()).status_code == 200


@pytest.mark.parametrize("headers, label", [
    (_basic(password="wrong"), "wrong password"),
    (_basic(username="nobody"), "wrong username"),
    ({"Authorization": "Basic !!!not-base64!!!"}, "undecodable"),
    ({"Authorization": "Basic " + base64.b64encode(b"no-colon-here").decode()}, "no colon"),
])
def test_bad_basic_credentials_are_rejected(client, headers, label):
    assert client.get("/queue", headers=headers).status_code == 401, label


# --- Scheme handling -------------------------------------------------------

@pytest.mark.parametrize("headers, label", [
    ({}, "no header"),
    ({"Authorization": "Digest abc"}, "unsupported scheme"),
    ({"Authorization": "Bearer"}, "scheme with no value"),
])
def test_missing_or_unsupported_auth_is_rejected(client, headers, label):
    assert client.get("/queue", headers=headers).status_code == 401, label


def test_bearer_scheme_is_case_insensitive(client):
    token = client.post("/login", headers=_basic()).json()["token"]
    res = client.get("/me", headers={"Authorization": f"bearer {token}"})
    assert res.status_code == 200


# --- optional_admin --------------------------------------------------------

def test_optional_admin_returns_none_when_no_credentials_sent():
    """Endpoints open to everyone must treat a missing header as 'a stranger',
    not as an error."""
    assert producer_api.optional_admin(_request({})) is None


def test_optional_admin_accepts_either_scheme():
    token = producer_api.issue_session_token(settings.admin_username)
    assert producer_api.optional_admin(_request(_bearer(token))) == settings.admin_username
    assert producer_api.optional_admin(_request(_basic())) == settings.admin_username


def test_optional_admin_rejects_credentials_that_were_sent_but_are_wrong():
    """A typo'd admin login should fail loudly rather than silently downgrade
    to an anonymous request that then hits the rate limit."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        producer_api.optional_admin(_request(_basic(password="wrong")))
    assert exc.value.status_code == 401
