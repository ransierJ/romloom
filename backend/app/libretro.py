"""libretro-thumbnails scraper — keyless box art + screenshots by title.

The libretro-thumbnails project hosts per-system GitHub repos of Named_Boxarts /
Named_Snaps / Named_Titles PNGs, named by the No-Intro / Redump game name. We
list a repo's tree once (cached), index it by normalized title, and match the
library's games against it — no API key, no login. It's the best keyless source
for the HD disc systems ScreenScraper struggles to hash-match (Xbox, PS3), and it
supplies the screenshots GameTDB lacks for Wii / Wii U.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Optional

from . import config
from .catalog import normkey

# Canonical system id -> libretro-thumbnails repo name. Systems not listed simply
# have no libretro source (graceful: no media fetched).
LIBRETRO_REPO = {
    "nes": "Nintendo_-_Nintendo_Entertainment_System",
    "snes": "Nintendo_-_Super_Nintendo_Entertainment_System",
    "n64": "Nintendo_-_Nintendo_64",
    "gb": "Nintendo_-_Game_Boy", "gbc": "Nintendo_-_Game_Boy_Color",
    "gba": "Nintendo_-_Game_Boy_Advance", "nds": "Nintendo_-_Nintendo_DS",
    "3ds": "Nintendo_-_Nintendo_3DS", "gc": "Nintendo_-_GameCube",
    "wii": "Nintendo_-_Wii", "wiiu": "Nintendo_-_Wii_U",
    "virtualboy": "Nintendo_-_Virtual_Boy", "fds": "Nintendo_-_Family_Computer_Disk_System",
    "pokemini": "Nintendo_-_Pokemon_Mini",
    "genesis": "Sega_-_Mega_Drive_-_Genesis", "mastersystem": "Sega_-_Master_System_-_Mark_III",
    "gamegear": "Sega_-_Game_Gear", "sega32x": "Sega_-_32X",
    "segacd": "Sega_-_Mega-CD_-_Sega_CD", "saturn": "Sega_-_Saturn",
    "dreamcast": "Sega_-_Dreamcast", "sg1000": "Sega_-_SG-1000",
    "psx": "Sony_-_PlayStation", "ps2": "Sony_-_PlayStation_2",
    "ps3": "Sony_-_PlayStation_3", "psp": "Sony_-_PlayStation_Portable",
    "psvita": "Sony_-_PlayStation_Vita",
    "xbox": "Microsoft_-_Xbox", "xbox360": "Microsoft_-_Xbox_360",
    "tg16": "NEC_-_PC_Engine_-_TurboGrafx_16",
    "tg16cd": "NEC_-_PC_Engine_CD_-_TurboGrafx-CD",
    "neogeo": "SNK_-_Neo_Geo", "ngp": "SNK_-_Neo_Geo_Pocket",
    "ngpc": "SNK_-_Neo_Geo_Pocket_Color",
    "wonderswan": "Bandai_-_WonderSwan", "wonderswancolor": "Bandai_-_WonderSwan_Color",
    "atari2600": "Atari_-_2600", "atarilynx": "Atari_-_Lynx", "atarijaguar": "Atari_-_Jaguar",
    "colecovision": "Coleco_-_ColecoVision", "intellivision": "Mattel_-_Intellivision",
    "msx": "Microsoft_-_MSX", "3do": "The_3DO_Company_-_3DO", "pcfx": "NEC_-_PC-FX",
}

# Systems for which libretro is the PRIMARY scraper (ScreenScraper/Skyscraper
# hash-match poorly on these HD disc dumps; title-matched libretro does better).
LIBRETRO_PRIMARY = {"xbox", "xbox360", "ps3"}

_RAW = "https://raw.githubusercontent.com/libretro-thumbnails/{repo}/master/{sub}/{fn}"
_TREE = "https://api.github.com/repos/libretro-thumbnails/{repo}/git/trees/master?recursive=1"
_CACHE_DIR = config.CONFIG_DIR / "libretro"
_TREE_TTL = 7 * 86400
_SUBS = ("Named_Boxarts", "Named_Snaps", "Named_Titles")

_index_mem: dict = {}   # repo -> {sub: {normkey: filename}}


def handles(system_id: str) -> bool:
    return system_id in LIBRETRO_PRIMARY


def has_repo(system_id: str) -> bool:
    return system_id in LIBRETRO_REPO


def _http(url: str, timeout: int = 30) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ROMForge"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 200:
                return r.read()
    except Exception:
        return None
    return None


def _tree_paths(repo: str) -> list:
    cache = _CACHE_DIR / f"{repo}.txt"
    if cache.is_file() and (time.time() - cache.stat().st_mtime) < _TREE_TTL:
        return cache.read_text(encoding="utf-8").splitlines()
    blob = _http(_TREE.format(repo=repo), timeout=45)
    if not blob:
        return cache.read_text(encoding="utf-8").splitlines() if cache.is_file() else []
    try:
        paths = [t["path"] for t in json.loads(blob).get("tree", []) if t.get("type") == "blob"]
    except Exception:
        return []
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text("\n".join(paths), encoding="utf-8")
    except OSError:
        pass
    return paths


def _index(repo: str) -> dict:
    if repo in _index_mem:
        return _index_mem[repo]
    idx = {sub: {} for sub in _SUBS}
    for p in _tree_paths(repo):
        for sub in _SUBS:
            prefix = sub + "/"
            if p.startswith(prefix) and p.lower().endswith(".png"):
                fn = p[len(prefix):]
                key = normkey(fn[:-4])          # filename minus .png
                if key:
                    idx[sub].setdefault(key, fn)
                break
    _index_mem[repo] = idx
    return idx


def _fetch(repo: str, sub: str, fn: str) -> Optional[bytes]:
    return _http(_RAW.format(repo=repo, sub=sub, fn=urllib.parse.quote(fn)))


def snap_for(system_id: str, stem: str) -> Optional[bytes]:
    """A screenshot (Named_Snaps) for one game, matched by title. Used to fill in
    the screenshots GameTDB doesn't provide for Wii / Wii U."""
    repo = LIBRETRO_REPO.get(system_id)
    if not repo:
        return None
    fn = _index(repo)["Named_Snaps"].get(normkey(stem))
    return _fetch(repo, "Named_Snaps", fn) if fn else None


