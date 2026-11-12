"""IGDB metadata source (Twitch-authenticated).

Fills the text metadata — and backfills cover / screenshots where missing — that
libretro-thumbnails (images only) can't provide for Xbox / Xbox 360 / PS3.
Matched by title + platform filter. Needs a free Twitch developer Client ID +
Secret (entered in Settings); no ROM hashing involved.
"""
from __future__ import annotations

import datetime
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from . import config, settings
from .catalog import normkey

_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_GAMES_URL = "https://api.igdb.com/v4/games"
_IMG = "https://images.igdb.com/igdb/image/upload/t_{size}/{image_id}.jpg"

# Canonical system id -> IGDB platform id (filters the title search).
IGDB_PLATFORM = {
    "xbox": 11, "xbox360": 12, "ps3": 9, "wii": 5, "wiiu": 41, "gc": 21,
    "ps2": 8, "psx": 7, "psp": 38, "psvita": 46,
    "nes": 18, "snes": 19, "n64": 4, "gb": 33, "gbc": 22, "gba": 24,
    "nds": 20, "3ds": 37, "genesis": 29, "mastersystem": 64, "gamegear": 35,
    "saturn": 32, "dreamcast": 23, "segacd": 78, "sega32x": 30, "sg1000": 84,
    "neogeo": 80, "ngp": 119, "ngpc": 120, "wonderswan": 57, "wonderswancolor": 123,
    "atari2600": 59, "atarilynx": 61, "atarijaguar": 62, "colecovision": 68,
    "intellivision": 67, "msx": 27, "3do": 50, "pcfx": 274, "tg16": 86,
    "tg16cd": 150, "dos": 13, "arcade": 52,
}

_FIELDS = ("name,summary,genres.name,involved_companies.company.name,"
           "involved_companies.developer,involved_companies.publisher,"
           "first_release_date,rating,cover.image_id,screenshots.image_id")
_PAREN = re.compile(r"[\(\[][^)\]]*[)\]]")
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")

_token = {"val": None, "exp": 0.0}


def _creds():
    ig = settings.load().get("igdb", {})
    return ig.get("client_id") or "", ig.get("client_secret") or ""


def configured() -> bool:
    cid, sec = _creds()
    return bool(cid and sec)


def _clean_title(stem: str) -> str:
    return _PAREN.sub("", stem).strip()


def _get_token() -> Optional[str]:
    cid, sec = _creds()
    if not (cid and sec):
        return None
    if _token["val"] and time.time() < _token["exp"] - 60:
        return _token["val"]
    params = urllib.parse.urlencode({"client_id": cid, "client_secret": sec,
                                     "grant_type": "client_credentials"})
    try:
        req = urllib.request.Request(f"{_TOKEN_URL}?{params}", method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
        _token["val"] = d["access_token"]
        _token["exp"] = time.time() + d.get("expires_in", 3600)
        return _token["val"]
    except Exception:
        return None


def _query(token: str, cid: str, body: str) -> list:
    try:
        req = urllib.request.Request(
            _GAMES_URL, data=body.encode("utf-8"), method="POST",
            headers={"Client-ID": cid, "Authorization": f"Bearer {token}",
                     "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except Exception:
        return []


def _search(token: str, cid: str, title: str, platform_id: Optional[int]) -> Optional[dict]:
    esc = title.replace('"', "").strip()
    if not esc:
        return None
    where = f" where platforms = ({platform_id});" if platform_id else ""
    body = f'search "{esc}"; fields {_FIELDS};{where} limit 6;'
    res = _query(token, cid, body)
    if not res:
        return None
    nk = normkey(title)
    for g in res:                      # prefer an exact normalized-title match
        if normkey(g.get("name", "")) == nk:
            return g
    return res[0]                      # else the top-ranked search result


def _fetch_img(image_id: str, size: str) -> Optional[bytes]:
    try:
        req = urllib.request.Request(_IMG.format(size=size, image_id=image_id),
                                     headers={"User-Agent": "ROMLoom"})
        with urllib.request.urlopen(req, timeout=20) as r:
            if r.status == 200:
                return r.read()
    except Exception:
        return None
    return None


def _to_meta(g: dict) -> dict:
    dev = pub = None
    for ic in g.get("involved_companies", []) or []:
        name = (ic.get("company") or {}).get("name")
        if ic.get("developer") and not dev:
            dev = name
        if ic.get("publisher") and not pub:
            pub = name
    rd = None
    if g.get("first_release_date"):
        rd = datetime.datetime.utcfromtimestamp(g["first_release_date"]).strftime("%Y-%m-%d")
    genres = ",".join(x.get("name", "") for x in (g.get("genres") or []) if x.get("name"))
    return {
        "name": g.get("name"),
        "description": g.get("summary"),
        "developer": dev, "publisher": pub,
        "genre": genres or None,
        "releasedate": rd,
        "rating": round(g["rating"] / 100, 2) if g.get("rating") else None,
    }


_TEXTUAL = (("name", "name"), ("desc", "description"), ("rating", "rating"),
            ("releasedate", "releasedate"), ("developer", "developer"),
            ("publisher", "publisher"), ("genre", "genre"))


def _write_textual(path: Path, meta: dict) -> None:
    root = ET.Element("game")
    for tag, key in _TEXTUAL:
        v = meta.get(key)
        if v not in (None, ""):
            ET.SubElement(root, tag).text = str(v)
    tree = ET.ElementTree(root)
    if hasattr(ET, "indent"):
        ET.indent(tree)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _has_image(folder: Path, stem: str) -> bool:
    return any((folder / f"{stem}{e}").exists() for e in _IMG_EXTS)


def scrape(system_id: str, folder: str, rows, on_progress=None, control=None) -> dict:
    """Write IGDB metadata (+ cover/screenshot where none exists yet) per game,
    matched by title. A no-op if credentials aren't set."""
    from .scraper import ScrapeCancelled

    token = _get_token()
    cid, _ = _creds()
    if not token:
        return {"backend": "igdb", "matched": 0, "total": len(rows), "note": "no credentials"}
    pid = IGDB_PLATFORM.get(system_id)
    media = config.ROMS_ROOT / folder / "media"
    covers = media / "covers"
    shots = media / "screenshots"
    textual = media / "textual"
    for d in (covers, shots, textual):
        d.mkdir(parents=True, exist_ok=True)

    matched = 0
    for i, r in enumerate(rows):
        if control:
            if hasattr(control, "wait_if_paused"):
                control.wait_if_paused()
            if control.cancelled:
                raise ScrapeCancelled()
        stem = r["stem"]
        g = _search(token, cid, _clean_title(stem), pid)
        if g:
            _write_textual(textual / f"{stem}.xml", _to_meta(g))
            cov = g.get("cover") or {}
            if cov.get("image_id") and not _has_image(covers, stem):
                img = _fetch_img(cov["image_id"], "cover_big")
                if img:
                    (covers / f"{stem}.jpg").write_bytes(img)
            sc = g.get("screenshots") or []
            if sc and sc[0].get("image_id") and not _has_image(shots, stem):
                img = _fetch_img(sc[0]["image_id"], "screenshot_big")
                if img:
                    (shots / f"{stem}.jpg").write_bytes(img)
            matched += 1
        if on_progress:
            on_progress(i + 1, len(rows), "igdb")
    return {"backend": "igdb", "matched": matched, "total": len(rows)}
