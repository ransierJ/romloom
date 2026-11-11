"""Reference catalogs: what a *complete* set looks like, per system.

The authoritative "every known game" lists are the No-Intro (cartridge) and
Redump (disc) DAT files. DAT-o-MATIC is click-to-download and bot-hostile, so we
pull them from the libretro-database GitHub mirror, which republishes both as
plain ClrMamePro .dat files, one per system.

A catalog is fetched on demand, cached under CONFIG_DIR/catalogs, parsed into the
catalog_titles table, and diffed against on-disk games to report what's missing.
Matching is by a normalized base title (region/variant tags stripped), so a
scraper-renamed ROM still lines up with its catalog entry.
"""
from __future__ import annotations

import re
import urllib.request
from urllib.parse import quote

from . import config

_RAW = "https://raw.githubusercontent.com/libretro/libretro-database/master/metadat/{group}/{file}"

# Canonical system id -> (libretro group, DAT filename stem). Arcade (neogeo,
# cps*, fbneo, mame), Switch, Wii U, and the home-computer formats have no clean
# No-Intro/Redump equivalent and are intentionally absent — the UI reports those
# as "no catalog available" rather than guessing.
DAT_SOURCES = {
    # --- No-Intro (cartridge / handheld) ---
    "atari2600": ("no-intro", "Atari - 2600"),
    "atari5200": ("no-intro", "Atari - 5200"),
    "atari7800": ("no-intro", "Atari - 7800"),
    "atarijaguar": ("no-intro", "Atari - Jaguar"),
    "atarilynx": ("no-intro", "Atari - Lynx"),
    "wonderswan": ("no-intro", "Bandai - WonderSwan"),
    "wonderswancolor": ("no-intro", "Bandai - WonderSwan Color"),
    "colecovision": ("no-intro", "Coleco - ColecoVision"),
    "c64": ("no-intro", "Commodore - 64"),
    "vic20": ("no-intro", "Commodore - VIC-20"),
    "vectrex": ("no-intro", "GCE - Vectrex"),
    "odyssey2": ("no-intro", "Magnavox - Odyssey2"),
    "intellivision": ("no-intro", "Mattel - Intellivision"),
    "channelf": ("no-intro", "Fairchild - Channel F"),
    "msx": ("no-intro", "Microsoft - MSX"),
    "msx2": ("no-intro", "Microsoft - MSX2"),
    "tg16": ("no-intro", "NEC - PC Engine - TurboGrafx 16"),
    "supergrafx": ("no-intro", "NEC - PC Engine SuperGrafx"),
    "fds": ("no-intro", "Nintendo - Family Computer Disk System"),
    "gba": ("no-intro", "Nintendo - Game Boy Advance"),
    "gbc": ("no-intro", "Nintendo - Game Boy Color"),
    "gb": ("no-intro", "Nintendo - Game Boy"),
    "3ds": ("no-intro", "Nintendo - Nintendo 3DS"),
    "n64": ("no-intro", "Nintendo - Nintendo 64"),
    "nds": ("no-intro", "Nintendo - Nintendo DS"),
    "nes": ("no-intro", "Nintendo - Nintendo Entertainment System"),
    "pokemini": ("no-intro", "Nintendo - Pokemon Mini"),
    "snes": ("no-intro", "Nintendo - Super Nintendo Entertainment System"),
    "virtualboy": ("no-intro", "Nintendo - Virtual Boy"),
    "ngp": ("no-intro", "SNK - Neo Geo Pocket"),
    "ngpc": ("no-intro", "SNK - Neo Geo Pocket Color"),
    "sega32x": ("no-intro", "Sega - 32X"),
    "gamegear": ("no-intro", "Sega - Game Gear"),
    "mastersystem": ("no-intro", "Sega - Master System - Mark III"),
    "genesis": ("no-intro", "Sega - Mega Drive - Genesis"),
    "sg1000": ("no-intro", "Sega - SG-1000"),
    "x68000": ("no-intro", "Sharp - X68000"),
    "psvita": ("no-intro", "Sony - PlayStation Vita"),
    # --- Redump (disc) ---
    "3do": ("redump", "The 3DO Company - 3DO"),
    "amigacd32": ("redump", "Commodore - CD32"),
    "atarijaguarcd": ("redump", "Atari - Jaguar CD"),
    "cdi": ("redump", "Philips - CD-i"),
    "dreamcast": ("redump", "Sega - Dreamcast"),
    "fmtowns": ("redump", "Fujitsu - FM Towns"),
    "gc": ("redump", "Nintendo - GameCube"),
    "neogeocd": ("redump", "SNK - Neo Geo CD"),
    "pcfx": ("redump", "NEC - PC-FX"),
    "pc98": ("redump", "NEC - PC-98"),
    "ps2": ("redump", "Sony - PlayStation 2"),
    "psx": ("redump", "Sony - PlayStation"),
    "psp": ("redump", "Sony - PlayStation Portable"),
    "saturn": ("redump", "Sega - Saturn"),
    "segacd": ("redump", "Sega - Mega-CD - Sega CD"),
    "tg16cd": ("redump", "NEC - PC Engine CD - TurboGrafx-CD"),
    "wii": ("redump", "Nintendo - Wii"),
    "xbox": ("redump", "Microsoft - Xbox"),
}

