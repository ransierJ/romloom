"""Export engine: a collection -> a physical, frontend-specific folder.

Given a collection and a target frontend, build a Plan (every file copy + every
gamelist to write) and then execute it. The Plan is the single source of truth:
dry-run returns its summary without touching disk; a real run executes it with
progress and a resume-friendly skip-existing check.

Targets differ only in three things — where ROMs go, how media is renamed/placed,
and the gamelist flavor — so each is a small builder over shared helpers.
"""
from __future__ import annotations

import os
import re
import shutil
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import config
from .scanner import _index_media  # reuse tolerant media-folder resolution

TARGETS = ("raw", "es-de", "batocera", "retrobat")

# Skyscraper asset key -> its source media sub-folder (canonical name).
_SKY_DIR = {"cover": "covers", "screenshot": "screenshots",
            "wheel": "wheels", "marquee": "marquees"}

# Image assets resolved from the index (<asset>_file columns); "video" is
# resolved on demand from the videos/ folder, like textual.
IMAGE_ASSETS = ("cover", "screenshot", "wheel", "marquee")
ALL_ASSETS = IMAGE_ASSETS + ("video",)


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #
@dataclass
class FileOp:
    src: str
    dst: str          # relative to OUTPUT_ROOT
    size: int
    is_dir: bool = False


@dataclass
class WriteOp:
    dst: str          # relative to OUTPUT_ROOT
    content: str


