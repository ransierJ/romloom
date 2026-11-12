"""Runtime configuration.

Settings come, in priority order, from: environment variables (ROMLOOM_* then
legacy ROMFORGE_*), then a romloom.toml next to the executable / working dir,
then the built-in defaults. The toml layer exists so the packaged Windows build
can be configured by editing a file beside the .exe — no terminal needed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _app_base() -> Path:
    """Root for the config file and the default data dirs: next to the frozen
    executable when packaged (PyInstaller), otherwise the working directory."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _load_file_conf() -> dict:
    """Read romloom.toml beside the app, if present. Accepts either flat keys or
    a [romloom] table. Never raises — a bad/missing file just yields no config."""
    try:
        import tomllib  # stdlib on 3.11+
    except ModuleNotFoundError:
        return {}
    p = _app_base() / "romloom.toml"
    if not p.is_file():
        return {}
    try:
        with p.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception:
        return {}
    if isinstance(data.get("romloom"), dict):
        data = data["romloom"]
    return {str(k).lower(): v for k, v in data.items()}


_APP_BASE = _app_base()
_FILE_CONF = _load_file_conf()


def _env(name: str, default: str) -> str:
    # Env wins (ROMLOOM_* then legacy ROMFORGE_*), then romloom.toml, then default.
    for key in (f"ROMLOOM_{name}", f"ROMFORGE_{name}"):
        if key in os.environ:
            return os.environ[key]
    if name.lower() in _FILE_CONF:
        return str(_FILE_CONF[name.lower()])
    return default


def _path(name: str, default: str) -> Path:
    return Path(_env(name, default)).expanduser()


# Source library — mounted read-only in Docker. Never written to.
ROMS_ROOT: Path = _path("ROMS_ROOT", "/media/jean/Plex3/ROMs")

# Where forged collections are written (Phase 3). Created on demand.
OUTPUT_ROOT: Path = _path("OUTPUT_ROOT", str(_APP_BASE / "output"))

# Persistent app state: the SQLite index, the thumbnail cache, settings.
CONFIG_DIR: Path = _path("CONFIG_DIR", str(_APP_BASE / "config"))

DB_PATH: Path = CONFIG_DIR / "romloom.db"
THUMB_DIR: Path = CONFIG_DIR / "thumbs"
SETTINGS_PATH: Path = CONFIG_DIR / "settings.json"
# Cached reference catalogs (No-Intro / Redump .dat files) for the Missing view.
CATALOG_DIR: Path = CONFIG_DIR / "catalogs"

# Folders directly under ROMS_ROOT that are never treated as a system.
IGNORED_SYSTEM_DIRS = {"Bios", "BIOS", "bios", "#recycle", "@eaDir"}

# File extensions under a system folder that are never treated as a ROM.
# ".part" is an incomplete download, not a game.
NON_ROM_EXTENSIONS = {
    ".txt", ".md", ".dat", ".xml", ".nfo", ".db", ".ini", ".json",
    ".log", ".bak", ".sav", ".srm", ".state", ".cfg", ".part", ".crdownload",
    ".opt", ".dsv", ".mcr", ".ldci",   # emulator saves/config (NDS .opt, etc.)
}

# Cue-sheet / playlist extensions whose referenced track files (e.g. the .bin of
# a "Game.cue + Game.bin" Sega CD / PSX set) should NOT also count as games.
# A standalone .bin with no cue (e.g. an Atari 2600 ROM) is unaffected.
CUESHEET_EXTENSIONS = (".cue", ".gdi", ".ccd", ".mds")

# Per-system ROM extension allowlist (keyed by canonical platform id). When a
# system is listed here, ONLY these extensions count as ROMs; everything else
# (stray dumps with odd extensions, saves, archives) is ignored. Systems not
# listed accept any extension not in NON_ROM_EXTENSIONS. Nintendo DS carts are
# single-file .nds, so drop the loose .is0/.gmc/etc. debris in those folders.
SYSTEM_ROM_EXTENSIONS = {
    "nds": {".nds"},
}

