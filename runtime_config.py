"""
Admin-tunable settings, overlaid on top of the env-backed defaults in
config.py and persisted to a small JSON file so BOTH processes — the API
(producer_api) and the player (consumer_worker) — pick up changes live,
without a restart.

Each tunable is read fresh from the file at the point of use (per request in
the API, per song in the consumer), so an admin edit in the Settings tab
takes effect on the next request / next song. The file only stores keys the
admin has actually overridden; everything else falls through to the config.py
default.
"""
import json
import os
import threading
from typing import Any, Optional

from config import settings

_lock = threading.Lock()

# key -> UI/validation metadata. `default` is pulled live from settings so the
# env value is always the fallback. Keep keys identical to Settings attribute
# names so get()/reset() can fall through to getattr(settings, key).
SPEC: dict = {
    "rate_limit_max_songs": {
        "type": "int", "min": 1, "max": 100, "unit": "songs",
        "label": "Songs per user per window",
        "help": "Max songs one requester can enqueue per window (admins are exempt).",
    },
    "rate_limit_window_seconds": {
        "type": "int", "min": 60, "max": 86400, "unit": "seconds",
        "label": "Rate-limit window",
        "help": "Length of the per-user rate-limit window.",
    },
    "max_queue_wait_seconds": {
        "type": "int", "min": 0, "max": 86400, "unit": "seconds",
        "label": "Max queue wait",
        "help": "Reject new requests once the queue's total wait already exceeds this. 0 disables it.",
    },
    "max_duration_seconds": {
        "type": "int", "min": 60, "max": 36000, "unit": "seconds",
        "label": "Max song length",
        "help": "Reject songs longer than this.",
    },
    "normalize_volume": {
        "type": "bool",
        "label": "Volume normalization",
        "help": "Even out song volumes with ffplay's loudnorm filter.",
    },
    "loudnorm_target_lufs": {
        "type": "float", "min": -40, "max": 0, "unit": "LUFS",
        "label": "Loudness target",
        "help": "Target loudness for normalization (-16 is the streaming standard).",
    },
    "crossfade_lead_seconds": {
        "type": "float", "min": 0, "max": 60, "unit": "seconds",
        "label": "Crossfade lead",
        "help": "How early the next song's announcement starts before the current one ends.",
    },
}


def _load() -> dict:
    if not os.path.exists(settings.runtime_config_file):
        return {}
    try:
        with open(settings.runtime_config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    tmp_path = settings.runtime_config_file + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, settings.runtime_config_file)


def get(key: str) -> Any:
    """Effective value for `key`: the admin override if set, else the
    env/config.py default."""
    overrides = _load()
    if key in overrides:
        return overrides[key]
    return getattr(settings, key)


def _coerce(key: str, value: Any) -> Any:
    spec = SPEC[key]
    t = spec["type"]
    if t == "int":
        coerced = int(value)
    elif t == "float":
        coerced = float(value)
    elif t == "bool":
        coerced = value.strip().lower() in ("1", "true", "yes", "on") if isinstance(value, str) else bool(value)
    else:
        raise ValueError(f"Unknown type for {key}")
    if t in ("int", "float"):
        if "min" in spec and coerced < spec["min"]:
            raise ValueError(f"{spec['label']} must be at least {spec['min']}")
        if "max" in spec and coerced > spec["max"]:
            raise ValueError(f"{spec['label']} must be at most {spec['max']}")
    return coerced


def update(changes: dict) -> dict:
    """Validate and persist a batch of overrides. Raises ValueError on a bad
    key or out-of-range value (nothing is saved if any value is invalid)."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("No settings provided")
    coerced = {}
    for key, value in changes.items():
        if key not in SPEC:
            raise ValueError(f"Unknown setting: {key}")
        coerced[key] = _coerce(key, value)
    with _lock:
        overrides = _load()
        overrides.update(coerced)
        _save(overrides)
    return get_all()


def reset(key: Optional[str] = None) -> dict:
    """Drop one override (back to its default), or all when key is None."""
    with _lock:
        if key is None:
            _save({})
        else:
            overrides = _load()
            overrides.pop(key, None)
            _save(overrides)
    return get_all()


def get_all() -> dict:
    """Every tunable with its current value, default, override flag, and UI
    metadata — for GET /config and the Settings tab."""
    overrides = _load()
    result = {}
    for key, meta in SPEC.items():
        default = getattr(settings, key)
        result[key] = {
            "value": overrides.get(key, default),
            "default": default,
            "overridden": key in overrides,
            **meta,
        }
    return result