# No-Intro parenthetical region tokens -> canonical region bucket.
_REGION_MAP = {
    "usa": "USA", "us": "USA", "world": "World",
    "europe": "Europe", "eur": "Europe", "uk": "Europe",
    "japan": "Japan", "jpn": "Japan", "asia": "Asia",
    "korea": "Korea", "china": "China", "taiwan": "Asia", "hong kong": "Asia",
    "brazil": "Brazil", "australia": "Australia",
    "spain": "Europe", "france": "Europe", "germany": "Europe",
    "italy": "Europe", "netherlands": "Europe", "sweden": "Europe",
    "canada": "USA", "russia": "Europe", "scandinavia": "Europe",
    "latin america": "Brazil",
}

# Tags that mark a non-retail entry, excluded from the (default) retail view.
# Revisions — (Rev 1), (v1.1) — are NOT here: they collapse into the base title
# so that owning any version counts as owning the game.
_EXCLUDE_TAGS = (
    "beta", "proto", "prototype", "demo", "sample", "unl", "aftermarket",
    "pirate", "program", "test", "debug", "kiosk", "promo", "hack",
    "homebrew", "competition cart", "competition",
)

_ARTICLES = ("the", "a", "an", "le", "la", "les", "los", "el", "der", "die", "das")

_GAME_RE = re.compile(r'game\s*\(\s*name\s+"([^"]+)"', re.IGNORECASE)
_PAREN_RE = re.compile(r'[\(\[]([^)\]]*)[)\]]')
# No-Intro folds the leading article to the end of the main title, either at the
# very end ("Legend of Zelda, The") or right before a " - " subtitle
# ("Legend of Zelda, The - A Link to the Past"). Undo both.
_ARTICLE_RE = re.compile(
    r'^(.*?),\s*(' + "|".join(_ARTICLES) + r')(\s+-\s+.*)?$', re.IGNORECASE
)


def _fold_article(base: str) -> str:
    m = _ARTICLE_RE.match(base)
    if m:
        return f"{m.group(2)} {m.group(1)}{m.group(3) or ''}".strip()
    return base


def available(system_id: str) -> bool:
    return system_id in DAT_SOURCES


def source_label(system_id: str) -> str | None:
    src = DAT_SOURCES.get(system_id)
    return src[0] if src else None


def _url(system_id: str) -> str:
    group, fname = DAT_SOURCES[system_id]
    return _RAW.format(group=group, file=quote(fname + ".dat"))


def cache_path(system_id: str):
    return config.CATALOG_DIR / f"{system_id}.dat"


def fetch(system_id: str) -> bytes:
    """Download the DAT and write it to the cache. Returns the raw bytes.
    Raises on network/HTTP error (caller surfaces it)."""
    if system_id not in DAT_SOURCES:
        raise ValueError(f"no catalog source for {system_id!r}")
    req = urllib.request.Request(_url(system_id), headers={"User-Agent": "ROMForge"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = resp.read()
    config.CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    cache_path(system_id).write_bytes(data)
    return data


def _pretty(base: str) -> str:
    """'Legend of Zelda, The - ...' -> 'The Legend of Zelda - ...' for display."""
    return _fold_article(base)


def normkey(title: str) -> str:
    """Match key: drop every ()/[] group, fold the article back to the front,
    then keep lowercase alphanumerics only. 'The Legend of Zelda (USA)' and
    'Legend of Zelda, The (USA) (Rev 1)' both collapse to 'thelegendofzelda'."""
    base = _fold_article(_PAREN_RE.sub("", title).strip().lower())
    return re.sub(r"[^a-z0-9]", "", base)


def _tags(title: str) -> list[str]:
    out = []
    for grp in _PAREN_RE.findall(title):
        out.extend(t.strip().lower() for t in grp.split(","))
    return out


def dat_version(text: str) -> str:
    m = re.search(r'version\s+"([^"]+)"', text)
    return m.group(1) if m else ""


def parse_titles(text: str, retail_only: bool = True):
    """Yield (display_title, normkey, regions_csv) per catalog game, deduped by
    normkey with revisions/variants collapsed and regions unioned."""
    seen: dict[str, list] = {}
    for m in _GAME_RE.finditer(text):
        name = m.group(1)
        tags = _tags(name)
        if retail_only and any(t in _EXCLUDE_TAGS for t in tags):
            continue
        regions = {_REGION_MAP[t] for t in tags if t in _REGION_MAP}
        key = normkey(name)
        if not key:
            continue
        base = _pretty(_PAREN_RE.sub("", name).strip())
        if key in seen:
            seen[key][1] |= regions
        else:
            seen[key] = [base, set(regions)]
    for key, (base, regions) in seen.items():
        yield base, key, ",".join(sorted(regions))
