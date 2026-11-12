"""ROMForge HTTP API + static frontend host.

Read-only over the library in Phase 1: scan the disk into the index, browse it,
and serve art. Collections and export arrive in later phases.
"""
from __future__ import annotations

import errno
import io
import json
import mimetypes
import os
import shutil
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from . import catalog, config, db, export, platforms, scanner, scraper, settings, thumbnails
from .scanner import scan

app = FastAPI(title="ROMForge", version="0.1.0")

# A single shared connection is fine: SQLite in WAL mode handles our read-mostly,
# single-writer access pattern, and FastAPI's threadpool is modest.
config.ensure_dirs()
_conn = db.connect()
db.init_db(_conn)

# Guards the one background scan so two can't run at once.
_scan_lock = threading.Lock()
_scanning = False

# Background scrape job (one at a time): live status for the UI to poll.
_scrape_lock = threading.Lock()
_scrape_control = None
_scrape_status = {"state": "idle", "system": None, "scope": None, "phase": None,
                  "current": 0, "total": 0, "message": "", "error": None,
                  "request": None}


# --------------------------------------------------------------------------- #
# Scan
# --------------------------------------------------------------------------- #
def _run_scan(force: bool) -> None:
    global _scanning
    try:
        conn = db.connect()  # own connection for the worker thread
        scan(conn, force=force)
        conn.close()
        _resolve_all_smart()   # keep smart collections current with the library
    finally:
        _scanning = False
        _scan_lock.release()


@app.post("/api/scan")
def start_scan(force: bool = Query(False)):
    global _scanning
    if not _scan_lock.acquire(blocking=False):
        return JSONResponse({"state": "running"}, status_code=409)
    _scanning = True
    threading.Thread(target=_run_scan, args=(force,), daemon=True).start()
    return {"state": "started", "force": force}


@app.get("/api/scan/status")
def scan_status():
    return {
        "state": "running" if _scanning else (db.get_meta(_conn, "scan_state") or "idle"),
        "last_scan_epoch": db.get_meta(_conn, "last_scan_epoch"),
        "last_scan_seconds": db.get_meta(_conn, "last_scan_seconds"),
        "roms_root": db.get_meta(_conn, "roms_root") or str(config.ROMS_ROOT),
    }


# --------------------------------------------------------------------------- #
# Scraping (bundled Skyscraper → ScreenScraper)
# --------------------------------------------------------------------------- #
class ScrapeIn(BaseModel):
    system_id: str
    game_ids: Optional[List[int]] = None   # None/empty = whole system


def _reindex_after_scrape(system_id, filenames):
    try:
        conn = db.connect()
        scanner.reindex_media(conn, system_id, filenames=filenames)
        conn.close()
    except Exception:
        pass


def _run_scrape(system_id: str, folder: str, filenames, control) -> None:
    try:
        cfg = settings.load()
        creds = cfg.get("screenscraper", {})
        prefs = cfg.get("scrape", {})
        scraper.configure(creds.get("user", ""), creds.get("password", ""),
                          region=prefs.get("region") or "us",
                          lang=prefs.get("lang") or "en")

        def prog(cur, total, phase):
            if _scrape_status["state"] == "paused":
                return
            _scrape_status.update(current=cur, total=total, phase=phase,
                                  message=f"{phase} {cur}/{total}")

        scraper.scrape(system_id, folder, filenames=filenames,
                       on_progress=prog, control=control)
        _reindex_after_scrape(system_id, filenames)  # keeps game ids stable
        _scrape_status.update(state="done", phase=None,
                              message="Scrape complete", error=None)
    except scraper.ScrapeCancelled:
        _reindex_after_scrape(system_id, filenames)  # keep whatever landed
        _scrape_status.update(state="stopped", phase=None,
                              message="Scrape stopped", error=None)
    except Exception as e:
        _scrape_status.update(state="error", error=str(e), message=str(e))
    finally:
        _scrape_lock.release()


@app.get("/api/scrape/available")
def scrape_available(system_id: Optional[str] = None):
    creds = settings.masked(settings.load()).get("screenscraper", {})
    out = {
        "installed": scraper.available(),
        "credentials_set": bool(creds.get("user") and creds.get("password_set")),
    }
    if system_id:
        out["platform"] = scraper.platform(system_id)   # None => unsupported
    return out


@app.post("/api/scrape")
def start_scrape(body: ScrapeIn):
    if not scraper.available():
        raise HTTPException(503, "Skyscraper is not installed in this image")
    if scraper.platform(body.system_id) is None:
        raise HTTPException(400, f"Scraping isn't supported for '{body.system_id}'")
    folder = _system_folder(body.system_id)
    if not folder:
        raise HTTPException(404, "Unknown system")
    creds = settings.load().get("screenscraper", {})
    if not (creds.get("user") and creds.get("password")):
        raise HTTPException(400, "Set your ScreenScraper login in Settings first")

    filenames = None
    if body.game_ids:
        rows = _conn.execute(
            "SELECT rom_filename FROM games WHERE system_id=? AND id IN (%s)"
            % ",".join("?" * len(body.game_ids)),
            [body.system_id, *body.game_ids],
        ).fetchall()
        filenames = [r["rom_filename"] for r in rows]
        if not filenames:
            raise HTTPException(400, "No matching games to scrape")

    if not _scrape_lock.acquire(blocking=False):
        return JSONResponse({"state": "running"}, status_code=409)
    global _scrape_control
    _scrape_control = scraper.ScrapeControl()
    scope = "games" if filenames else "system"
    _scrape_status.update(
        state="running", system=body.system_id, scope=scope, phase="starting",
        current=0, total=len(filenames) if filenames else 0,
        message="Starting Skyscraper…", error=None,
        request={"system_id": body.system_id, "game_ids": body.game_ids or None},
    )
    threading.Thread(target=_run_scrape,
                     args=(body.system_id, folder, filenames, _scrape_control),
                     daemon=True).start()
    return {"state": "started", "scope": scope,
            "count": len(filenames) if filenames else None}


@app.get("/api/scrape/status")
def scrape_status():
    return dict(_scrape_status)


@app.post("/api/scrape/stop")
def stop_scrape():
    if _scrape_control and _scrape_status["state"] in ("running", "paused"):
        _scrape_control.stop()
        return {"ok": True}
    raise HTTPException(409, "No scrape is running")


@app.post("/api/scrape/pause")
def pause_scrape():
    if _scrape_control and _scrape_status["state"] == "running":
        _scrape_control.pause()
        _scrape_status.update(state="paused", message="Paused")
        return {"ok": True}
    raise HTTPException(409, "No running scrape to pause")


