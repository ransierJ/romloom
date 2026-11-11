"""Library scanner: disk -> SQLite index.

Walks ROMS_ROOT one level deep for systems, then for each system reads the ROM
files and the Skyscraper media/ folder, matching art and metadata to each ROM by
filename stem. Pure standard library so it can run without the web stack.

Can be executed directly for a quick sanity check against a real library:

    python -m app.scanner            # scan configured ROMS_ROOT
    python -m app.scanner /path/roms # scan an explicit root
"""
from __future__ import annotations

import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from . import config, db, platforms, thumbnails

# Metadata fields we lift out of each media/textual/<stem>.xml.
_TEXTUAL_FIELDS = (
    "name", "rating", "releasedate", "developer", "publisher", "genre", "players",
)


@dataclass
class ScanStats:
    systems: int = 0
    scanned: int = 0
    skipped: int = 0
    roms: int = 0
    complete: int = 0
    partial: int = 0
    missing: int = 0
    warmed: int = 0
    seconds: float = 0.0


def _refresh_mappings(conn) -> None:
    """Re-resolve every system's frontend ids from the current platform map,
    keyed by folder (not re-deriving the canonical id, so collections are safe).
    Runs on every scan so platform-map updates reach even skipped systems."""
    for row in conn.execute("SELECT id, folder FROM systems").fetchall():
        plat = platforms.resolve(row["folder"])
        conn.execute(
            "UPDATE systems SET display_name=?, es_id=?, batocera_id=?, retrobat_id=?, known=? "
            "WHERE id=?",
            (plat.display, plat.es, plat.batocera, plat.retrobat, int(plat.known), row["id"]),
        )


def _apply_overrides(conn) -> None:
    """Apply per-system mapping overrides on top of the built-in platform map."""
    for r in conn.execute("SELECT * FROM system_overrides"):
        sets, params = [], []
        for col in ("display_name", "es_id", "batocera_id", "retrobat_id"):
            if r[col] is not None:
                sets.append(f"{col} = ?")
                params.append(r[col])
        if sets:
            params.append(r["system_id"])
            conn.execute(f"UPDATE systems SET {', '.join(sets)} WHERE id = ?", params)


# Collapse "Bully - Scholarship Edition [RB7E54]" and
# "Bully - Scholarship Edition (USA) (En,Fr,Es)" to the same normalized key so a
# disc-folder game (named by id) can still find media (named by region).
_PAREN_BRACKET = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_WS = re.compile(r"\s+")


def _normalize(stem: str) -> str:
    return _WS.sub(" ", _PAREN_BRACKET.sub("", stem)).strip().lower()


class MediaIndex:
    """Exact + normalized lookup over one media sub-folder (or merged folders)."""

    def __init__(self) -> None:
        self.exact: Dict[str, str] = {}
        self.norm: Dict[str, str] = {}
        self.src: Dict[str, str] = {}   # same keys -> absolute source path

    def add_dir(self, path: Path) -> None:
        try:
            entries = os.scandir(path)
        except (FileNotFoundError, NotADirectoryError):
            return
        with entries:
            for e in entries:
                if e.is_file():
                    stem = os.path.splitext(e.name)[0]
                    self.exact.setdefault(stem.lower(), e.name)
                    self.norm.setdefault(_normalize(stem), e.name)
                    self.src.setdefault(stem.lower(), e.path)
                    self.src.setdefault(_normalize(stem), e.path)
                elif e.is_dir():
                    # Nested per-game folder (Skraper's disc layout, e.g.
                    # box2dfront/<Game [ID]>/<ID>.png). Index by the folder name,
                    # storing the "folder/innerfile" relative path.
                    try:
                        inner = os.scandir(e.path)
                    except OSError:
                        continue
                    with inner:
                        for f in inner:
                            if not f.is_file() or f.name.startswith("."):
                                continue
                            rel = f"{e.name}/{f.name}"
                            self.exact.setdefault(e.name.lower(), rel)
                            self.norm.setdefault(_normalize(e.name), rel)
                            self.src.setdefault(e.name.lower(), f.path)
                            self.src.setdefault(_normalize(e.name), f.path)
                            break

    def get(self, stem: str) -> Optional[str]:
        hit = self.exact.get(stem.lower())
        if hit:
            return hit
        return self.norm.get(_normalize(stem))

    def get_path(self, stem: str) -> Optional[str]:
        hit = self.src.get(stem.lower())
        if hit:
            return hit
        return self.src.get(_normalize(stem))


