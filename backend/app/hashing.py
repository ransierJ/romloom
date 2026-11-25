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

from . import normalize

# Files larger than this are not hashed (disc images / big dumps — M4 territory).
MAX_HASH_BYTES = 512 * 1024 * 1024
_CHUNK = 1 << 20  # 1 MiB

Quad = Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]


def _hex(data: bytes) -> Tuple[str, str]:
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08x}", hashlib.sha1(data).hexdigest()


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


def file_hashes(conn, path: Path, size: int, mtime: float,
                system_id: Optional[str] = None) -> Quad:
    """(crc32, sha1, norm_crc32, norm_sha1) for a file, via hash_cache. The
    normalized pair is the hash of the No-Intro-canonical byte form (header
    stripped / byte order fixed) for systems that need it, else None. Returns
    all-None over the size cap or on a read error."""
    if size > MAX_HASH_BYTES:
        return None, None, None, None
    key = str(path)
    mt = int(mtime)
    row = conn.execute(
        "SELECT crc32, sha1, norm_crc32, norm_sha1 FROM hash_cache "
        "WHERE path=? AND size=? AND mtime=?", (key, size, mt),
    ).fetchone()
    if row:
        return row["crc32"], row["sha1"], row["norm_crc32"], row["norm_sha1"]

    norm_crc = norm_sha = None
    try:
        if system_id and normalize.has(system_id):
            # Need the bytes in memory to derive the canonical form.
            data = path.read_bytes()
            crc, sha = _hex(data)
            canon = normalize.canonical(
                system_id, data, path.suffix.lstrip(".").lower())
            if canon is not None:
                norm_crc, norm_sha = _hex(canon)
        else:
            crc, sha = compute(path)
    except OSError:
        return None, None, None, None
    conn.execute(
        "INSERT OR REPLACE INTO hash_cache"
        "(path, size, mtime, crc32, sha1, norm_crc32, norm_sha1) VALUES (?,?,?,?,?,?,?)",
        (key, size, mt, crc, sha, norm_crc, norm_sha),
    )
    return crc, sha, norm_crc, norm_sha