@app.post("/api/scrape/resume")
def resume_scrape():
    if _scrape_control and _scrape_status["state"] == "paused":
        _scrape_control.resume()
        _scrape_status.update(state="running", message="Resuming…")
        return {"ok": True}
    raise HTTPException(409, "Scrape is not paused")


# --------------------------------------------------------------------------- #
# Systems & games
# --------------------------------------------------------------------------- #
@app.get("/api/systems")
def list_systems():
    rows = _conn.execute(
        "SELECT id, display_name, folder, known, rom_count, complete_count,"
        " es_id, batocera_id, retrobat_id FROM systems "
        "WHERE rom_count > 0 ORDER BY display_name"
    ).fetchall()
    return [dict(r) for r in rows]


class SystemOverride(BaseModel):
    display_name: Optional[str] = None
    es_id: Optional[str] = None
    batocera_id: Optional[str] = None
    retrobat_id: Optional[str] = None


@app.get("/api/systems/{sid}/mapping")
def system_mapping(sid: str):
    row = _conn.execute("SELECT * FROM systems WHERE id = ?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown system")
    d = platforms.resolve(row["folder"])
    ov = _conn.execute("SELECT * FROM system_overrides WHERE system_id = ?", (sid,)).fetchone()
    keys = ("display_name", "es_id", "batocera_id", "retrobat_id")
    return {
        "system_id": sid, "folder": row["folder"],
        "current": {k: row[k] for k in keys},
        "default": {"display_name": d.display, "es_id": d.es,
                    "batocera_id": d.batocera, "retrobat_id": d.retrobat, "known": d.known},
        "override": {k: (ov[k] if ov else None) for k in keys},
    }


@app.patch("/api/systems/{sid}/mapping")
def patch_system_mapping(sid: str, body: SystemOverride):
    row = _conn.execute("SELECT folder FROM systems WHERE id = ?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown system")
    d = platforms.resolve(row["folder"])

    def norm(v):
        v = v.strip() if isinstance(v, str) else v
        return v or None

    ov = {k: norm(getattr(body, k)) for k in
          ("display_name", "es_id", "batocera_id", "retrobat_id")}

    if any(ov.values()):
        _conn.execute(
            "INSERT INTO system_overrides(system_id, display_name, es_id, batocera_id, retrobat_id) "
            "VALUES (?,?,?,?,?) ON CONFLICT(system_id) DO UPDATE SET "
            "display_name=excluded.display_name, es_id=excluded.es_id, "
            "batocera_id=excluded.batocera_id, retrobat_id=excluded.retrobat_id",
            (sid, ov["display_name"], ov["es_id"], ov["batocera_id"], ov["retrobat_id"]),
        )
    else:
        _conn.execute("DELETE FROM system_overrides WHERE system_id = ?", (sid,))

    # Effective = override, else built-in default, else the canonical id.
    eff = {
        "display_name": ov["display_name"] or d.display,
        "es_id": ov["es_id"] or d.es or sid,
        "batocera_id": ov["batocera_id"] or d.batocera or sid,
        "retrobat_id": ov["retrobat_id"] or d.retrobat or sid,
    }
    _conn.execute(
        "UPDATE systems SET display_name=?, es_id=?, batocera_id=?, retrobat_id=? WHERE id=?",
        (eff["display_name"], eff["es_id"], eff["batocera_id"], eff["retrobat_id"], sid),
    )
    _conn.commit()
    return system_mapping(sid)


class ScreenscraperIn(BaseModel):
    user: Optional[str] = None
    password: Optional[str] = None


class IgdbIn(BaseModel):
    client_id: Optional[str] = None
    client_secret: Optional[str] = None


class ScrapePrefsIn(BaseModel):
    source: Optional[str] = None
    missing_only: Optional[bool] = None
    region: Optional[str] = None
    lang: Optional[str] = None


class SettingsIn(BaseModel):
    screenscraper: Optional[ScreenscraperIn] = None
    igdb: Optional[IgdbIn] = None
    scrape: Optional[ScrapePrefsIn] = None


@app.get("/api/settings")
def get_settings():
    return settings.masked(settings.load())


@app.patch("/api/settings")
def patch_settings(body: SettingsIn):
    merged = settings.apply_patch(body.model_dump(exclude_none=True))
    return settings.masked(merged)


@app.get("/api/stats")
def stats():
    row = _conn.execute(
        "SELECT COUNT(*) roms,"
        " SUM(coverage='complete') complete,"
        " SUM(coverage='partial') partial,"
        " SUM(coverage='missing') missing FROM games"
    ).fetchone()
    systems = _conn.execute(
        "SELECT COUNT(*) n FROM systems WHERE rom_count > 0"
    ).fetchone()["n"]
    return {
        "systems": systems,
        "roms": row["roms"] or 0,
        "complete": row["complete"] or 0,
        "partial": row["partial"] or 0,
        "missing": row["missing"] or 0,
    }


_SORTS = {
    "name": "g.name COLLATE NOCASE ASC",
    "name_desc": "g.name COLLATE NOCASE DESC",
    "rating": "g.rating DESC NULLS LAST, g.name COLLATE NOCASE ASC",
    "coverage": "CASE g.coverage WHEN 'missing' THEN 0 WHEN 'partial' THEN 1 ELSE 2 END ASC, g.name COLLATE NOCASE ASC",
    # These reference the user-data / release columns; every list query joins
    # game_user_data, so the aliases resolve.
    "favorite": "COALESCE(u.favorite,0) DESC, u.rating DESC NULLS LAST, g.name COLLATE NOCASE ASC",
    "myrating": "u.rating DESC NULLS LAST, g.name COLLATE NOCASE ASC",
    "year": "g.releasedate DESC NULLS LAST, g.name COLLATE NOCASE ASC",
    "year_asc": "g.releasedate ASC NULLS LAST, g.name COLLATE NOCASE ASC",
}


# Per-game user data joined into list/detail queries (aliased to avoid colliding
# with the scraper's own `rating` column).
_USER_JOIN = ("LEFT JOIN game_user_data u "
              "ON u.system_id = g.system_id AND u.rom_filename = g.rom_filename")
_USER_COLS = ("u.favorite AS u_favorite, u.rating AS u_rating, u.status AS u_status, "
              "u.tags AS u_tags, u.notes AS u_notes")


