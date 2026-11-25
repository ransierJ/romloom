"""Whole-file hashing with an on-disk cache, for ROM verification.

Cartridge ROMs (No-Intro) are single-file, so the file's CRC32/SHA1 is its
identity — compute it once and match against the DAT. Hashing is cached by
(path, size, mtime) in the hash_cache table, so an unchanged ROM is hashed only
on its first scan; later scans and incremental rescans pay nothing.

Disc images are deliberately skipped here (size cap): Redump identity is
per-track and belongs to a later pass (M4), and hashing multi-GB images over a
NAS on every scan would be punishing.
"""
from __future__ import annotations

import hashlib
import zlib
from pathlib import Path
from typing import Optional, Tuple

# Files larger than this are not hashed (disc images / big dumps — M4 territory).
MAX_HASH_BYTES = 512 * 1024 * 1024
_CHUNK = 1 << 20  # 1 MiB


def compute(path: Path) -> Tuple[str, str]:
    """Stream the file once, returning (crc32, sha1) as lowercase hex."""
    crc = 0
    sha = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
            sha.update(chunk)
    return f"{crc & 0xFFFFFFFF:08x}", sha.hexdigest()


def file_hashes(conn, path: Path, size: int, mtime: float
                ) -> Tuple[Optional[str], Optional[str]]:
    """(crc32, sha1) for a file, via hash_cache. Returns (None, None) for a file
    over the size cap or on a read error. Caches a freshly computed pair."""
    if size > MAX_HASH_BYTES:
        return None, None
    key = str(path)
    mt = int(mtime)
    row = conn.execute(
        "SELECT crc32, sha1 FROM hash_cache WHERE path=? AND size=? AND mtime=?",
        (key, size, mt),
    ).fetchone()
    if row:
        return row["crc32"], row["sha1"]
    try:
        crc, sha = compute(path)
    except OSError:
        return None, None
    conn.execute(
        "INSERT OR REPLACE INTO hash_cache(path, size, mtime, crc32, sha1) "
        "VALUES (?,?,?,?,?)",
        (key, size, mt, crc, sha),
    )
    return crc, sha