def _index_media(media_root: Path, candidates: Iterable[str]) -> MediaIndex:
    """Merge one asset's candidate folders into a single tolerant index."""
    idx = MediaIndex()
    for folder in candidates:
        idx.add_dir(media_root / folder)
    return idx


def _pick(index: "MediaIndex", rom: "RomEntry") -> Optional[str]:
    """Match by a game's primary stem, then its secondary stem (inner disc file)."""
    v = index.get(rom.stem)
    if v is None and rom.alt_stem:
        v = index.get(rom.alt_stem)
    return v


def _pick_path(index: "MediaIndex", rom: "RomEntry") -> Optional[str]:
    """The absolute source path for a game's matched media (for thumb warming)."""
    v = index.get_path(rom.stem)
    if v is None and rom.alt_stem:
        v = index.get_path(rom.alt_stem)
    return v


def _parse_textual(path: Path) -> Dict[str, Optional[str]]:
    """Parse a Skyscraper textual XML into a flat dict. Tolerant of junk."""
    data: Dict[str, Optional[str]] = {f: None for f in _TEXTUAL_FIELDS}
    data["description"] = None
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return data
    for child in root:
        tag = child.tag.lower()
        text = (child.text or "").strip() or None
        if tag == "desc":
            data["description"] = text
        elif tag in data:
            data[tag] = text
    return data


def _es_date_to_iso(d: Optional[str]) -> Optional[str]:
    """EmulationStation date '19911102T000000' -> '1991-11-02' (textual format)."""
    if not d:
        return None
    digits = re.sub(r"[^0-9]", "", d)[:8]
    return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) == 8 else d


def _parse_gamelist(path: Path) -> Dict[str, Dict[str, Optional[str]]]:
    """Parse an EmulationStation gamelist.xml (as Skraper/Batocera/ES-DE write)
    into {stem_key -> metadata}, matched by the <path> basename. Keyed by both
    exact-lowercase and normalized stem so id/region-named ROMs still match."""
    out: Dict[str, Dict[str, Optional[str]]] = {}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return out
    for game in root.findall("game"):
        p = game.findtext("path") or ""
        if not p:
            continue
        stem = os.path.splitext(os.path.basename(p.replace("\\", "/")))[0]
        data: Dict[str, Optional[str]] = {
            "name": game.findtext("name"),
            "description": game.findtext("desc"),
            "rating": game.findtext("rating"),
            "releasedate": _es_date_to_iso(game.findtext("releasedate")),
            "developer": game.findtext("developer"),
            "publisher": game.findtext("publisher"),
            "genre": game.findtext("genre"),
            "players": game.findtext("players"),
        }
        data = {k: (v.strip() if isinstance(v, str) and v.strip() else None)
                for k, v in data.items()}
        out.setdefault(stem.lower(), data)
        out.setdefault(_normalize(stem), data)
    return out


def _load_gamelist(sys_path: Path) -> Dict[str, Dict[str, Optional[str]]]:
    """Find and parse a system's gamelist.xml (root, then media/)."""
    for candidate in (sys_path / "gamelist.xml", sys_path / "media" / "gamelist.xml"):
        if candidate.is_file():
            return _parse_gamelist(candidate)
    return {}


def _coverage(assets: Dict[str, bool]) -> str:
    """complete = all required assets present; missing = none of them; else partial."""
    required = [assets.get(k, False) for k in config.COMPLETE_REQUIRES]
    if all(required):
        return "complete"
    if not any(required):
        return "missing"
    return "partial"


# The media sub-folders whose mtime/count we fingerprint (plural + singular
# variants, matching the scan's tolerant resolution).
_SIG_DIRS = ("covers", "cover", "box2dfront", "box3d",
             "screenshots", "screenshot", "wheels", "wheel",
             "marquees", "marquee", "textual", "videos")


def _dir_fingerprint(path: str) -> str:
    """A directory's mtime — a single stat, NAS-cheap. The mtime bumps whenever
    an entry is added, removed, or renamed in that directory."""
    try:
        return str(os.stat(path).st_mtime_ns)
    except OSError:
        return "-"