def scrape(system_id: str, folder: str, rows, on_progress=None, control=None) -> dict:
    """Primary libretro scrape: box art (Named_Boxarts) + screenshot (Named_Snaps)
    for each game, matched by normalized title. No metadata (images only)."""
    from .scraper import ScrapeCancelled

    repo = LIBRETRO_REPO.get(system_id)
    if not repo:
        return {"backend": "libretro", "matched": 0, "total": len(rows)}
    idx = _index(repo)
    media = config.ROMS_ROOT / folder / "media"
    covers = media / "covers"
    shots = media / "screenshots"
    covers.mkdir(parents=True, exist_ok=True)
    shots.mkdir(parents=True, exist_ok=True)

    matched = 0
    for i, r in enumerate(rows):
        if control:
            if hasattr(control, "wait_if_paused"):
                control.wait_if_paused()
            if control.cancelled:
                raise ScrapeCancelled()
        stem = r["stem"]
        key = normkey(stem)
        hit = False
        bfn = idx["Named_Boxarts"].get(key)
        if bfn:
            d = _fetch(repo, "Named_Boxarts", bfn)
            if d:
                (covers / f"{stem}.png").write_bytes(d); hit = True
        sfn = idx["Named_Snaps"].get(key)
        if sfn:
            d = _fetch(repo, "Named_Snaps", sfn)
            if d:
                (shots / f"{stem}.png").write_bytes(d); hit = True
        if hit:
            matched += 1
        if on_progress:
            on_progress(i + 1, len(rows), "libretro")
    return {"backend": "libretro", "matched": matched, "total": len(rows)}