@dataclass
class Plan:
    target: str
    collection_name: str
    root_rel: str                       # output subfolder, relative to OUTPUT_ROOT
    copies: List[FileOp] = field(default_factory=list)
    writes: List[WriteOp] = field(default_factory=list)
    systems: Dict[str, int] = field(default_factory=dict)   # display_name -> rom count
    rom_count: int = 0
    media_count: int = 0
    missing_rom_sources: int = 0        # members whose ROM file wasn't found on disk

    @property
    def total_bytes(self) -> int:
        return sum(op.size for op in self.copies)

    def summary(self) -> dict:
        return {
            "target": self.target,
            "collection_name": self.collection_name,
            "output_path": str(config.OUTPUT_ROOT / self.root_rel),
            "root_rel": self.root_rel,
            "rom_count": self.rom_count,
            "media_count": self.media_count,
            "gamelists": len(self.writes),
            "total_bytes": self.total_bytes,
            "missing_rom_sources": self.missing_rom_sources,
            "systems": [{"display_name": k, "n": v} for k, v in sorted(self.systems.items())],
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def sanitize(name: str) -> str:
    name = re.sub(r"[^\w\s.\-]", "_", name).strip()
    return re.sub(r"\s+", " ", name) or "collection"


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _media_src(media_root: Path, asset: str, filename: str) -> Optional[Path]:
    for d in config.MEDIA_CANDIDATES[asset]:
        p = media_root / d / filename
        if p.is_file():
            return p
    return None


def _es_date(d: Optional[str]) -> Optional[str]:
    """'1991-11-02' -> '19911102T000000' (EmulationStation format)."""
    if not d:
        return None
    digits = re.sub(r"[^0-9]", "", d)[:8]
    return digits + "T000000" if len(digits) == 8 else None


def _indent(elem, level: int = 0) -> None:
    """Pretty-print indentation (ET.indent is 3.9+; keep 3.8 compatible)."""
    pad = "\n" + "  " * level
    if len(elem):
        if not (elem.text and elem.text.strip()):
            elem.text = pad + "  "
        for i, child in enumerate(elem):
            _indent(child, level + 1)
            tail = pad + ("  " if i < len(elem) - 1 else "")
            child.tail = tail
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = pad


def _gamelist_xml(entries: List[dict]) -> str:
    """Build a gameList XML document from a list of field dicts."""
    root = ET.Element("gameList")
    for e in entries:
        g = ET.SubElement(root, "game")
        for tag, val in e.items():
            if val is None or val == "":
                continue
            child = ET.SubElement(g, tag)
            child.text = str(val)
    _indent(root)
    return '<?xml version="1.0"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


# --------------------------------------------------------------------------- #
# Plan building
# --------------------------------------------------------------------------- #
def build_plan(conn, collection_id: int, target: str,
               assets: Optional[List[str]] = None) -> Plan:
    if target not in TARGETS:
        raise ValueError(f"Unknown target: {target}")
    assets = [a for a in (assets or ALL_ASSETS) if a in ALL_ASSETS] or list(ALL_ASSETS)

    coll = conn.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
    if not coll:
        raise ValueError("Unknown collection")

    rows = conn.execute(
        "SELECT g.*, s.folder, s.display_name, s.es_id, s.batocera_id, s.retrobat_id "
        "FROM collection_games cg "
        "JOIN games g ON g.system_id=cg.system_id AND g.rom_filename=cg.rom_filename "
        "JOIN systems s ON s.id = cg.system_id "
        "WHERE cg.collection_id = ? ORDER BY s.display_name, g.name COLLATE NOCASE",
        (collection_id,),
    ).fetchall()

    plan = Plan(target=target, collection_name=coll["name"],
                root_rel=f"{sanitize(coll['name'])}/{target}")

    # Group rows by system so gamelists and textual indexes are built once each.
    by_system: Dict[str, list] = {}
    for r in rows:
        by_system.setdefault(r["system_id"], []).append(r)

    for system_id, grows in by_system.items():
        _build_system(plan, system_id, grows, target, assets)

    return plan


def _target_paths(target: str, row) -> tuple:
    """Return (rom_dir_rel, media_dir_rel, gamelist_rel) for a system under root."""
    es = row["es_id"] or row["system_id"]
    bato = row["batocera_id"] or row["system_id"]
    retro = row["retrobat_id"] or row["system_id"]
    folder = row["folder"]
    if target == "raw":
        return folder, f"{folder}/media", None
    if target == "es-de":
        return f"ROMs/{es}", f"downloaded_media/{es}", f"ROMs/{es}/gamelist.xml"
    if target == "batocera":
        return bato, f"{bato}/images", f"{bato}/gamelist.xml"
    if target == "retrobat":
        return f"roms/{retro}", f"roms/{retro}/images", f"roms/{retro}/gamelist.xml"
    raise ValueError(target)


def _build_system(plan: Plan, system_id: str, grows: list, target: str, assets: List[str]):
    first = grows[0]
    folder = first["folder"]
    media_root = config.ROMS_ROOT / folder / "media"
    rom_dir_rel, media_dir_rel, gamelist_rel = _target_paths(target, first)
    root = plan.root_rel

    # Raw needs the textual xml; videos aren't indexed, so build tolerant
    # per-system indexes once for whatever this export needs.
    textual_idx = _index_media(media_root, ("textual",)) if target == "raw" else None
    video_idx = _index_media(media_root, config.VIDEO_CANDIDATES) if "video" in assets else None

    entries = []
    for r in grows:
        stem = r["stem"]
        rom_filename = r["rom_filename"]
        rom_src = config.ROMS_ROOT / folder / rom_filename
        is_dir = rom_src.is_dir()
        if not rom_src.exists():
            plan.missing_rom_sources += 1
            continue

        size = _dir_size(rom_src) if is_dir else _safe_size(rom_src)
        plan.copies.append(FileOp(str(rom_src), f"{root}/{rom_dir_rel}/{rom_filename}", size, is_dir))
        plan.rom_count += 1
        plan.systems[first["display_name"]] = plan.systems.get(first["display_name"], 0) + 1

        # MAME CHD companion: a sibling folder named like the romset carries the
        # game's disk image (calspeed.zip + calspeed/calspeed.chd). Ship it beside
        # the ROM so disk-based arcade games actually boot on the target.
        if not is_dir:
            companion = config.ROMS_ROOT / folder / stem
            if companion.is_dir():
                plan.copies.append(FileOp(str(companion),
                                          f"{root}/{rom_dir_rel}/{stem}",
                                          _dir_size(companion), True))

        # Disc tracks: a .cue/.gdi is just a text pointer to .bin/.img track
        # files that must ship with it, or the game won't mount. (They're hidden
        # from the library listing but still need exporting.)
        if not is_dir and os.path.splitext(rom_filename)[1].lower() in config.CUESHEET_EXTENSIONS:
            for track in _cue_tracks(rom_src, config.ROMS_ROOT / folder):
                tsrc = config.ROMS_ROOT / folder / track
                plan.copies.append(FileOp(str(tsrc), f"{root}/{rom_dir_rel}/{track}",
                                          _safe_size(tsrc)))

        # Image media per asset, placed/renamed per target.
        media_rel: Dict[str, str] = {}
        for asset in assets:
            if asset not in IMAGE_ASSETS:
                continue
            filename = r[f"{asset}_file"]
            if not filename:
                continue
            src = _media_src(media_root, asset, filename)
            if not src:
                continue
            ext = os.path.splitext(filename)[1] or ".png"
            dst_rel = _media_dst(target, media_dir_rel, asset, stem, filename, ext)
            if dst_rel is None:
                continue
            plan.copies.append(FileOp(str(src), f"{root}/{dst_rel}", _safe_size(src)))
            plan.media_count += 1
            media_rel[asset] = dst_rel

        # Gameplay video (resolved on demand; may be a large file).
        if video_idx is not None:
            vfilename = video_idx.get(stem)
            if vfilename:
                vsrc = media_root / ("videos" if (media_root / "videos" / vfilename).is_file() else "video") / vfilename
                if vsrc.is_file():
                    ext = os.path.splitext(vfilename)[1] or ".mp4"
                    vdst = _video_dst(target, rom_dir_rel, media_dir_rel, stem, vfilename, ext)
                    plan.copies.append(FileOp(str(vsrc), f"{root}/{vdst}", _safe_size(vsrc)))
                    plan.media_count += 1
                    media_rel["video"] = vdst

        # Raw: copy the textual metadata xml verbatim.
        if target == "raw" and textual_idx is not None:
            tname = textual_idx.get(stem)
            if tname:
                tsrc = media_root / "textual" / tname
                if tsrc.is_file():
                    plan.copies.append(FileOp(str(tsrc), f"{root}/{folder}/media/textual/{tname}",
                                              _safe_size(tsrc)))

        if gamelist_rel:
            entries.append(_gamelist_entry(target, r, rom_filename, media_rel, media_dir_rel))

    if gamelist_rel and entries:
        plan.writes.append(WriteOp(f"{root}/{gamelist_rel}", _gamelist_xml(entries)))


_CUE_FILE_RE = re.compile(r'FILE\s+"([^"]+)"', re.IGNORECASE)
_CUE_TRACK_RE = re.compile(r'(\S+\.(?:bin|img|raw|iso))', re.IGNORECASE)


def _cue_tracks(cue_path: Path, folder: Path) -> list:
    """Track files a .cue/.gdi references, resolved to their REAL on-disk names.
    Cue references can carry Windows paths or odd casing, so match the referenced
    basenames (normalized) against the actual files in the folder."""
    try:
        text = cue_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    refs = set()
    for m in _CUE_FILE_RE.finditer(text):
        refs.add(os.path.basename(m.group(1).replace("\\", "/")).strip().lower())
    for m in _CUE_TRACK_RE.finditer(text):
        refs.add(os.path.basename(m.group(1).strip('"').replace("\\", "/")).strip().lower())
    if not refs:
        return []
    out = []
    try:
        for e in os.scandir(folder):
            if e.is_file() and e.name.lower() in refs:
                out.append(e.name)
    except OSError:
        pass
    return out


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _media_dst(target: str, media_dir_rel: str, asset: str, stem: str,
               filename: str, ext: str) -> Optional[str]:
    """Where a given asset lands for a target (None = not used by this target)."""
    if target == "raw":
        # Passthrough: keep Skyscraper's own folder + original filename.
        return f"{media_dir_rel}/{_SKY_DIR[asset]}/{filename}"
    if target == "es-de":
        # ES-DE auto-links media by rom stem under downloaded_media/<system>/<type>/.
        es_type = {"cover": "covers", "screenshot": "screenshots", "wheel": "marquees"}.get(asset)
        if es_type is None:      # Skyscraper's arcade 'marquee' has no ES-DE home
            return None
        return f"{media_dir_rel}/{es_type}/{stem}{ext}"
    # batocera + retrobat: images/<stem>-<suffix>.<ext>, referenced from gamelist.
    suffix = {"screenshot": "image", "cover": "thumb", "wheel": "marquee"}.get(asset)
    if suffix is None:
        return None
    return f"{media_dir_rel}/{stem}-{suffix}{ext}"


def _video_dst(target: str, rom_dir_rel: str, media_dir_rel: str,
               stem: str, filename: str, ext: str) -> str:
    """Where a gameplay video lands per target."""
    if target == "raw":
        return f"{media_dir_rel}/videos/{filename}"           # <sys>/media/videos/
    if target == "es-de":
        return f"{media_dir_rel}/videos/{stem}{ext}"          # downloaded_media/<sys>/videos/
    # batocera + retrobat: <system>/videos/<stem>-video.<ext>, from gamelist.
    return f"{rom_dir_rel}/videos/{stem}-video{ext}"


def _gamelist_entry(target: str, r, rom_filename: str,
                    media_rel: Dict[str, str], media_dir_rel: str) -> dict:
    """One <game> field dict. ES-DE is metadata-only (media auto-links);
    Batocera/Retrobat reference media with paths relative to the system folder."""
    entry = {
        "path": f"./{rom_filename}",
        "name": r["name"] or r["stem"],
        "desc": r["description"],
        "rating": r["rating"],
        "releasedate": _es_date(r["releasedate"]),
        "developer": r["developer"],
        "publisher": r["publisher"],
        "genre": r["genre"],
        "players": r["players"],
    }
    if target in ("batocera", "retrobat"):
        # The gamelist lives in the system folder; reference media relative to it
        # as "./images/<file>" (matches where _media_dst places them).
        if media_rel.get("screenshot"):
            entry["image"] = "./images/" + os.path.basename(media_rel["screenshot"])
        if media_rel.get("cover"):
            entry["thumbnail"] = "./images/" + os.path.basename(media_rel["cover"])
        if media_rel.get("wheel"):
            entry["marquee"] = "./images/" + os.path.basename(media_rel["wheel"])
        if media_rel.get("video"):
            entry["video"] = "./videos/" + os.path.basename(media_rel["video"])
    return entry


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
MODES = ("copy", "hardlink", "symlink")


@dataclass
class Progress:
    files_total: int = 0
    files_done: int = 0
    bytes_total: int = 0
    bytes_done: int = 0
    state: str = "idle"          # idle | running | done | error
    message: str = ""
    output_path: str = ""
    mode: str = "copy"
    copied: int = 0
    linked: int = 0
    fell_back: int = 0           # hardlinks that fell back to copy (cross-device)

    def as_dict(self) -> dict:
        pct = (self.bytes_done / self.bytes_total * 100) if self.bytes_total else 0
        return {
            "state": self.state, "message": self.message, "mode": self.mode,
            "files_total": self.files_total, "files_done": self.files_done,
            "bytes_total": self.bytes_total, "bytes_done": self.bytes_done,
            "copied": self.copied, "linked": self.linked, "fell_back": self.fell_back,
            "percent": round(pct, 1), "output_path": self.output_path,
        }


def _place_file(src: Path, dst: Path, size: int, mode: str, prog: Progress) -> None:
    """Materialize one file as a copy, hardlink, or symlink (resume-aware)."""
    if dst.exists() or dst.is_symlink():
        if mode == "copy" and _safe_size(dst) == size:
            return
        if mode != "copy":
            return  # a link is already in place
        dst.unlink()  # size differs — replace the copy

    if mode == "symlink":
        os.symlink(os.path.abspath(src), dst)
        prog.linked += 1
        return
    if mode == "hardlink":
        try:
            os.link(src, dst)
            prog.linked += 1
            return
        except OSError:
            # Cross-device (EXDEV) or unsupported FS — fall back to a copy.
            prog.fell_back += 1
    shutil.copy2(src, dst)
    prog.copied += 1


def _place_dir(src: Path, dst: Path, mode: str, prog: Progress) -> None:
    """Place a disc-game folder. Symlink links the whole folder; hardlink/copy
    recreate the tree file-by-file."""
    if mode == "symlink":
        if dst.exists() or dst.is_symlink():
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(os.path.abspath(src), dst)
        prog.linked += 1
        return
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target_dir = dst / rel if rel != "." else dst
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            if f.startswith("."):
                continue  # skip macOS AppleDouble (._*) and other dotfile junk
            s = Path(root) / f
            _place_file(s, target_dir / f, _safe_size(s), mode, prog)


def run_export(plan: Plan, progress: Progress, manifest_epoch: int, mode: str = "copy") -> None:
    if mode not in MODES:
        mode = "copy"
    base = config.OUTPUT_ROOT
    progress.files_total = len(plan.copies) + len(plan.writes)
    progress.bytes_total = plan.total_bytes
    progress.mode = mode
    progress.state = "running"
    progress.output_path = str(base / plan.root_rel)

    for op in plan.copies:
        src = Path(op.src)
        dst = base / op.dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            if op.is_dir:
                _place_dir(src, dst, mode, progress)
            else:
                _place_file(src, dst, op.size, mode, progress)
        except OSError as e:
            progress.message = f"failed: {op.dst} ({e})"
        progress.files_done += 1
        progress.bytes_done += op.size

    for w in plan.writes:
        dst = base / w.dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(w.content, encoding="utf-8")
        progress.files_done += 1

    _write_manifest(plan, manifest_epoch, mode)
    progress.state = "done"
    verb = {"copy": "Copied", "hardlink": "Hardlinked", "symlink": "Symlinked"}[mode]
    extra = f" ({progress.fell_back} copied — cross-device)" if progress.fell_back else ""
    progress.message = f"{verb} {plan.rom_count} games to {plan.target}{extra}"


def _write_manifest(plan: Plan, epoch: int, mode: str = "copy") -> None:
    import json
    manifest = {
        "romforge_export": 1,
        "collection": plan.collection_name,
        "target": plan.target,
        "mode": mode,
        "created_epoch": epoch,
        "rom_count": plan.rom_count,
        "media_count": plan.media_count,
        "total_bytes": plan.total_bytes,
        "systems": plan.summary()["systems"],
    }
    path = config.OUTPUT_ROOT / plan.root_rel / "romforge-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