def _system_signature(sys_path: Path) -> str:
    """Directory-level fingerprint of a system: the ROM root + each media
    sub-folder's (mtime, entry count). ~10 readdirs per system instead of
    statting 13k files, so an incremental rescan is near-instant over a NAS.

    This catches the common changes (ROMs added/removed, new games scraped). It
    can miss an in-place overwrite of an existing file with no rename — use a
    full (forced) rescan after a bulk re-scrape to be certain.
    """
    parts = ["root:" + _dir_fingerprint(str(sys_path))]
    media = str(sys_path / "media")
    for sub in _SIG_DIRS:
        parts.append(f"{sub}:" + _dir_fingerprint(os.path.join(media, sub)))
    return "|".join(parts)


def _iter_system_dirs(root: Path) -> Iterable[os.DirEntry]:
    try:
        entries = list(os.scandir(root))
    except FileNotFoundError:
        raise FileNotFoundError(f"ROMS_ROOT does not exist: {root}")
    for e in sorted(entries, key=lambda x: x.name.lower()):
        if not e.is_dir():
            continue
        if e.name.startswith(".") or e.name in config.IGNORED_SYSTEM_DIRS:
            continue
        yield e


@dataclass
class RomEntry:
    stem: str          # media/metadata join key
    filename: str      # what the user selects (file name or disc-folder name)
    ext: str           # lowercased, no dot
    size: int
    alt_stem: str = "" # secondary join key (inner ROM name for disc folders)


def _disc_primary(folder: Path) -> Optional[Tuple[str, str, int]]:
    """For a disc-style game folder, return (inner_filename, ext, size) of its
    largest ROM file."""
    best: Optional[Tuple[str, str, int]] = None
    try:
        for e in os.scandir(folder):
            if not e.is_file() or e.name.startswith("."):
                continue
            ext = os.path.splitext(e.name)[1].lower()
            if ext in config.NON_ROM_EXTENSIONS:
                continue
            try:
                size = e.stat().st_size
            except OSError:
                size = 0
            if best is None or size > best[2]:
                best = (e.name, ext.lstrip("."), size)
    except OSError:
        return None
    return best


_TRACK_RE = re.compile(r'(\S+\.(?:bin|img|raw|iso))', re.IGNORECASE)