# Region tags and the (lowercased) filename tokens that imply them, matched
# against comma-split parenthetical groups in No-Intro / GoodTools names.
# Order matters — first match wins for the display region.
REGION_TOKENS = {
    "USA": ("usa", "us", "u", "america", "ntsc-u"),
    "Europe": ("europe", "eur", "e", "eu", "pal"),
    "Japan": ("japan", "jpn", "jp", "j", "ntsc-j"),
    "World": ("world", "w"),
    "Asia": ("asia",),
    "Korea": ("korea", "kor", "k"),
    "Brazil": ("brazil", "bra"),
    "Australia": ("australia", "aus"),
    "China": ("china", "chn"),
}

# Sub-folders of a system that hold media/metadata, never games. A disc game
# stored as its own subfolder is still enumerated; these scraper output dirs
# (Skyscraper's "media", Skraper's "images/manuals/videos", and the individual
# art folders) are not. Matched case-insensitively.
NON_GAME_DIRS = {
    "media", "images", "manuals", "videos", "video", "textual",
    "covers", "cover", "box2dfront", "box3d",
    "screenshots", "screenshot", "wheels", "wheel",
    "marquees", "marquee", "fanart", "thumbs", "thumb",
    "bezels", "bezel", "mix", "mixv1", "mixv2", "support", "manual",
}

# Image extensions Skyscraper may emit for media, in preference order.
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")

# Internal asset key -> candidate media sub-folder names, in preference order.
# Skyscraper's folder naming varies across systems (screenshot vs screenshots,
# wheel vs wheels), so each asset resolves against a list, plural first.
# textual/ holds per-game metadata XML and is handled separately.
# Plural first (Skyscraper), then singular + Skraper names (box2dfront is
# Skraper's box-art front; box3d its 3D box render as a last-resort cover).
MEDIA_CANDIDATES = {
    "cover": ("covers", "cover", "box2dfront", "box3d"),
    "screenshot": ("screenshots", "screenshot"),
    "wheel": ("wheels", "wheel"),
    "marquee": ("marquees", "marquee"),
}

# Gameplay videos: folder candidates + recognized extensions. Resolved on
# demand (not part of the coverage index) and streamed to the detail view.
VIDEO_CANDIDATES = ("videos", "video")
VIDEO_EXTENSIONS = (".mp4", ".webm", ".m4v", ".mkv", ".avi", ".mov")

# Thumbnail widths pre-generated during a scan, per asset, so the first view of
# a boxart wall serves from the local cache instead of resizing off the NAS on
# demand. Cover feeds Boxart (320) + List/Gaps (120); screenshot feeds Gaps
# (200). Set to {} to disable warming.
THUMB_WARM_WIDTHS = {"cover": (320, 120), "screenshot": (200,)}

# Which assets must be present for a game to count as "complete". Wheels and
# marquees are tracked and shown as gap flags, but aren't required here —
# requiring all four misrepresents non-arcade systems. Tunable later.
COMPLETE_REQUIRES = ("cover", "screenshot", "textual")

HTTP_PORT: int = int(_env("PORT", "8096"))
# Interface to bind. Docker's CMD passes --host 0.0.0.0 explicitly; a bare
# (non-Docker) run defaults to loopback so the app isn't exposed by accident.
HTTP_HOST: str = _env("HOST", "127.0.0.1")

# Path to a native Skyscraper binary. Empty => auto-detect: the bundled Docker
# path, then whatever is on PATH (Skyscraper / Skyscraper.exe). Set this when it
# lives somewhere non-standard (e.g. a Windows install dir).
SKYSCRAPER_BIN: str = _env("SKYSCRAPER_BIN", "")


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    # Rebrand migration: adopt a pre-rename romforge.db (+ its WAL/SHM) as the
    # romloom index so the existing library doesn't need a full rescan.
    legacy = CONFIG_DIR / "romforge.db"
    if legacy.is_file() and not DB_PATH.is_file():
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(legacy) + suffix)
            if src.is_file():
                try:
                    src.rename(Path(str(DB_PATH) + suffix))
                except OSError:
                    pass