def _game_dict(r) -> dict:
    k = r.keys()
    return {
        "id": r["id"],
        "system_id": r["system_id"],
        "rom_filename": r["rom_filename"],
        "name": r["name"],
        "ext": r["ext"],
        "size_bytes": r["size_bytes"],
        "rating": r["rating"],
        "releasedate": r["releasedate"],
        "developer": r["developer"],
        "publisher": r["publisher"],
        "genre": r["genre"],
        "players": r["players"],
        "region": r["region"],
        "coverage": r["coverage"],
        "has": {
            "cover": bool(r["cover_file"]),
            "screenshot": bool(r["screenshot_file"]),
            "wheel": bool(r["wheel_file"]),
            "marquee": bool(r["marquee_file"]),
            "video": bool(r["video_file"]),
            "metadata": bool(r["has_textual"]),
        },
        "user": {
            "favorite": bool(r["u_favorite"]) if "u_favorite" in k else False,
            "rating": r["u_rating"] if "u_rating" in k else None,
            "status": (r["u_status"] if "u_status" in k else None) or "",
            "tags": (r["u_tags"] if "u_tags" in k else None) or "",
            "notes": (r["u_notes"] if "u_notes" in k else None) or "",
        },
    }


def _games_where(system_id: str, search: str, coverage: Optional[str],
                 region: Optional[str] = None, genre: Optional[str] = None,
                 favorite: bool = False, status: Optional[str] = None):
    """Shared WHERE builder for library listing and bulk add-by-query. The
    favorite/status predicates reference the game_user_data alias `u`, so callers
    using these must include _USER_JOIN in their query."""
    where = ["g.system_id = ?"]
    params: list = [system_id]
    if search and search.strip():
        where.append("(g.name LIKE ? OR g.rom_filename LIKE ?)")
        term = f"%{search.strip()}%"
        params += [term, term]
    if coverage:
        where.append("g.coverage = ?")
        params.append(coverage)
    if region:
        where.append("g.region LIKE ?")
        params.append(f"%{region}%")
    if genre:
        where.append("g.genre LIKE ?")
        params.append(f"%{genre}%")
    if favorite:
        where.append("COALESCE(u.favorite,0) = 1")
    if status:
        where.append("u.status = ?")
        params.append(status)
    return " AND ".join(where), params


# ---------------------------------------------------------------- catalogs ----
# "Missing games" compares a system against a reference catalog (No-Intro /
# Redump via libretro-database): the complete set of known titles.

def _catalog_meta_dict(system_id: str):
    row = _conn.execute(
        "SELECT source, version, total, fetched_epoch FROM catalog_meta WHERE system_id=?",
        (system_id,),
    ).fetchone()
    if not row:
        return None
    return {"source": row["source"], "version": row["version"],
            "total": row["total"], "fetched_epoch": row["fetched_epoch"]}


def _import_catalog(system_id: str) -> dict:
    """Parse the cached DAT into catalog_titles/catalog_meta (retail titles)."""
    text = catalog.cache_path(system_id).read_bytes().decode("utf-8", "ignore")
    version = catalog.dat_version(text)
    rows = [(system_id, title, key, regions)
            for title, key, regions in catalog.parse_titles(text, retail_only=True)]
    _conn.execute("DELETE FROM catalog_titles WHERE system_id=?", (system_id,))
    _conn.executemany(
        "INSERT OR IGNORE INTO catalog_titles(system_id, title, title_norm, regions) "
        "VALUES (?,?,?,?)", rows)
    _conn.execute(
        "INSERT OR REPLACE INTO catalog_meta"
        "(system_id, source, version, total, fetched_epoch) VALUES (?,?,?,?,?)",
        (system_id, catalog.source_label(system_id), version, len(rows), int(time.time())))
    _conn.commit()
    return _catalog_meta_dict(system_id)


def _have_keys(system_id: str) -> set:
    """Normalized base-title keys for every ROM on disk in this system."""
    stems = _conn.execute(
        "SELECT stem FROM games WHERE system_id=?", (system_id,)).fetchall()
    return {catalog.normkey(r["stem"]) for r in stems}


def _region_match(regions_csv: str, region: str) -> bool:
    regs = regions_csv.split(",") if regions_csv else []
    if region == "World":
        return "World" in regs
    # A World release plays everywhere, so count it toward every regional set.
    return region in regs or "World" in regs


@app.post("/api/systems/{system_id}/catalog/refresh")
def refresh_catalog(system_id: str):
    if not catalog.available(system_id):
        raise HTTPException(404, "no reference catalog exists for this system")
    try:
        catalog.fetch(system_id)
    except Exception as e:  # network / HTTP / parse
        raise HTTPException(502, f"could not fetch catalog: {e}")
    meta = _import_catalog(system_id)
    return {"ok": True, **meta}