def _cue_referenced_files(sys_path: Path) -> set:
    """Filenames referenced by .cue/.gdi sheets in a system folder — their track
    files shouldn't also count as games."""
    refs: set = set()
    try:
        entries = list(os.scandir(sys_path))
    except OSError:
        return refs
    for e in entries:
        if not e.is_file() or not e.name.lower().endswith(config.CUESHEET_EXTENSIONS):
            continue
        try:
            text = Path(e.path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in re.finditer(r'FILE\s+"([^"]+)"', text):     # quoted (may contain spaces)
            refs.add(_cue_basename(m.group(1)))
        for m in _TRACK_RE.finditer(text):                   # unquoted / .gdi tracks
            refs.add(_cue_basename(m.group(1).strip('"')))
    return refs


def _cue_basename(ref: str) -> str:
    """Lowercased basename of a cue FILE reference, tolerant of Windows paths.
    Cue sheets authored on Windows embed absolute paths with backslashes
    (e.g. C:\\SEGA\\...\\GAME.BIN); Linux os.path.basename won't split those."""
    ref = ref.replace("\\", "/")
    return os.path.basename(ref).strip().lower()


def _parse_region(filename: str) -> str:
    """Comma-joined region tags from a No-Intro/GoodTools filename, e.g. 'USA,Europe'."""
    tags = []
    for group in re.findall(r'[\(\[]([^)\]]*)[)\]]', filename):
        for tok in group.split(','):
            t = tok.strip().lower()
            for region, keys in config.REGION_TOKENS.items():
                if t in keys and region not in tags:
                    tags.append(region)
    return ",".join(tags)


def _enumerate_roms(sys_path: Path, allowed_exts: set | None = None) -> Iterable[RomEntry]:
    """File-based ROMs in the system root, plus disc-style game subfolders.
    allowed_exts, when given, is a lowercased extension allowlist ({'.nds'}):
    only files with those extensions count (overrides NON_ROM_EXTENSIONS)."""
    excluded = _cue_referenced_files(sys_path)
    try:
        entries = list(os.scandir(sys_path))
    except OSError:
        return
    # Resolve file ROMs first so a subfolder sharing a file ROM's name can be
    # recognised as that ROM's companion data rather than a standalone game.
    file_stems: set = set()
    files = []
    dirs = []
    for e in entries:
        name = e.name
        if name.startswith(".") or name in config.IGNORED_SYSTEM_DIRS:
            continue
        if e.is_file():
            if name.lower() in excluded:
                continue
            stem, ext = os.path.splitext(name)
            if allowed_exts is not None:
                if ext.lower() not in allowed_exts:
                    continue
            elif ext.lower() in config.NON_ROM_EXTENSIONS:
                continue
            file_stems.add(stem.lower())
            files.append((e, name, stem, ext))
        elif e.is_dir() and name.lower() not in config.NON_GAME_DIRS:
            dirs.append((e, name))

    for e, name, stem, ext in files:
        try:
            size = e.stat().st_size
        except OSError:
            size = 0
        yield RomEntry(stem, name, ext.lstrip(".").lower(), size)

    for e, name in dirs:
        # A subfolder named like a sibling file ROM is that ROM's companion data,
        # not its own game — e.g. MAME's <game>/<game>.chd beside <game>.zip. It
        # travels with the file ROM on export, so don't list it separately.
        if name.lower() in file_stems:
            continue
        primary = _disc_primary(Path(e.path))
        if primary is None:
            continue  # empty / media-only dir, not a game
        inner_name, ext, size = primary
        # Match media/metadata by folder name AND the inner ROM name, so
        # scrapers that key by the inner disc file (e.g. Wii S5BETL.wbfs)
        # still line up with this folder-named game.
        yield RomEntry(name, name, ext, size,
                       alt_stem=os.path.splitext(inner_name)[0])


def _scan_system(conn, sys_dir: os.DirEntry, signature: str = "") -> tuple[int, int]:
    """Index one system folder. Returns (rom_count, complete_count)."""
    plat = platforms.resolve(sys_dir.name)
    sys_path = Path(sys_dir.path)
    media_root = sys_path / "media"

    media = {key: _index_media(media_root, cands)
             for key, cands in config.MEDIA_CANDIDATES.items()}
    textual = _index_media(media_root, ("textual",))
    video = _index_media(media_root, config.VIDEO_CANDIDATES)
    gamelist = _load_gamelist(sys_path)   # ES/Skraper metadata fallback

    conn.execute(
        "INSERT OR REPLACE INTO systems"
        "(id, folder, display_name, known, es_id, batocera_id, retrobat_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (plat.canonical, sys_dir.name, plat.display, int(plat.known),
         plat.es, plat.batocera, plat.retrobat),
    )
    conn.execute("DELETE FROM games WHERE system_id = ?", (plat.canonical,))

    rom_count = 0
    complete_count = 0
    rows = []
    warm_targets: Dict[str, set] = {}   # abs media path -> {widths} to pre-thumb
    allowed_exts = config.SYSTEM_ROM_EXTENSIONS.get(plat.canonical)
    for rom in _enumerate_roms(sys_path, allowed_exts):
        cover = _pick(media["cover"], rom)
        shot = _pick(media["screenshot"], rom)
        for asset, idx in (("cover", media["cover"]), ("screenshot", media["screenshot"])):
            widths = config.THUMB_WARM_WIDTHS.get(asset)
            if not widths:
                continue
            p = _pick_path(idx, rom)
            if p:
                warm_targets.setdefault(p, set()).update(widths)
        wheel = _pick(media["wheel"], rom)
        marquee = _pick(media["marquee"], rom)
        video_name = _pick(video, rom)
        textual_name = _pick(textual, rom)

        # Metadata: per-game textual/ first, then gamelist.xml fallback.
        meta = {f: None for f in _TEXTUAL_FIELDS}
        meta["description"] = None
        has_meta = False
        if textual_name:
            meta = _parse_textual(media_root / "textual" / textual_name)
            has_meta = True
        elif gamelist:
            gm = gamelist.get(rom.stem.lower()) or gamelist.get(_normalize(rom.stem))
            if not gm and rom.alt_stem:
                gm = (gamelist.get(rom.alt_stem.lower())
                      or gamelist.get(_normalize(rom.alt_stem)))
            if gm:
                meta = gm
                has_meta = True
        has_textual = has_meta  # column stores "has any metadata"

        rating = None
        if meta.get("rating"):
            try:
                rating = float(meta["rating"])
            except ValueError:
                rating = None

        cov = _coverage({
            "cover": bool(cover), "screenshot": bool(shot),
            "wheel": bool(wheel), "marquee": bool(marquee),
            "textual": has_textual,
        })
        if cov == "complete":
            complete_count += 1
        rom_count += 1

        rows.append((
            plat.canonical, rom.filename, rom.stem, rom.ext, rom.size,
            meta.get("name") or rom.stem, meta.get("description"), rating,
            meta.get("releasedate"), meta.get("developer"), meta.get("publisher"),
            meta.get("genre"), meta.get("players"),
            cover, shot, wheel, marquee, video_name, _parse_region(rom.filename),
            int(has_textual), cov,
        ))

    conn.executemany(
        "INSERT INTO games"
        "(system_id, rom_filename, stem, ext, size_bytes,"
        " name, description, rating, releasedate, developer, publisher, genre, players,"
        " cover_file, screenshot_file, wheel_file, marquee_file, video_file, region, has_textual, coverage) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.execute(
        "UPDATE systems SET rom_count = ?, complete_count = ?, scan_sig = ? WHERE id = ?",
        (rom_count, complete_count, signature, plat.canonical),
    )
    warmed = _warm_thumbs(warm_targets)
    return rom_count, complete_count, warmed


