# Spec: Hash identity + arcade filtering

Status: approved 2026-11-25. Building in milestones M1→M4 (one build, phased).

## Goal
Make ROMLoom's library *identity-aware*, delivering two user-facing wins:
1. **Arcade**: hide adult/mature sets, hide non-US/World sets, filter by category,
   plus an arcade Missing view.
2. **Cartridge/disc**: verify each ROM against the No-Intro/Redump hash and flag
   mismatches/renames.

They ship together because they share one new spine: a reference-catalog +
canonical-identity layer feeding the existing `_games_where` filters and
`missing_games` endpoint.

## Identity is routed by system class
Hashing a MAME `.zip` is meaningless — a romset's identity is its set name, and
the DAT lists per-internal-ROM CRCs, not a whole-file hash.

| Class | Systems | Identity key | Reference source |
|---|---|---|---|
| Arcade | arcade, mame, fbneo, neogeo, cps1/2/3 | set short name (= `games.stem`) | MAME DAT + `catver.ini` |
| Cartridge | nes, snes, gb/gba, genesis… | whole-file CRC32/SHA1 | No-Intro DAT (already wired) |
| Disc | psx, saturn, segacd… | per-track hashes | Redump DAT (deferred → M4) |

Arcade does no hashing (clean exact name-join). Hashing applies to cart/disc.

## Data model (`db.py`)
Add to `games` (nullable; populated per class):
```sql
crc32     TEXT,
sha1      TEXT,
dat_match TEXT,     -- canonical name if matched
verified  INTEGER,  -- 1 matched, 0 present-but-no-match, NULL n/a
is_mature INTEGER DEFAULT 0,
mame_cat  TEXT
```
New persistent tables:
```sql
hash_cache(path TEXT PRIMARY KEY, size INT, mtime INT, crc32 TEXT, sha1 TEXT);
arcade_catalog(set_name TEXT PRIMARY KEY, parent TEXT, is_clone INT,
               category TEXT, is_mature INT, region TEXT, description TEXT);
```

## `hashing.py` (cartridge identity — M2)
- `file_hashes(path,size,mtime)` with `hash_cache` lookup; hash only on miss.
- Streamed 1 MB chunks; CRC32 via `zlib.crc32`, SHA1 via `hashlib`.
- Skip files over ~512 MB in v1 (disc images). Cache makes rescans free.
- Headered systems (NES/SNES) hash headerless in No-Intro → come back
  `verified=0`, never wrongly matched. Header-stripping is M4.

## `mame.py` (arcade identity — M1)
- Parse `catver.ini` → `{set: (category, is_mature)}`; mature = `* Mature *` marker.
- Parse MAME reference DAT → set, `cloneof`→parent, description, region token
  from the description (`(USA)`/`(World)`/`(Japan)`/`(Euro)`).
- Source (locked): auto-fetch from a pinned mirror into `CONFIG_DIR/mame/`, AND
  honor user-dropped `catver.ini` / `*.dat` in `CONFIG_DIR/mame/` (reliable
  fallback). Populates `arcade_catalog`.

## Scan integration (`scanner.py`)
After each `RomEntry`, before the games INSERT:
- Arcade → look up `rom.stem` in `arcade_catalog`; set `mame_cat/is_mature/region/
  dat_match`. No hashing. Works even for unscraped sets.
- Cartridge → `hashing.file_hashes`; match to No-Intro; set `crc32/sha1/dat_match/
  verified`.
Incremental scan unaffected (skipped systems keep rows; rescanned pull from cache).

## API + filters (`main.py`, `settings.py`)
- `_games_where` gains `hide_mature` → `COALESCE(g.is_mature,0)=0`, `category` →
  `g.mame_cat = ?`; reuse `region` for US/World-only (`USA,World,US`).
- Settings defaults (set once): `arcade_hide_mature`, `arcade_usa_only`.
- Arcade Missing (M3): diff library set names against `arcade_catalog` filtered to
  parent-only + non-mature + US/World + minus junk categories (Mahjong / Casino /
  gambling / electromechanical), dropped by default with a toggle.

## Frontend
- Arcade toolbar: `Hide adult`, `US/World only` toggles + Category dropdown
  (arcade systems only).
- GameDrawer: cart/disc → `Verified ✓ (No-Intro)` / `Unverified` + CRC/SHA1;
  arcade → category · mature · parent.
- SettingsDialog: the two arcade defaults + a MAME-data status line.
- MissingView: enabled for arcade once `arcade_catalog` is populated.

## Milestones
- **M1 — Arcade filters** ✅ done (`d504d10`): `mame.py` + catver/languages ingest
  + `is_mature`/`mame_cat`/`mame_lang` + three filters + settings defaults.
- **M2 — Hashing + verification** ✅ done: `hashing.py` + `hash_cache` + cartridge
  CRC32/SHA1 at scan + No-Intro match + drawer badge + per-system Verify.
  - Scan hashes cartridge ROMs (cached) and matches **if the DAT is already
    cached**; it never force-fetches DATs mid-scan.
  - `POST /api/systems/{id}/verify` fetches the DAT on demand, builds the hash
    index, and fills verified/dat_match from the stored hashes.
  - Known limit: headered systems (NES/SNES) and byte-swapped N64 hash
    differently than No-Intro → they read `verified=0` (never wrongly matched).
    Header/byteswap normalization is M4.
- **M3 — Arcade Missing view** ✅ done: on-demand ARCADE.dat (AntoPISA/MAME_Dats,
  Logiqx XML, ~34 MB) parsed for set list + cloneof + title + region; diffed
  against the library, filtered to parent sets / US-World / non-adult / non-junk
  with toggles to include each. A game family counts as US-available if any set
  in it is US (so Pac-Man, whose US set is a clone of JP "puckman", is included).
  - Source note: the real-region upgrade lives **in the Missing view only**; the
    library-view "US/World" filter stays on the M1 language proxy (keeping M3
    free of schema/scanner changes). Wiring DAT region into the live filter is a
    small follow-up if wanted.
- **M4 — optional tail** (in progress):
  - ✅ **Header/byte-order normalization** done: `normalize.py` canonicalizes
    SNES (512 copier header), NES (iNES), N64 (v64/n64→z64), Atari Lynx (.lnx),
    Atari 7800 (.a78), and Genesis (.smd de-interleave). The scanner stores a
    `norm_crc32`/`norm_sha1` beside the raw hash (cached), and verification
    matches on **either** — so these systems now verify regardless of whether the
    No-Intro set is headered or headerless.
  - ✅ **DAT region into the live arcade library filter** done: scan stores
    `games.mame_region` ('US'/'NONUS') from ARCADE.dat when downloaded;
    `mame.apply_region()` backfills it the moment the dat is fetched (no rescan);
    the library US/World filter prefers real region and falls back to the
    languages.ini proxy only where region is unknown.
  - ☐ **Disc/Redump multi-track hashing** — not started (heavy over NAS; low ROI);
    documented non-goal for now.

## Locked decisions
1. MAME data: auto-fetch from pinned mirror AND honor user-dropped files in
   `/config/mame/`.
2. Disc hashing deferred to M4; cart-only hashing in v1.
3. Missing drops Mahjong/Casino/gambling/electromechanical by default, with a
   toggle to show them.
