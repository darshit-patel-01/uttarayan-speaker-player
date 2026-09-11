"""
Share-link plumbing: detecting the public Tailscale Funnel URL, and letting
the bridges report which account they're actually signed in as.

Both exist so the QR code points somewhere that works. The failure mode is a
QR that silently leads nowhere, which nobody notices until a guest tries it.
"""
import base64

import pytest
from fastapi.testclient import TestClient

import public_url
import runtime_config
from config import settings


@pytest.fixture()
def client():
    from producer_api import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear_public_url_cache():
    public_url._cache = (0.0, None)
    yield
    public_url._cache = (0.0, None)


def _basic():
    raw = f"{settings.admin_username}:{settings.admin_password}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


def _serve_status(host="box.tail1234.ts.net:443", proxy="http://127.0.0.1:8000", funnel=True):
    return {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {host: {"Handlers": {"/": {"Proxy": proxy}}}},
        "AllowFunnel": {host: funnel} if funnel is not None else {},
    }


# --- Funnel detection ------------------------------------------------------

@pytest.mark.parametrize("status, expected, label", [
    (_serve_status(), "https://box.tail1234.ts.net", "funnel serving our port"),
    (_serve_status(funnel=False), None, "served but funnel disabled"),
    (_serve_status(funnel=None), None, "no AllowFunnel entry at all"),
    (_serve_status(proxy="http://127.0.0.1:9999"), None, "funnel points at another port"),
    (_serve_status(proxy="http://127.0.0.1:8000/"), "https://box.tail1234.ts.net", "trailing slash on proxy"),
    ({}, None, "empty serve config"),
    ({"Web": {}}, None, "no web handlers"),
    (None, None, "tailscale CLI unavailable"),
])
def test_detects_only_a_public_funnel_on_our_port(monkeypatch, status, expected, label):
    """A host served without Funnel is reachable only from the owner's own
    tailnet, so it is no more shareable than localhost and must be ignored."""
    monkeypatch.setattr(public_url, "_run_serve_status", lambda: status)
    assert public_url.get_public_url(8000, force_refresh=True) == expected, label


def test_non_standard_port_is_kept_in_the_url(monkeypatch):
    monkeypatch.setattr(
        public_url, "_run_serve_status",
        lambda: _serve_status(host="box.tail1234.ts.net:8443"),
    )
    assert public_url.get_public_url(8000, force_refresh=True) == "https://box.tail1234.ts.net:8443"


def test_result_is_cached_so_every_request_does_not_shell_out(monkeypatch):
    calls = []

    def _fake():
        calls.append(1)
        return _serve_status()

    monkeypatch.setattr(public_url, "_run_serve_status", _fake)
    first = public_url.get_public_url(8000, force_refresh=True)
    for _ in range(5):
        public_url.get_public_url(8000)
    assert first == "https://box.tail1234.ts.net"
    assert len(calls) == 1


@pytest.mark.parametrize("status, label", [
    ({"Web": "not-a-dict", "AllowFunnel": {}}, "Web is not an object"),
    ({"Web": {"h:443": None}, "AllowFunnel": {"h:443": True}}, "null host entry"),
    ({"Web": {"h:443": {"Handlers": "nope"}}, "AllowFunnel": {"h:443": True}}, "handlers not an object"),
    ({"Web": {"h:443": {"Handlers": {"/": {"Proxy": 8000}}}}, "AllowFunnel": {"h:443": True}}, "proxy not a string"),
    ({"Web": {"h:443": {}}, "AllowFunnel": "yes"}, "AllowFunnel not an object"),
    ("a string, somehow", "output is not an object"),
])
def test_unexpected_cli_output_degrades_instead_of_crashing(monkeypatch, status, label):
    """tailscale's JSON is another program's contract — a format change should
    mean 'no public URL', not a 500 on the share endpoint."""
    monkeypatch.setattr(public_url, "_run_serve_status", lambda: status)
    assert public_url.get_public_url(8000, force_refresh=True) is None, label


# --- Which URL the QR advertises -------------------------------------------

def test_share_config_prefers_the_public_url(client, monkeypatch):
    monkeypatch.setattr(public_url, "_run_serve_status", lambda: _serve_status())
    body = client.get("/share/config").json()
    assert body["web_url"] == "https://box.tail1234.ts.net"
    assert body["public_url"] == "https://box.tail1234.ts.net"
    assert body["public_url_enabled"] is True


def test_share_config_falls_back_to_the_request_host_when_funnel_is_down(client, monkeypatch):
    monkeypatch.setattr(public_url, "_run_serve_status", lambda: None)
    body = client.get("/share/config").json()
    assert body["web_url"] == "http://testserver"
    assert body["public_url"] is None


