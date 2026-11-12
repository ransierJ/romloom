"""GameTDB scraper for Wii — matched by the 6-character GameID.

ScreenScraper (via Skyscraper) is weak for Wii: dumps are multi-GB, their hash
depends on the format (.wbfs/.iso/.rvz), and the inner file is named by the
GameID rather than a title. GameTDB is the Wii-native database (the source
WiiFlow / USB Loader GX use): art and metadata keyed directly by the GameID,
which ROMLoom already has in the folder name (`Title [RMCE01]`).

Art: art.gametdb.com/wii/<type>/<region>/<ID>.png — we prefer the 3D box render.
Metadata: the downloadable wiitdb.xml (cached), parsed by GameID. No API key.
GameCube is intentionally not handled here — those files are title-named, so
Skyscraper's filename matching already works for them.
"""
from __future__ import annotations

import re
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional

from . import config, libretro

HANDLED = {"wii", "wiiu"}

# Per-system GameTDB console path (art) + metadata DB.
_CONSOLE = {"wii": "wii", "wiiu": "wiiu"}
_DB = {
    "wii": ("https://www.gametdb.com/wiitdb.zip?LANG=EN", config.CONFIG_DIR / "wiitdb.xml"),
    "wiiu": ("https://www.gametdb.com/wiiutdb.zip?LANG=EN", config.CONFIG_DIR / "wiiutdb.xml"),
}
_ART_BASE = "https://art.gametdb.com/{console}/{type}/{region}/{gid}.png"
_DB_MAX_AGE = 30 * 86400          # refresh the metadata DB monthly

# Box-art preference: full 3D box first, then the flat full wrap, then plain 2D.
_ART_TYPES = ("cover3D", "coverfull", "cover")
# 4th char of a GameID encodes region -> GameTDB art-folder preference.
_REGION_MAP = {"E": "US", "P": "EN", "J": "JA", "K": "KO", "W": "EN", "X": "EN", "Y": "EN"}

_GID_RE = re.compile(r"\[([A-Z0-9]{6})\]")

_db_cache: dict = {}             # system_id -> {gid: metadata}


def handles(system_id: str) -> bool:
    return system_id in HANDLED


def game_id(stem: str) -> Optional[str]:
    """The 6-char GameID from a folder/file name like 'Mario Kart Wii [RMCE01]'."""
    m = _GID_RE.search(stem or "")
    return m.group(1) if m else None


def _regions_for(gid: str) -> list:
    primary = _REGION_MAP.get(gid[3], "US") if len(gid) >= 4 else "US"
    out, seen = [], set()
    for r in (primary, "US", "EN", "JA"):
        if r not in seen:
            seen.add(r); out.append(r)
    return out


def _http_get(url: str, timeout: int = 20) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ROMLoom"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 200:
                return r.read()
    except Exception:
        return None
    return None


def fetch_cover(gid: str, console: str = "wii") -> Optional[bytes]:
    """First available cover for a GameID: 3D box, then full wrap, then 2D."""
    for art_type in _ART_TYPES:
        for region in _regions_for(gid):
            data = _http_get(_ART_BASE.format(console=console, type=art_type,
                                              region=region, gid=gid))
            if data:
                return data
    return None


# --------------------------------------------------------------- metadata DB --
def _ensure_db(system_id: str) -> None:
    url, path = _DB[system_id]
    if path.is_file() and (time.time() - path.stat().st_mtime) < _DB_MAX_AGE:
        return
    blob = _http_get(url, timeout=60)
    if not blob:
        return
    try:
        with zipfile.ZipFile(BytesIO(blob)) as z:
            name = next((n for n in z.namelist() if n.endswith(".xml")), None)
            if name:
                config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                path.write_bytes(z.read(name))
    except Exception:
        pass


def _load_db(system_id: str) -> dict:
    if system_id in _db_cache:
        return _db_cache[system_id]
    _ensure_db(system_id)
    db: dict = {}
    try:
        root = ET.parse(_DB[system_id][1]).getroot()
    except (ET.ParseError, OSError):
        _db_cache[system_id] = db
        return db
    for game in root.findall("game"):
        gid = (game.findtext("id") or "").strip()
        if not gid:
            continue
        loc = game.find("locale")           # first locale = EN (filter=EN)
        title = loc.findtext("title") if loc is not None else None
        synopsis = loc.findtext("synopsis") if loc is not None else None
        date = game.find("date")
        releasedate = None
        if date is not None and date.get("year"):
            y = date.get("year"); mo = (date.get("month") or "1").zfill(2)
            d = (date.get("day") or "1").zfill(2)
            releasedate = f"{y}-{mo}-{d}"
        inp = game.find("input")
        players = inp.get("players") if inp is not None and inp.get("players") not in (None, "0") else None
        db[gid] = {
            "name": (title or "").strip() or None,
            "description": (synopsis or "").strip() or None,
            "developer": (game.findtext("developer") or "").strip() or None,
            "publisher": (game.findtext("publisher") or "").strip() or None,
            "genre": (game.findtext("genre") or "").strip() or None,
            "releasedate": releasedate,
            "players": players,
        }
    _db_cache[system_id] = db
    return db


_TEXTUAL_TAGS = (("name", "name"), ("desc", "description"), ("releasedate", "releasedate"),
                 ("developer", "developer"), ("publisher", "publisher"),
                 ("genre", "genre"), ("players", "players"))


def _write_textual(path: Path, meta: dict) -> None:
    root = ET.Element("game")
    for tag, key in _TEXTUAL_TAGS:
        val = meta.get(key)
        if val:
            ET.SubElement(root, tag).text = str(val)
    tree = ET.ElementTree(root)
    if hasattr(ET, "indent"):
        ET.indent(tree)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def scrape(system_id: str, folder: str, rows, on_progress: Optional[Callable] = None,
           control=None) -> dict:
    """Write GameTDB cover + metadata into the library for each game that has a
    resolvable GameID. `rows` are game rows with a `stem` (folder name). Honors
    control.cancelled / pause between games. Mirrors scraper.scrape's contract."""
    from .scraper import ScrapeCancelled   # shared cancellation signal

    console = _CONSOLE[system_id]
    db = _load_db(system_id)
    media = config.ROMS_ROOT / folder / "media"
    covers = media / "covers"
    textual = media / "textual"
    screenshots = media / "screenshots"
    for d in (covers, textual, screenshots):
        d.mkdir(parents=True, exist_ok=True)

    total = len(rows)
    matched = 0
    for i, r in enumerate(rows):
        if control:
            if hasattr(control, "wait_if_paused"):
                control.wait_if_paused()
            if control.cancelled:
                raise ScrapeCancelled()
        stem = r["stem"]
        gid = game_id(stem)
        hit = False
        if gid:
            data = fetch_cover(gid, console)
            if data:
                (covers / f"{stem}.png").write_bytes(data); hit = True
            meta = db.get(gid)
            if meta:
                _write_textual(textual / f"{stem}.xml", meta); hit = True
        # GameTDB has no screenshots — fill from libretro-thumbnails by title.
        snap = libretro.snap_for(system_id, stem)
        if snap:
            (screenshots / f"{stem}.png").write_bytes(snap); hit = True
        if hit:
            matched += 1
        if on_progress:
            on_progress(i + 1, total, "gametdb")
    return {"backend": "gametdb", "matched": matched, "total": total}
