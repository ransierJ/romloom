"""Persistent app settings in /config/settings.json — scraper credentials and
scrape defaults. Secrets are stored plaintext on the user's own NAS but are
never returned in full over the API (only a *_set boolean).
"""
from __future__ import annotations

import json
from typing import Any, Dict

from . import config

DEFAULTS: Dict[str, Any] = {
    "screenscraper": {"user": "", "password": ""},
    "igdb": {"client_id": "", "client_secret": ""},  # Phase 5.4
    "scrape": {"source": "screenscraper", "missing_only": True,
               "region": "us", "lang": "en"},
}

# Which leaf keys are secrets — masked on read, only updated when a value is sent.
_SECRET_KEYS = {"password", "client_secret"}


def _merge(base: dict, over: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    try:
        data = json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        data = {}
    return _merge(DEFAULTS, data if isinstance(data, dict) else {})


def save(data: dict) -> None:
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config.SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def masked(data: dict) -> dict:
    """Replace secret values with a boolean <key>_set for safe API exposure."""
    out: Dict[str, Any] = {}
    for section, val in data.items():
        if isinstance(val, dict):
            sec: Dict[str, Any] = {}
            for k, v in val.items():
                if k in _SECRET_KEYS:
                    sec[f"{k}_set"] = bool(v)
                else:
                    sec[k] = v
            out[section] = sec
        else:
            out[section] = val
    return out


def apply_patch(patch: dict) -> dict:
    """Merge a patch into stored settings and persist. For secret keys, an
    omitted/None value keeps the existing secret; a non-empty value replaces it;
    an empty string clears it."""
    current = load()
    merged = _merge(current, {})
    for section, val in (patch or {}).items():
        if not isinstance(val, dict):
            merged[section] = val
            continue
        target = merged.setdefault(section, {})
        for k, v in val.items():
            if v is None:
                continue  # leave unchanged
            target[k] = v
    save(merged)
    return merged