def test_admin_can_opt_out_of_sharing_the_public_url(client, monkeypatch):
    monkeypatch.setattr(public_url, "_run_serve_status", lambda: _serve_status())
    runtime_config.update({"use_public_url": False})

    body = client.get("/share/config").json()
    assert body["web_url"] == "http://testserver"
    # Still reported, so the UI can explain why it isn't being used.
    assert body["public_url"] == "https://box.tail1234.ts.net"
    assert body["public_url_enabled"] is False


def test_web_qr_renders_from_the_resolved_url(client, monkeypatch):
    monkeypatch.setattr(public_url, "_run_serve_status", lambda: _serve_status())
    res = client.get("/share/qr?target=web")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/svg+xml"
    assert res.content


# --- Bridge self-registration ----------------------------------------------

def test_whatsapp_bridge_registers_its_own_number(client):
    res = client.post("/share/bridge-identity", json={"whatsapp_number": "919173386988"}, headers=_basic())
    assert res.json() == {"status": "updated", "whatsapp_number": "919173386988"}

    cfg = client.get("/share/config").json()
    assert cfg["whatsapp_number"] == "919173386988"
    assert cfg["whatsapp_auto_detected"] is True


def test_telegram_bridge_registers_its_own_username(client):
    res = client.post("/share/bridge-identity", json={"telegram_bot": "@my_bot"}, headers=_basic())
    assert res.json() == {"status": "updated", "telegram_bot": "my_bot"}

    cfg = client.get("/share/config").json()
    assert cfg["telegram_bot"] == "my_bot"
    assert cfg["telegram_auto_detected"] is True


@pytest.mark.parametrize("sent, stored", [
    ("+91 917-338-6988", "919173386988"),
    ("(919) 173386988", "919173386988"),
])
def test_phone_numbers_are_normalised(client, sent, stored):
    res = client.post("/share/bridge-identity", json={"whatsapp_number": sent}, headers=_basic())
    assert res.json()["whatsapp_number"] == stored


def test_reconnecting_bridge_does_not_rewrite_an_unchanged_value(client):
    client.post("/share/bridge-identity", json={"whatsapp_number": "919173386988"}, headers=_basic())
    again = client.post("/share/bridge-identity", json={"whatsapp_number": "919173386988"}, headers=_basic())
    assert again.json()["status"] == "unchanged"


def test_each_bridge_only_touches_its_own_field(client):
    client.post("/share/bridge-identity", json={"whatsapp_number": "919173386988"}, headers=_basic())
    client.post("/share/bridge-identity", json={"telegram_bot": "my_bot"}, headers=_basic())

    cfg = client.get("/share/config").json()
    assert cfg["whatsapp_number"] == "919173386988"
    assert cfg["telegram_bot"] == "my_bot"


def test_a_relinked_bridge_corrects_a_stale_manual_value(client):
    """The whole point: after relinking to a different phone, the QR must stop
    pointing at the old number without anyone remembering to edit it."""
    client.post(
        "/share/config",
        json={"whatsapp_number": "911111111111", "telegram_bot": "my_bot"},
        headers=_basic(),
    )
    assert client.get("/share/config").json()["whatsapp_number"] == "911111111111"

    client.post("/share/bridge-identity", json={"whatsapp_number": "919173386988"}, headers=_basic())

    cfg = client.get("/share/config").json()
    assert cfg["whatsapp_number"] == "919173386988"
    assert cfg["whatsapp_auto_detected"] is True


def test_manual_edit_clears_the_auto_detected_flag(client):
    client.post("/share/bridge-identity", json={"whatsapp_number": "919173386988"}, headers=_basic())
    client.post(
        "/share/config",
        json={"whatsapp_number": "911111111111", "telegram_bot": None},
        headers=_basic(),
    )
    assert client.get("/share/config").json().get("whatsapp_auto_detected") is not True


@pytest.mark.parametrize("payload, label", [
    ({}, "nothing provided"),
    ({"whatsapp_number": "abc"}, "number with no digits"),
    ({"telegram_bot": "   "}, "blank bot handle"),
])
def test_invalid_identity_payloads_are_refused(client, payload, label):
    res = client.post("/share/bridge-identity", json=payload, headers=_basic())
    assert res.status_code == 422, label


def test_bridge_identity_requires_admin_credentials(client):
    res = client.post("/share/bridge-identity", json={"whatsapp_number": "919173386988"})
    assert res.status_code == 401
