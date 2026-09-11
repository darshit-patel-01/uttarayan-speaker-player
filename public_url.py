"""
Detects whether the API port is exposed to the public internet through
Tailscale Funnel, so the share QR can point at the internet-reachable URL
instead of a localhost address nobody else can open.

Shelling out to the tailscale CLI takes a few hundred ms, so results are
cached briefly — the funnel is not something that flips on a per-request
basis.
"""
import json
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Optional

logger = logging.getLogger("public_url")

CACHE_TTL_SECONDS = 30

_lock = threading.Lock()
_cache: tuple[float, Optional[str]] = (0.0, None)

# On Windows the CLI isn't always on the PATH uvicorn inherits.
_WINDOWS_FALLBACK = r"C:\Program Files\Tailscale\tailscale.exe"


def _tailscale_cmd() -> str:
    if sys.platform == "win32" and os.path.exists(_WINDOWS_FALLBACK):
        return _WINDOWS_FALLBACK
    return "tailscale"


def _run_serve_status() -> Optional[dict]:
    kwargs = {}
    if sys.platform == "win32":
        # Keep a console window from flashing up on each poll.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            [_tailscale_cmd(), "serve", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def _host_to_url(hostport: str) -> str:
    host, sep, port = hostport.rpartition(":")
    if not sep:
        return f"https://{hostport}"
    return f"https://{host}" if port == "443" else f"https://{host}:{port}"


def _detect(port: int) -> Optional[str]:
    """
    Parses `tailscale serve status --json`, looking for a host that proxies to
    our port AND has Funnel switched on. A host served without Funnel is
    reachable only from the owner's own tailnet, so it is no more shareable
    than localhost and is deliberately ignored.
    """
    cfg = _run_serve_status()
    if not isinstance(cfg, dict):
        return None

    # Shapes are defensive throughout: this is another program's CLI output,
    # and a format change should degrade to "no public URL" rather than 500
    # the share endpoint.
    allow_funnel = cfg.get("AllowFunnel")
    web = cfg.get("Web")
    if not isinstance(allow_funnel, dict) or not isinstance(web, dict):
        return None

    suffix = f":{port}"
    for hostport, entry in web.items():
        if not allow_funnel.get(hostport) or not isinstance(entry, dict):
            continue
        handlers = entry.get("Handlers")
        if not isinstance(handlers, dict):
            continue
        for handler in handlers.values():
            if not isinstance(handler, dict):
                continue
            proxy = handler.get("Proxy")
            if isinstance(proxy, str) and proxy.rstrip("/").endswith(suffix):
                return _host_to_url(hostport)
    return None


def get_public_url(port: int, *, force_refresh: bool = False) -> Optional[str]:
    """The public Funnel URL serving `port`, or None when it isn't exposed."""
    global _cache
    now = time.monotonic()
    if not force_refresh:
        with _lock:
            checked_at, cached_url = _cache
            if checked_at and now - checked_at < CACHE_TTL_SECONDS:
                return cached_url

    url = _detect(port)
    with _lock:
        _cache = (now, url)
    return url