def _warm_thumbs(targets: Dict[str, set]) -> int:
    """Pre-generate thumbnails for a system's media, several widths per source
    read, in parallel (NAS reads are latency-bound). Returns thumbs created."""
    if not targets:
        return 0
    items = list(targets.items())
    total = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for made in ex.map(lambda kv: thumbnails.warm(Path(kv[0]), kv[1]), items):
            total += made
    return total


def reindex_media(conn, system_id: str, filenames=None) -> int:
    """Re-resolve media + metadata for a system's games and UPDATE rows in place
    (ids stay stable, unlike a full _scan_system which deletes+reinserts). Used
    after a scrape writes fresh art/gamelist into the library. `filenames`, when
    given, limits the refresh to those rom_filenames. Returns rows updated."""
    row = conn.execute("SELECT folder FROM systems WHERE id = ?", (system_id,)).fetchone()
    if not row:
        return 0
    sys_path = config.ROMS_ROOT / row["folder"]
    media_root = sys_path / "media"
    media = {k: _index_media(media_root, c) for k, c in config.MEDIA_CANDIDATES.items()}
    textual = _index_media(media_root, ("textual",))
    video = _index_media(media_root, config.VIDEO_CANDIDATES)
    gamelist = _load_gamelist(sys_path)

    want = set(filenames) if filenames is not None else None
    games = conn.execute(
        "SELECT id, rom_filename, stem FROM games WHERE system_id = ?", (system_id,)
    ).fetchall()

    warm_targets: Dict[str, set] = {}
    updated = 0
    complete_delta = 0
    for g in games:
        if want is not None and g["rom_filename"] not in want:
            continue
        rom = RomEntry(g["stem"], g["rom_filename"], "", 0)
        cover = _pick(media["cover"], rom)
        shot = _pick(media["screenshot"], rom)
        wheel = _pick(media["wheel"], rom)
        marquee = _pick(media["marquee"], rom)
        video_name = _pick(video, rom)
        textual_name = _pick(textual, rom)
        for asset, idx in (("cover", media["cover"]), ("screenshot", media["screenshot"])):
            p = _pick_path(idx, rom)
            if p:
                warm_targets.setdefault(p, set()).update(config.THUMB_WARM_WIDTHS.get(asset, ()))

        meta = {f: None for f in _TEXTUAL_FIELDS}
        meta["description"] = None
        has_meta = False
        if textual_name:
            meta = _parse_textual(media_root / "textual" / textual_name)
            has_meta = True
        elif gamelist:
            gm = gamelist.get(rom.stem.lower()) or gamelist.get(_normalize(rom.stem))
            if gm:
                meta = gm
                has_meta = True

        rating = None
        if meta.get("rating"):
            try:
                rating = float(meta["rating"])
            except ValueError:
                rating = None
        cov = _coverage({"cover": bool(cover), "screenshot": bool(shot),
                         "wheel": bool(wheel), "marquee": bool(marquee), "textual": has_meta})
        conn.execute(
            "UPDATE games SET name=?, description=?, rating=?, releasedate=?, developer=?, "
            "publisher=?, genre=?, players=?, cover_file=?, screenshot_file=?, wheel_file=?, "
            "marquee_file=?, video_file=?, has_textual=?, coverage=? WHERE id=?",
            (meta.get("name") or rom.stem, meta.get("description"), rating,
             meta.get("releasedate"), meta.get("developer"), meta.get("publisher"),
             meta.get("genre"), meta.get("players"), cover, shot, wheel, marquee,
             video_name, int(has_meta), cov, g["id"]),
        )
        updated += 1

    # Recompute the system's complete_count from scratch (cheap, one query).
    cc = conn.execute(
        "SELECT SUM(coverage='complete') c FROM games WHERE system_id=?", (system_id,)
    ).fetchone()["c"] or 0
    conn.execute("UPDATE systems SET complete_count=? WHERE id=?", (cc, system_id))
    conn.commit()
    _warm_thumbs(warm_targets)
    return updated


