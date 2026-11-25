"""Arcade (MAME) reference metadata for filtering.

Arcade romsets are identified by their MAME short name (the zip stem, e.g.
``sf2``) — not by a file hash — so we join against the progettosnaps support
files instead of a No-Intro/Redump DAT:

- ``catver.ini``    -> category per set, plus a ``* Mature *`` marker on adult sets
- ``languages.ini`` -> the language of each set's text, a lightweight proxy for
                       "was this ever released in the US" (we treat Japanese /
                       Chinese / Korean-only sets as import-only)

Both are plain-text, keyless, and published by AntoPISA (progettosnaps) on
GitHub. Files are cached under CONFIG_DIR/mame/; a user may also drop their own
``catver.ini`` / ``languages.ini`` there (e.g. matching their MAME version) and
we never overwrite those on a normal scan — only an explicit refresh re-fetches.

Region accuracy here is deliberately approximate (language != release region);
the M3 arcade Missing view introduces real region data from the MAME DAT.
"""
from __future__ import annotations

import os
import time
import urllib.request
from pathlib import Path
from typing import Dict, Optional

from . import config

# Canonical system ids whose games are MAME-named arcade sets.
ARCADE_SYSTEMS = {"arcade", "mame", "fbneo", "neogeo", "neogeocd",
                  "cps1", "cps2", "cps3"}

# languages.ini sections we treat as "never released in the US" for the
# US/World-only filter. European languages are intentionally NOT included —
# their US availability is ambiguous and this filter errs toward keeping games.
NON_US_LANGS = {"Japanese", "Chinese", "Korean", "Taiwanese"}

# Top-level catver categories that are usually noise in a USA-focused arcade
# collection; used by the arcade Missing view (M3) to prune by default.
JUNK_TOP_CATS = {"Mahjong", "Casino", "Electromechanical", "System",
                 "Tabletop", "Utilities", "Gambling", "Misc.", "MultiGame"}

_RAW = "https://raw.githubusercontent.com/AntoPISA/MAME_SupportFiles/main"
SOURCES = {
    "catver.ini": f"{_RAW}/catver.ini/catver.ini",
    "languages.ini": f"{_RAW}/languages.ini/languages.ini",
}

MAME_DIR = Path(config.CONFIG_DIR) / "mame"
_MATURE_SUFFIX = "* Mature *"

# In-memory parse cache, invalidated when the source files change on disk.
_cache: Optional[Dict[str, dict]] = None
_cache_sig: Optional[tuple] = None


def is_arcade(system_id: str) -> bool:
    return system_id in ARCADE_SYSTEMS


def _path(name: str) -> Path:
    return MAME_DIR / name


def available() -> bool:
    """True once catver.ini is present (fetched or user-dropped)."""
    return _path("catver.ini").is_file()


def fetch(force: bool = False) -> dict:
    """Ensure the support files are present under CONFIG_DIR/mame. Only downloads
    a file that is missing (or, with force, re-fetches all) — a user-provided
    file is never overwritten on a normal scan. Returns a per-file status."""
    MAME_DIR.mkdir(parents=True, exist_ok=True)
    status: Dict[str, str] = {}
    for name, url in SOURCES.items():
        dest = _path(name)
        if dest.is_file() and not force:
            status[name] = "present"
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ROMLoom"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
            status[name] = "fetched"
        except Exception as e:  # noqa: BLE001 — surface, don't crash the scan
            status[name] = ("error: %s" % e) if not dest.is_file() else "kept-old"
    _invalidate()
    return status


def _invalidate() -> None:
    global _cache, _cache_sig
    _cache = None
    _cache_sig = None


def _sig() -> tuple:
    out = []
    for name in SOURCES:
        p = _path(name)
        out.append((name, p.stat().st_mtime if p.is_file() else 0))
    return tuple(out)


def parse_catver(text: str) -> Dict[str, tuple]:
    """{set_name: (full_category, top_category, is_mature)} from catver.ini."""
    out: Dict[str, tuple] = {}
    in_cat = False
    for line in text.splitlines():
        line = line.rstrip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("["):
            in_cat = line.strip() == "[Category]"
            continue
        if not in_cat or "=" not in line:
            continue
        name, _, cat = line.partition("=")
        name = name.strip().lower()
        cat = cat.strip()
        mature = cat.endswith(_MATURE_SUFFIX)
        if mature:
            cat = cat[: -len(_MATURE_SUFFIX)].strip()
        top = cat.split("/")[0].strip() if cat else None
        out[name] = (cat or None, top or None, mature)
    return out


def parse_languages(text: str) -> Dict[str, str]:
    """{set_name: language} from languages.ini (section = language)."""
    out: Dict[str, str] = {}
    cur: Optional[str] = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("["):
            sec = line[1:-1].strip()
            cur = None if sec in ("FOLDER_SETTINGS", "ROOT_FOLDER") else sec
            continue
        if cur is None:
            continue
        out[line.lower()] = cur
    return out


def load() -> Dict[str, dict]:
    """Merged {set_name: {cat, top, mature, lang}}. Cached until files change."""
    global _cache, _cache_sig
    if not available():
        return {}
    sig = _sig()
    if _cache is not None and sig == _cache_sig:
        return _cache
    try:
        cats = parse_catver(_path("catver.ini").read_text("utf-8", "ignore"))
    except OSError:
        return {}
    lp = _path("languages.ini")
    langs = parse_languages(lp.read_text("utf-8", "ignore")) if lp.is_file() else {}
    merged = {
        s: {"cat": cat, "top": top, "mature": mature, "lang": langs.get(s)}
        for s, (cat, top, mature) in cats.items()
    }
    _cache, _cache_sig = merged, sig
    return merged


def ingest(conn) -> int:
    """(Re)populate the arcade_catalog table from the support files. Returns the
    row count, or 0 if no data is available."""
    data = load()
    if not data:
        return 0
    conn.execute("DELETE FROM arcade_catalog")
    conn.executemany(
        "INSERT OR REPLACE INTO arcade_catalog"
        "(set_name, parent, is_clone, category, top_cat, is_mature, language, description) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [(s, None, 0, d["cat"], d["top"], int(d["mature"]), d["lang"], None)
         for s, d in data.items()],
    )
    return len(data)


def status() -> dict:
    """Summary for Settings: which files are present and how many sets/mature."""
    data = load()
    return {
        "available": available(),
        "dir": str(MAME_DIR),
        "files": {n: _path(n).is_file() for n in SOURCES},
        "sets": len(data),
        "mature": sum(1 for d in data.values() if d["mature"]),
    }