@app.get("/api/systems/{system_id}/missing")
def missing_games(
    system_id: str,
    region: str = Query("USA"),
    search: str = "",
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    base = {"available": catalog.available(system_id), "imported": False,
            "source": catalog.source_label(system_id), "region": region,
            "catalog_total": 0, "have": 0, "missing_total": 0, "missing": []}
    if not base["available"]:
        return base
    meta = _catalog_meta_dict(system_id)
    if not meta:
        return base

    have = _have_keys(system_id)
    rows = _conn.execute(
        "SELECT title, title_norm, regions FROM catalog_titles WHERE system_id=?",
        (system_id,)).fetchall()
    in_region = [r for r in rows if _region_match(r["regions"], region)]
    have_count = sum(1 for r in in_region if r["title_norm"] in have)
    missing = [r for r in in_region if r["title_norm"] not in have]
    if search.strip():
        term = search.strip().lower()
        missing = [r for r in missing if term in r["title"].lower()]
    missing.sort(key=lambda r: r["title"].lower())
    page = missing[offset:offset + limit]
    return {**base, "imported": True, "version": meta["version"],
            "fetched_epoch": meta["fetched_epoch"],
            "catalog_total": len(in_region), "have": have_count,
            "missing_total": len(missing),
            "missing": [{"title": r["title"], "regions": r["regions"]} for r in page]}


@app.get("/api/systems/{system_id}/games")
def list_games(
    system_id: str,
    search: str = "",
    coverage: Optional[str] = Query(None, pattern="^(complete|partial|missing)$"),
    sort: str = Query("name"),
    region: Optional[str] = Query(None),
    genre: Optional[str] = Query(None),
    favorite: bool = Query(False),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    selected_for: Optional[int] = Query(None, description="collection id to flag membership"),
):
    if not _conn.execute("SELECT 1 FROM systems WHERE id = ?", (system_id,)).fetchone():
        raise HTTPException(404, "Unknown system")

    where_sql, params = _games_where(system_id, search, coverage, region, genre, favorite, status)
    order = _SORTS.get(sort, _SORTS["name"])

    total = _conn.execute(
        f"SELECT COUNT(*) n FROM games g {_USER_JOIN} WHERE {where_sql}", params
    ).fetchone()["n"]

    sel_join = ""
    sel_col = "0 AS selected"
    sel_params: list = []
    if selected_for is not None:
        sel_col = "(cg.collection_id IS NOT NULL) AS selected"
        sel_join = ("LEFT JOIN collection_games cg ON cg.collection_id = ? "
                    "AND cg.system_id = g.system_id AND cg.rom_filename = g.rom_filename")
        sel_params = [selected_for]

    rows = _conn.execute(
        f"SELECT g.*, {sel_col}, {_USER_COLS} FROM games g {sel_join} {_USER_JOIN} "
        f"WHERE {where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
        sel_params + params + [limit, offset],
    ).fetchall()

    out = []
    for r in rows:
        d = _game_dict(r)
        d["selected"] = bool(r["selected"])
        out.append(d)
    return {"total": total, "limit": limit, "offset": offset, "games": out}


@app.get("/api/systems/{system_id}/genres")
def system_genres(system_id: str):
    """Distinct genres present in a system, for the genre filter dropdown."""
    rows = _conn.execute(
        "SELECT DISTINCT genre FROM games WHERE system_id=? AND genre IS NOT NULL "
        "AND genre != '' ORDER BY genre COLLATE NOCASE", (system_id,)).fetchall()
    return [r["genre"] for r in rows]


@app.get("/api/games/{game_id}")
def game_detail(game_id: int):
    r = _conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Unknown game")
    d = _game_dict(r)
    d["description"] = r["description"]
    d["stem"] = r["stem"]
    u = _conn.execute(
        "SELECT favorite AS u_favorite, rating AS u_rating, status AS u_status, "
        "tags AS u_tags, notes AS u_notes FROM game_user_data "
        "WHERE system_id=? AND rom_filename=?", (r["system_id"], r["rom_filename"])
    ).fetchone()
    if u:
        d["user"] = {"favorite": bool(u["u_favorite"]), "rating": u["u_rating"],
                     "status": u["u_status"] or "", "tags": u["u_tags"] or "",
                     "notes": u["u_notes"] or ""}
    # The drawer resolves video on demand too, so it plays even before a full
    # rescan has indexed video_file for this system.
    if not d["has"]["video"]:
        d["has"]["video"] = _video_path(r) is not None
    return d


# ------------------------------------------------------------- manual editing --
# Edits are written into the library the same way a scraper would: metadata as a
# media/textual/<stem>.xml, art as a file under media/<asset-folder>/. The scanner
# re-reads both, so manual fixes (e.g. ROM hacks no scraper knows) survive a
# rescan and flow into exports — no separate override table needed.

class MetadataIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    developer: Optional[str] = None
    publisher: Optional[str] = None
    genre: Optional[str] = None
    players: Optional[str] = None
    releasedate: Optional[str] = None
    rating: Optional[float] = None


_TEXTUAL_TAGS = (  # (xml tag, metadata field)
    ("name", "name"), ("desc", "description"), ("rating", "rating"),
    ("releasedate", "releasedate"), ("developer", "developer"),
    ("publisher", "publisher"), ("genre", "genre"), ("players", "players"),
)


def _write_textual(path: Path, fields: dict) -> None:
    root = ET.Element("game")
    for tag, key in _TEXTUAL_TAGS:
        val = fields.get(key)
        if val is not None and str(val).strip() != "":
            ET.SubElement(root, tag).text = str(val)
    tree = ET.ElementTree(root)
    if hasattr(ET, "indent"):     # 3.9+ (prettify; cosmetic)
        ET.indent(tree)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _coverage_for(cover, screenshot, has_textual) -> str:
    return scanner._coverage({
        "cover": bool(cover), "screenshot": bool(screenshot),
        "textual": bool(has_textual),
    })


def _readonly_or_500(e: OSError) -> HTTPException:
    if e.errno in _READONLY_ERRNOS:
        return HTTPException(409, "Library is mounted read-only — change /roms:ro "
                                  "to /roms (read-write) to save edits")
    return HTTPException(500, f"write failed: {e}")


@app.patch("/api/games/{game_id}/metadata")
def edit_metadata(game_id: int, body: MetadataIn):
    r = _conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Unknown game")
    folder = _system_folder(r["system_id"])
    if not folder:
        raise HTTPException(404, "Unknown system")

    # The submitted body is the full desired state (blank clears a field), except
    # name falls back to the ROM stem so a game is never left nameless.
    fields = {
        "name": (body.name or "").strip() or r["stem"],
        "description": (body.description or "").strip() or None,
        "developer": (body.developer or "").strip() or None,
        "publisher": (body.publisher or "").strip() or None,
        "genre": (body.genre or "").strip() or None,
        "players": (body.players or "").strip() or None,
        "releasedate": (body.releasedate or "").strip() or None,
        "rating": body.rating,
    }

    textual_dir = config.ROMS_ROOT / folder / "media" / "textual"
    try:
        textual_dir.mkdir(parents=True, exist_ok=True)
        _write_textual(textual_dir / f"{r['stem']}.xml", fields)
    except OSError as e:
        raise _readonly_or_500(e)

    cov = _coverage_for(r["cover_file"], r["screenshot_file"], True)
    _conn.execute(
        "UPDATE games SET name=?, description=?, developer=?, publisher=?, genre=?, "
        "players=?, releasedate=?, rating=?, has_textual=1, coverage=? WHERE id=?",
        (fields["name"], fields["description"], fields["developer"],
         fields["publisher"], fields["genre"], fields["players"],
         fields["releasedate"], fields["rating"], cov, game_id),
    )
    _conn.commit()
    return game_detail(game_id)


class UserDataIn(BaseModel):
    favorite: Optional[bool] = None
    rating: Optional[float] = None       # 0–5 user stars (half steps); 0 clears
    status: Optional[str] = None         # '', playing, beaten, completed, dropped
    tags: Optional[str] = None
    notes: Optional[str] = None


@app.patch("/api/games/{game_id}/user")
def edit_user_data(game_id: int, body: UserDataIn):
    """Favorite / rating / completion / tags / notes — kept in game_user_data by
    natural key so it survives rescans (the games row is rebuilt each scan)."""
    r = _conn.execute(
        "SELECT system_id, rom_filename FROM games WHERE id=?", (game_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Unknown game")
    sid, fn = r["system_id"], r["rom_filename"]
    cur = _conn.execute(
        "SELECT favorite, rating, status, tags, notes FROM game_user_data "
        "WHERE system_id=? AND rom_filename=?", (sid, fn)).fetchone()
    data = {"favorite": (cur["favorite"] if cur else 0),
            "rating": (cur["rating"] if cur else None),
            "status": (cur["status"] if cur else None),
            "tags": (cur["tags"] if cur else None),
            "notes": (cur["notes"] if cur else None)}
    patch = body.model_dump(exclude_unset=True)   # distinguish "omitted" from "cleared"
    if "favorite" in patch:
        data["favorite"] = int(bool(patch["favorite"]))
    if "rating" in patch:
        data["rating"] = patch["rating"] or None
    if "status" in patch:
        data["status"] = (patch["status"] or "").strip() or None
    if "tags" in patch:
        data["tags"] = (patch["tags"] or "").strip() or None
    if "notes" in patch:
        data["notes"] = (patch["notes"] or "").strip() or None
    _conn.execute(
        "INSERT INTO game_user_data"
        "(system_id, rom_filename, favorite, rating, status, tags, notes, updated_epoch) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(system_id, rom_filename) DO UPDATE SET "
        "favorite=excluded.favorite, rating=excluded.rating, status=excluded.status, "
        "tags=excluded.tags, notes=excluded.notes, updated_epoch=excluded.updated_epoch",
        (sid, fn, data["favorite"], data["rating"], data["status"],
         data["tags"], data["notes"], int(time.time())))
    _conn.commit()
    return game_detail(game_id)


@app.post("/api/games/{game_id}/media/{asset}")
async def upload_media(game_id: int, asset: str, file: UploadFile = File(...)):
    if asset not in config.MEDIA_CANDIDATES:
        raise HTTPException(404, "Unknown asset")
    r = _conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Unknown game")
    folder = _system_folder(r["system_id"])
    if not folder:
        raise HTTPException(404, "Unknown system")

    data = await file.read()
    try:
        Image.open(io.BytesIO(data)).verify()   # reject non-images
    except Exception:
        raise HTTPException(400, "Not a readable image file")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in config.IMAGE_EXTENSIONS:
        ext = ".png"
    sub = config.MEDIA_CANDIDATES[asset][0]      # canonical folder (covers, …)
    dest_dir = config.ROMS_ROOT / folder / "media" / sub
    fname = f"{r['stem']}{ext}"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / fname).write_bytes(data)
    except OSError as e:
        raise _readonly_or_500(e)

    _conn.execute(f"UPDATE games SET {asset}_file=? WHERE id=?", (fname, game_id))
    row2 = _conn.execute(
        "SELECT cover_file, screenshot_file, has_textual FROM games WHERE id=?",
        (game_id,)).fetchone()
    cov = _coverage_for(row2["cover_file"], row2["screenshot_file"], row2["has_textual"])
    _conn.execute("UPDATE games SET coverage=? WHERE id=?", (cov, game_id))
    _conn.commit()
    thumbnails.warm(dest_dir / fname, config.THUMB_WARM_WIDTHS.get(asset, (320,)))
    return game_detail(game_id)


def _delete_media_files(r, media_root: Path) -> list:
    """Delete a game's resolved media files (best-effort). Returns their names."""
    gone = []
    for asset in ("cover", "screenshot", "wheel", "marquee"):
        fn = r[f"{asset}_file"]
        if not fn:
            continue
        for sub in config.MEDIA_CANDIDATES[asset]:
            p = media_root / sub / fn
            if p.is_file():
                try:
                    p.unlink()
                    gone.append(f"{sub}/{fn}")
                except OSError:
                    pass
    if r["video_file"]:
        for sub in config.VIDEO_CANDIDATES:
            p = media_root / sub / r["video_file"]
            if p.is_file():
                try:
                    p.unlink()
                    gone.append(f"{sub}/{r['video_file']}")
                except OSError:
                    pass
    for stem in {r["stem"], os.path.splitext(r["rom_filename"])[0]}:
        p = media_root / "textual" / f"{stem}.xml"
        if p.is_file():
            try:
                p.unlink()
                gone.append(f"textual/{stem}.xml")
            except OSError:
                pass
    return gone


_READONLY_ERRNOS = (errno.EROFS, errno.EACCES, errno.EPERM)


def _delete_game_row(r, delete_media: bool = True) -> tuple:
    """Delete one game's ROM + media + index rows. Raises HTTPException on a
    path-safety violation or a read-only mount. Returns (deleted, errors)."""
    folder = _system_folder(r["system_id"])
    if not folder:
        raise HTTPException(404, "Unknown system")
    sys_root = (config.ROMS_ROOT / folder).resolve()
    rom_path = (config.ROMS_ROOT / folder / r["rom_filename"]).resolve()
    if rom_path.parent != sys_root:
        raise HTTPException(400, "Refusing to delete outside the system folder")

    deleted, errors = [], []
    media_root = config.ROMS_ROOT / folder / "media"
    if delete_media:
        deleted += _delete_media_files(r, media_root)
    try:
        if rom_path.is_dir():
            shutil.rmtree(rom_path)
            deleted.append(r["rom_filename"] + "/")
        elif rom_path.is_file():
            rom_path.unlink()
            deleted.append(r["rom_filename"])
        else:
            errors.append("ROM file was not on disk")
    except OSError as e:
        if e.errno in _READONLY_ERRNOS:
            raise HTTPException(
                409,
                "Library is mounted read-only. To delete files, change the ROMs "
                "volume from ':ro' to read-write in your compose file and recreate "
                "the container.",
            )
        raise HTTPException(500, f"Delete failed: {e}")

    _conn.execute("DELETE FROM games WHERE id = ?", (r["id"],))
    _conn.execute("DELETE FROM collection_games WHERE system_id = ? AND rom_filename = ?",
                  (r["system_id"], r["rom_filename"]))
    _conn.execute("UPDATE systems SET rom_count = MAX(0, rom_count - 1) WHERE id = ?",
                  (r["system_id"],))
    _conn.commit()
    return deleted, errors


@app.delete("/api/games/{game_id}")
def delete_game(game_id: int, delete_media: bool = Query(True)):
    """Permanently delete a game's ROM (file or disc folder) and its media from
    disk. Path-validated to the game's own system folder; needs a read-write
    ROMs mount."""
    r = _conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Unknown game")
    deleted, errors = _delete_game_row(r, delete_media)
    return {"deleted": deleted, "errors": errors, "name": r["name"]}


class BulkDeleteBody(BaseModel):
    ids: List[int]


@app.post("/api/games/delete")
def bulk_delete_games(body: BulkDeleteBody):
    """Delete several games from disk. Aborts early with 409 if the mount is
    read-only (so nothing is half-deleted on a misconfigured mount)."""
    deleted_count, failed = 0, []
    for gid in body.ids:
        r = _conn.execute("SELECT * FROM games WHERE id = ?", (gid,)).fetchone()
        if not r:
            continue
        _, errors = _delete_game_row(r, True)   # read-only raises 409, aborting
        deleted_count += 1
        for e in errors:
            failed.append(f"{r['name']}: {e}")
    return {"deleted_count": deleted_count, "errors": failed}


# --------------------------------------------------------------------------- #
# Collections (Phase 2)
# --------------------------------------------------------------------------- #
class CollectionCreate(BaseModel):
    name: str
    note: Optional[str] = None
    rules: Optional[dict] = None       # non-empty => a smart collection


class CollectionPatch(BaseModel):
    name: Optional[str] = None
    note: Optional[str] = None
    rules: Optional[dict] = None       # {} / null clears smart (back to manual)


class GameRef(BaseModel):
    system_id: str
    rom_filename: str


class GamesBody(BaseModel):
    games: List[GameRef]


class QueryAddBody(BaseModel):
    system_id: str
    search: Optional[str] = ""
    coverage: Optional[str] = None


def _collection_or_404(cid: int):
    row = _conn.execute("SELECT * FROM collections WHERE id = ?", (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown collection")
    return row


_SMART_STATUS = {"playing", "beaten", "completed", "dropped"}


def _smart_where(rules: dict):
    """Build a WHERE over games g (+ user join alias u) from a smart-collection
    rule set. Fields AND together; multi-value fields OR within."""
    where, params = [], []
    systems = rules.get("systems") or []
    if systems:
        where.append("g.system_id IN (%s)" % ",".join("?" * len(systems)))
        params += systems
    genres = rules.get("genres") or []
    if genres:
        where.append("(" + " OR ".join(["g.genre LIKE ?"] * len(genres)) + ")")
        params += [f"%{x}%" for x in genres]
    if rules.get("coverage") in ("complete", "partial", "missing"):
        where.append("g.coverage = ?"); params.append(rules["coverage"])
    if rules.get("region"):
        where.append("g.region LIKE ?"); params.append(f"%{rules['region']}%")
    if rules.get("search"):
        where.append("(g.name LIKE ? OR g.rom_filename LIKE ?)")
        t = f"%{rules['search']}%"; params += [t, t]
    if rules.get("favorite"):
        where.append("COALESCE(u.favorite,0) = 1")
    if rules.get("status") in _SMART_STATUS:
        where.append("u.status = ?"); params.append(rules["status"])
    if rules.get("min_rating") is not None:
        try:
            where.append("COALESCE(u.rating,0) >= ?"); params.append(float(rules["min_rating"]))
        except (TypeError, ValueError):
            pass
    return (" AND ".join(where) if where else "1=1"), params


def _resolve_smart(cid: int, rules: dict) -> None:
    """Replace a smart collection's membership with the games matching its rules."""
    wsql, params = _smart_where(rules)
    _conn.execute("DELETE FROM collection_games WHERE collection_id = ?", (cid,))
    _conn.execute(
        "INSERT OR IGNORE INTO collection_games"
        "(collection_id, system_id, rom_filename, added_epoch) "
        f"SELECT ?, g.system_id, g.rom_filename, ? FROM games g {_USER_JOIN} WHERE {wsql}",
        [cid, int(time.time())] + params)
    _conn.commit()


def _resolve_all_smart() -> None:
    """Re-evaluate every smart collection (called after a library scan)."""
    for c in _conn.execute(
            "SELECT id, rules FROM collections WHERE rules IS NOT NULL").fetchall():
        try:
            _resolve_smart(c["id"], json.loads(c["rules"]))
        except Exception:
            pass


def _touch(cid: int) -> None:
    _conn.execute("UPDATE collections SET updated_epoch = ? WHERE id = ?",
                  (int(time.time()), cid))


def _collection_detail(cid: int) -> dict:
    c = _collection_or_404(cid)
    stats = _conn.execute(
        "SELECT COUNT(*) n,"
        " SUM(g.coverage='complete') complete,"
        " SUM(g.coverage='partial') partial,"
        " SUM(g.coverage='missing') missing,"
        " COALESCE(SUM(g.size_bytes),0) bytes "
        "FROM collection_games cg "
        "JOIN games g ON g.system_id=cg.system_id AND g.rom_filename=cg.rom_filename "
        "WHERE cg.collection_id = ?", (cid,)
    ).fetchone()
    total_members = _conn.execute(
        "SELECT COUNT(*) n FROM collection_games WHERE collection_id = ?", (cid,)
    ).fetchone()["n"]
    systems = _conn.execute(
        "SELECT cg.system_id, COALESCE(s.display_name, cg.system_id) display_name, COUNT(*) n "
        "FROM collection_games cg LEFT JOIN systems s ON s.id = cg.system_id "
        "WHERE cg.collection_id = ? GROUP BY cg.system_id ORDER BY display_name",
        (cid,)
    ).fetchall()
    rules = json.loads(c["rules"]) if c["rules"] else None
    return {
        "id": c["id"], "name": c["name"], "note": c["note"],
        "rules": rules, "smart": bool(rules),
        "created_epoch": c["created_epoch"], "updated_epoch": c["updated_epoch"],
        "game_count": total_members,
        "resolved": stats["n"] or 0,
        "coverage": {
            "complete": stats["complete"] or 0,
            "partial": stats["partial"] or 0,
            "missing": stats["missing"] or 0,
        },
        "total_bytes": stats["bytes"] or 0,
        "systems": [dict(r) for r in systems],
    }


@app.get("/api/collections")
def list_collections():
    rows = _conn.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM collection_games cg WHERE cg.collection_id=c.id) game_count "
        "FROM collections c ORDER BY c.updated_epoch DESC, c.name COLLATE NOCASE"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["smart"] = bool(d.pop("rules", None))
        out.append(d)
    return out


@app.post("/api/collections")
def create_collection(body: CollectionCreate):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    now = int(time.time())
    rules = body.rules or None
    cur = _conn.execute(
        "INSERT INTO collections(name, note, rules, created_epoch, updated_epoch) VALUES (?,?,?,?,?)",
        (name, body.note, json.dumps(rules) if rules else None, now, now),
    )
    _conn.commit()
    if rules:
        _resolve_smart(cur.lastrowid, rules)
    return _collection_detail(cur.lastrowid)


@app.get("/api/collections/{cid}")
def get_collection(cid: int):
    return _collection_detail(cid)


@app.patch("/api/collections/{cid}")
def patch_collection(cid: int, body: CollectionPatch):
    _collection_or_404(cid)
    fields = body.model_dump(exclude_unset=True)   # distinguish omitted from cleared
    sets, params = [], []
    if "name" in fields:
        n = (fields["name"] or "").strip()
        if not n:
            raise HTTPException(400, "Name cannot be empty")
        sets.append("name = ?"); params.append(n)
    if "note" in fields:
        sets.append("note = ?"); params.append(fields["note"])
    new_rules, rules_changed = None, False
    if "rules" in fields:
        rules_changed = True
        new_rules = fields["rules"] or None
        sets.append("rules = ?"); params.append(json.dumps(new_rules) if new_rules else None)
    if sets:
        sets.append("updated_epoch = ?"); params.append(int(time.time()))
        _conn.execute(f"UPDATE collections SET {', '.join(sets)} WHERE id = ?", params + [cid])
        _conn.commit()
    if rules_changed and new_rules:
        _resolve_smart(cid, new_rules)   # repopulate from the new rules
    return _collection_detail(cid)


@app.delete("/api/collections/{cid}")
def delete_collection(cid: int):
    _collection_or_404(cid)
    _conn.execute("DELETE FROM collections WHERE id = ?", (cid,))
    _conn.commit()
    return {"deleted": cid}


@app.post("/api/collections/{cid}/add")
def add_games(cid: int, body: GamesBody):
    _collection_or_404(cid)
    now = int(time.time())
    rows = [(cid, g.system_id, g.rom_filename, now) for g in body.games]
    _conn.executemany(
        "INSERT OR IGNORE INTO collection_games"
        "(collection_id, system_id, rom_filename, added_epoch) VALUES (?,?,?,?)", rows)
    _touch(cid); _conn.commit()
    return _collection_detail(cid)


@app.post("/api/collections/{cid}/add-ids")
def add_games_by_id(cid: int, body: BulkDeleteBody):
    """Add games by their (volatile) row id. The natural key is resolved here
    from the games table, so callers can send a selection of ids without having
    every selected row loaded client-side."""
    _collection_or_404(cid)
    now = int(time.time())
    rows = []
    for gid in body.ids:
        r = _conn.execute(
            "SELECT system_id, rom_filename FROM games WHERE id=?", (gid,)).fetchone()
        if r:
            rows.append((cid, r["system_id"], r["rom_filename"], now))
    _conn.executemany(
        "INSERT OR IGNORE INTO collection_games"
        "(collection_id, system_id, rom_filename, added_epoch) VALUES (?,?,?,?)", rows)
    _touch(cid); _conn.commit()
    return _collection_detail(cid)


@app.post("/api/collections/{cid}/refresh")
def refresh_collection(cid: int):
    """Re-read each member game's media + metadata from the library (in place,
    ids stable) so an export reflects edits/scrapes made since the last scan.
    For a smart collection, first re-evaluate its rules."""
    c = _collection_or_404(cid)
    if c["rules"]:
        _resolve_smart(cid, json.loads(c["rules"]))
    rows = _conn.execute(
        "SELECT system_id, rom_filename FROM collection_games WHERE collection_id=?",
        (cid,)).fetchall()
    by_system: dict = {}
    for r in rows:
        by_system.setdefault(r["system_id"], []).append(r["rom_filename"])
    updated = 0
    for system_id, filenames in by_system.items():
        updated += scanner.reindex_media(_conn, system_id, filenames=filenames)
    return {"updated": updated, **_collection_detail(cid)}


@app.post("/api/collections/{cid}/prune")
def prune_collection(cid: int):
    """Drop collection entries whose ROM is no longer in the library (dangling
    natural keys with no matching game row)."""
    _collection_or_404(cid)
    cur = _conn.execute(
        "DELETE FROM collection_games WHERE collection_id=? AND NOT EXISTS ("
        "  SELECT 1 FROM games g WHERE g.system_id=collection_games.system_id "
        "  AND g.rom_filename=collection_games.rom_filename)",
        (cid,))
    removed = cur.rowcount
    _touch(cid)
    _conn.commit()
    return {"removed": removed, **_collection_detail(cid)}


@app.post("/api/collections/{cid}/remove")
def remove_games(cid: int, body: GamesBody):
    _collection_or_404(cid)
    rows = [(cid, g.system_id, g.rom_filename) for g in body.games]
    _conn.executemany(
        "DELETE FROM collection_games "
        "WHERE collection_id=? AND system_id=? AND rom_filename=?", rows)
    _touch(cid); _conn.commit()
    return _collection_detail(cid)


@app.post("/api/collections/{cid}/add-query")
def add_by_query(cid: int, body: QueryAddBody):
    """Add every game matching a system + search + coverage filter at once."""
    _collection_or_404(cid)
    if not _conn.execute("SELECT 1 FROM systems WHERE id = ?", (body.system_id,)).fetchone():
        raise HTTPException(404, "Unknown system")
    where_sql, params = _games_where(body.system_id, body.search or "", body.coverage)
    now = int(time.time())
    _conn.execute(
        "INSERT OR IGNORE INTO collection_games(collection_id, system_id, rom_filename, added_epoch) "
        f"SELECT ?, g.system_id, g.rom_filename, ? FROM games g WHERE {where_sql}",
        [cid, now] + params,
    )
    _touch(cid); _conn.commit()
    return _collection_detail(cid)


@app.get("/api/collections/{cid}/games")
def collection_games(
    cid: int,
    search: str = "",
    coverage: Optional[str] = Query(None, pattern="^(complete|partial|missing)$"),
    sort: str = Query("name"),
    system: Optional[str] = None,
    region: Optional[str] = Query(None),
    genre: Optional[str] = Query(None),
    favorite: bool = Query(False),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    _collection_or_404(cid)
    where = ["cg.collection_id = ?"]
    params: list = [cid]
    if system:
        where.append("cg.system_id = ?"); params.append(system)
    if search.strip():
        where.append("(g.name LIKE ? OR g.rom_filename LIKE ?)")
        term = f"%{search.strip()}%"; params += [term, term]
    if coverage:
        where.append("g.coverage = ?"); params.append(coverage)
    if region:
        where.append("g.region LIKE ?"); params.append(f"%{region}%")
    if genre:
        where.append("g.genre LIKE ?"); params.append(f"%{genre}%")
    if favorite:
        where.append("COALESCE(u.favorite,0) = 1")
    if status:
        where.append("u.status = ?"); params.append(status)
    where_sql = " AND ".join(where)
    order = _SORTS.get(sort, _SORTS["name"])
    join = ("FROM collection_games cg "
            "JOIN games g ON g.system_id=cg.system_id AND g.rom_filename=cg.rom_filename")
    total = _conn.execute(
        f"SELECT COUNT(*) n {join} {_USER_JOIN} WHERE {where_sql}", params
    ).fetchone()["n"]
    rows = _conn.execute(
        f"SELECT g.*, {_USER_COLS} {join} {_USER_JOIN} WHERE {where_sql} "
        f"ORDER BY {order} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    out = []
    for r in rows:
        d = _game_dict(r)
        d["selected"] = True
        out.append(d)
    return {"total": total, "limit": limit, "offset": offset, "games": out}


# --------------------------------------------------------------------------- #
# Export (Phase 3)
# --------------------------------------------------------------------------- #
class ExportBody(BaseModel):
    target: str
    assets: Optional[List[str]] = None
    mode: str = "copy"
    dry_run: bool = False


_export_lock = threading.Lock()
_export_progress = export.Progress()


def _run_export(plan: export.Plan, mode: str) -> None:
    try:
        export.run_export(plan, _export_progress, int(time.time()), mode=mode)
    except Exception as e:  # noqa: BLE001 — surface any failure to the UI
        _export_progress.state = "error"
        _export_progress.message = str(e)
    finally:
        _export_lock.release()


@app.post("/api/collections/{cid}/export")
def start_export(cid: int, body: ExportBody):
    _collection_or_404(cid)
    if body.target not in export.TARGETS:
        raise HTTPException(400, f"target must be one of {export.TARGETS}")
    try:
        plan = export.build_plan(_conn, cid, body.target, body.assets)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if body.dry_run:
        return {"dry_run": True, "plan": plan.summary()}

    if body.mode not in export.MODES:
        raise HTTPException(400, f"mode must be one of {export.MODES}")
    if not _export_lock.acquire(blocking=False):
        return JSONResponse({"state": "running"}, status_code=409)
    # Reset progress for the new job.
    global _export_progress
    _export_progress = export.Progress()
    threading.Thread(target=_run_export, args=(plan, body.mode), daemon=True).start()
    return {"dry_run": False, "state": "started", "plan": plan.summary()}


@app.get("/api/export/status")
def export_status():
    return _export_progress.as_dict()


class PresetBody(BaseModel):
    name: str
    target: str
    assets: List[str]
    mode: str = "copy"


def _preset_dict(r) -> dict:
    return {"id": r["id"], "name": r["name"], "target": r["target"],
            "assets": [a for a in (r["assets"] or "").split(",") if a], "mode": r["mode"]}


@app.get("/api/presets")
def list_presets():
    rows = _conn.execute("SELECT * FROM export_presets ORDER BY name COLLATE NOCASE").fetchall()
    return [_preset_dict(r) for r in rows]


@app.post("/api/presets")
def create_preset(body: PresetBody):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    if body.target not in export.TARGETS:
        raise HTTPException(400, "Bad target")
    mode = body.mode if body.mode in export.MODES else "copy"
    assets = ",".join(a for a in body.assets if a in export.ALL_ASSETS)
    cur = _conn.execute(
        "INSERT INTO export_presets(name, target, assets, mode) VALUES (?,?,?,?)",
        (name, body.target, assets, mode),
    )
    _conn.commit()
    r = _conn.execute("SELECT * FROM export_presets WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _preset_dict(r)


@app.delete("/api/presets/{pid}")
def delete_preset(pid: int):
    _conn.execute("DELETE FROM export_presets WHERE id = ?", (pid,))
    _conn.commit()
    return {"deleted": pid}


# --------------------------------------------------------------------------- #
# Media
# --------------------------------------------------------------------------- #
def _system_folder(system_id: str) -> Optional[str]:
    row = _conn.execute("SELECT folder FROM systems WHERE id = ?", (system_id,)).fetchone()
    return row["folder"] if row else None


def _media_path(game_row, asset: str) -> Optional[Path]:
    """Locate the on-disk media file for a game + asset, honoring folder variants."""
    filename = game_row[f"{asset}_file"]
    if not filename:
        return None
    folder = _system_folder(game_row["system_id"])
    if not folder:
        return None
    media_root = config.ROMS_ROOT / folder / "media"
    for sub in config.MEDIA_CANDIDATES[asset]:
        candidate = media_root / sub / filename
        if candidate.is_file():
            return candidate
    return None


def _video_path(game_row) -> Optional[Path]:
    """Locate a game's gameplay video. Uses the indexed filename when present;
    falls back to resolving by stem on demand so playback still works for
    systems an incremental rescan skipped (video_file not yet populated)."""
    folder = _system_folder(game_row["system_id"])
    if not folder:
        return None
    media_root = config.ROMS_ROOT / folder / "media"
    filename = game_row["video_file"]
    if not filename:
        idx = scanner._index_media(media_root, config.VIDEO_CANDIDATES)
        filename = idx.get(game_row["stem"])
        if not filename:
            return None
    for sub in config.VIDEO_CANDIDATES:
        p = media_root / sub / filename
        if p.is_file():
            return p
    return None


@app.get("/api/games/{game_id}/media/{asset}")
def game_media(
    game_id: int,
    asset: str,
    w: int = Query(0, ge=0, le=1024),
):
    if asset not in config.MEDIA_CANDIDATES:
        raise HTTPException(404, "Unknown asset")
    r = _conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Unknown game")
    src = _media_path(r, asset)
    if not src:
        raise HTTPException(404, "No such media")
    if w and w > 0:
        # The grid always requests a thumbnail. If one can't be produced (the
        # NAS read failed, or the art is corrupt), don't fall back to streaming
        # the full-size original off the same flaky mount — that's what turned a
        # missing/unreadable cover into a 500. A 404 lets the UI show its
        # placeholder and RetryImg back off cleanly.
        thumb = thumbnails.thumbnail(src, w)
        if not thumb:
            raise HTTPException(404, "Thumbnail unavailable")
        return FileResponse(thumb, media_type="image/jpeg")
    if not src.is_file():
        raise HTTPException(404, "No such media")
    return FileResponse(src)


@app.get("/api/games/{game_id}/video")
def game_video(game_id: int):
    r = _conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Unknown game")
    src = _video_path(r)
    if not src:
        raise HTTPException(404, "No video")
    # FileResponse honors Range requests, so the player can seek/stream.
    media_type, _ = mimetypes.guess_type(str(src))
    return FileResponse(src, media_type=media_type or "video/mp4")


# A tiny inline favicon so browsers stop logging a 404 for /favicon.ico — the
# brand mark (accent square + joystick dot) as scalable SVG, no asset to ship.
_FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#B0711A"/>'
    '<circle cx="16" cy="13" r="4.2" fill="#fff"/>'
    '<rect x="14.4" y="13" width="3.2" height="10" rx="1.6" fill="#fff"/>'
    '</svg>'
)


@app.get("/favicon.ico")
def favicon():
    return Response(content=_FAVICON, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


# --------------------------------------------------------------------------- #
# Static frontend (built React app). Mounted last so /api wins.
# --------------------------------------------------------------------------- #
_STATIC = Path(__file__).resolve().parent.parent / "static"
if _STATIC.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC), html=True), name="static")
