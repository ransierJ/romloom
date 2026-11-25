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
import re
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
        "dat": dat_available(),
    }


# --------------------------------------------------------------------------- #
# Full arcade DAT (AntoPISA/MAME_Dats ARCADE.dat, Logiqx XML) — the authoritative
# "what exists" list with parent/clone + region-in-description + human titles.
# ~34 MB, so fetched on demand (when the arcade Missing view is opened), not on
# scan. Honors a user-dropped ARCADE.dat in /config/mame the same way.
# --------------------------------------------------------------------------- #
ARCADE_DAT = "ARCADE.dat"
ARCADE_DAT_URL = ("https://raw.githubusercontent.com/AntoPISA/MAME_Dats/"
                  "main/ARCADE_dat/ARCADE.dat")

# Region tokens parsed from a machine <description>, e.g. "(World, set 1)".
_US_REGIONS = {"world", "usa", "us", "universal", "export"}
_NONUS_REGIONS = {
    "japan", "japón", "euro", "europe", "asia", "korea", "taiwan", "china",
    "hong kong", "germany", "france", "italy", "spain", "netherlands", "brazil",
    "argentina", "sweden", "denmark", "finland", "norway", "russia", "portugal",
    "uk", "england",
}

_LABEL = {"us": "USA", "usa": "USA", "world": "World", "universal": "World",
          "export": "Export", "euro": "Europe", "europe": "Europe"}

_MACHINE_RE = re.compile(
    r'<machine\s+name="([^"]+)"([^>]*)>\s*<description>([^<]*)</description>')
_CLONEOF_RE = re.compile(r'cloneof="([^"]+)"')
_PAREN_RE = re.compile(r"\(([^)]*)\)")

_dat_cache: Optional[Dict[str, dict]] = None
_dat_sig: Optional[float] = None


def dat_available() -> bool:
    return _path(ARCADE_DAT).is_file()


def dat_meta() -> dict:
    p = _path(ARCADE_DAT)
    if not p.is_file():
        return {"available": False}
    m = {"available": True, "mtime": int(p.stat().st_mtime)}
    try:
        head = p.read_bytes()[:2000].decode("utf-8", "ignore")
        v = re.search(r"<version>([^<]+)</version>", head)
        if v:
            m["version"] = v.group(1)
    except OSError:
        pass
    return m


def fetch_dat(force: bool = False) -> str:
    """Download ARCADE.dat on demand (~34 MB). Skips if present unless force;
    never overwrites a user-dropped file unless force. Returns a status string."""
    dest = _path(ARCADE_DAT)
    if dest.is_file() and not force:
        return "present"
    MAME_DIR.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(ARCADE_DAT_URL, headers={"User-Agent": "ROMLoom"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read()
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    _invalidate_dat()
    return "fetched"


def _invalidate_dat() -> None:
    global _dat_cache, _dat_sig
    _dat_cache = None
    _dat_sig = None


def _region_of(desc: str):
    """(us_available: bool, label: str) from a machine description's region
    token. Region-free descriptions are treated as broadly available."""
    toks = set()
    for grp in _PAREN_RE.findall(desc):
        # Split on commas AND whitespace: region codes follow the region without
        # a comma, e.g. "(World 910522)" or "(Japan 911210, CPS-B-13)".
        for t in re.split(r"[,\s]+", grp):
            toks.add(t.strip().lower())
    for t in toks:
        if t in _US_REGIONS:
            return True, _LABEL.get(t, t.title())
    for t in toks:
        if t in _NONUS_REGIONS:
            return False, _LABEL.get(t, t.title())
    return True, ""   # region-free → assume available


def load_dat() -> Dict[str, dict]:
    """Parse ARCADE.dat into {set: {parent, clone, title, us, region}}. Skips
    BIOS/device/non-runnable machines. Cached until the file changes."""
    global _dat_cache, _dat_sig
    if not dat_available():
        return {}
    sig = _path(ARCADE_DAT).stat().st_mtime
    if _dat_cache is not None and sig == _dat_sig:
        return _dat_cache
    text = _path(ARCADE_DAT).read_text("utf-8", "ignore")
    out: Dict[str, dict] = {}
    for m in _MACHINE_RE.finditer(text):
        name, attrs, desc = m.group(1), m.group(2), m.group(3)
        if ('isdevice="yes"' in attrs or 'isbios="yes"' in attrs
                or 'runnable="no"' in attrs or 'ismechanical="yes"' in attrs):
            continue
        co = _CLONEOF_RE.search(attrs)
        us, label = _region_of(desc)
        out[name.lower()] = {
            "parent": co.group(1).lower() if co else None,
            "clone": bool(co),
            "title": desc,
            "us": us,
            "region": label,
        }
    _dat_cache, _dat_sig = out, sig
    return out


def arcade_missing(have_sets, *, include_clones=False, include_mature=False,
                   include_nonus=False, include_junk=False) -> dict:
    """Diff the arcade library against ARCADE.dat. `have_sets` is the set of
    MAME short names on disk. Returns reference totals + the missing list. The
    reference defaults to parent sets, US/World, non-adult, non-junk category."""
    dat = load_dat()
    cats = load()   # catver: category + mature
    if not dat:
        return {"imported": False}

    # A parent counts as "present" if the user has it OR any of its clones.
    present = set(have_sets)
    for s in have_sets:
        info = dat.get(s)
        if info and info["clone"] and info["parent"]:
            present.add(info["parent"])

    # A game family is US-available if ANY set in it is US — so a parent whose
    # only US release is a clone (e.g. Puck Man's US clone "pacman") still counts.
    family_us = {}
    for name, info in dat.items():
        if info["clone"] and info["us"] and info["parent"]:
            family_us[info["parent"]] = True

    reference = []
    for name, info in dat.items():
        if info["clone"] and not include_clones:
            continue
        if not (info["us"] or family_us.get(name)) and not include_nonus:
            continue
        cv = cats.get(name)
        if cv and cv["mature"] and not include_mature:
            continue
        top = cv["top"] if cv else None
        if top in JUNK_TOP_CATS and not include_junk:
            continue
        reference.append((name, info))

    have = sum(1 for n, _ in reference if n in present)
    missing = [{"title": info["title"], "set": n, "regions": info["region"]}
               for n, info in reference if n not in present]
    missing.sort(key=lambda r: r["title"].lower())
    return {"imported": True, "catalog_total": len(reference),
            "have": have, "missing": missing}