def scan(conn, roms_root: Path | None = None, force: bool = False) -> ScanStats:
    root = roms_root or config.ROMS_ROOT
    started = time.monotonic()
    db.init_db(conn)
    db.set_meta(conn, "scan_state", "running")

    # Drop systems that no longer exist on disk.
    present = {platforms.resolve(e.name).canonical for e in _iter_system_dirs(root)}
    existing = {r["id"] for r in conn.execute("SELECT id FROM systems")}
    for gone in existing - present:
        conn.execute("DELETE FROM systems WHERE id = ?", (gone,))

    # Existing signatures, to decide what can be skipped.
    old_sigs = {r["id"]: r["scan_sig"]
                for r in conn.execute("SELECT id, scan_sig FROM systems")}

    stats = ScanStats()
    for sys_dir in _iter_system_dirs(root):
        canonical = platforms.resolve(sys_dir.name).canonical
        sig = _system_signature(Path(sys_dir.path))
        if (not force and old_sigs.get(canonical) == sig
                and conn.execute("SELECT 1 FROM games WHERE system_id = ? LIMIT 1",
                                 (canonical,)).fetchone()):
            stats.skipped += 1
        else:
            _, _, warmed = _scan_system(conn, sys_dir, sig)
            stats.scanned += 1
            stats.warmed += warmed
        stats.systems += 1
        conn.commit()

    _refresh_mappings(conn)
    _apply_overrides(conn)

    agg = conn.execute(
        "SELECT COUNT(*) n,"
        " SUM(coverage='complete') c,"
        " SUM(coverage='partial') p,"
        " SUM(coverage='missing') m FROM games"
    ).fetchone()
    stats.roms = agg["n"] or 0
    stats.complete = agg["c"] or 0
    stats.partial = agg["p"] or 0
    stats.missing = agg["m"] or 0
    stats.seconds = round(time.monotonic() - started, 2)

    db.set_meta(conn, "scan_state", "idle")
    db.set_meta(conn, "last_scan_epoch", str(int(time.time())))
    db.set_meta(conn, "last_scan_seconds", str(stats.seconds))
    db.set_meta(conn, "last_scan_scanned", str(stats.scanned))
    db.set_meta(conn, "last_scan_warmed", str(stats.warmed))
    db.set_meta(conn, "last_scan_skipped", str(stats.skipped))
    db.set_meta(conn, "roms_root", str(root))
    conn.commit()
    return stats


def _main(argv: list[str]) -> int:
    root = Path(argv[1]).expanduser() if len(argv) > 1 else config.ROMS_ROOT
    force = "--force" in argv[1:]
    args = [a for a in argv[1:] if not a.startswith("--")]
    if args:
        root = Path(args[0]).expanduser()
    config.ensure_dirs()
    conn = db.connect()
    print(f"Scanning: {root}" + ("  (forced full)" if force else ""))
    stats = scan(conn, root, force=force)
    print(f"  systems : {stats.systems}  (scanned {stats.scanned}, skipped {stats.skipped})")
    print(f"  roms    : {stats.roms}")
    print(f"  complete: {stats.complete}")
    print(f"  partial : {stats.partial}")
    print(f"  missing : {stats.missing}")
    print(f"  time    : {stats.seconds}s")
    print("\nPer system:")
    for r in conn.execute(
        "SELECT display_name, rom_count, complete_count, known FROM systems "
        "ORDER BY display_name"
    ):
        flag = "" if r["known"] else "  (unmapped)"
        print(f"  {r['display_name']:<34} {r['rom_count']:>5} roms"
              f"  {r['complete_count']:>5} complete{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
