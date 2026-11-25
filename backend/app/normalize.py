"""Canonicalize ROM bytes to the form No-Intro hashes, for verification (M4).

A whole-file hash misses a match whenever the on-disk file carries a copier
header or a different byte order than the No-Intro reference. For the systems
below we derive the *canonical* byte stream and hash that too; the verifier then
accepts a match on either the raw or the normalized hash, so we don't need to
know which convention a given DAT uses.

Only well-defined, reversible transforms are implemented — the common sources of
false negatives: SNES copier headers, N64 byte order, iNES headers, Atari
Lynx/.a78 headers, and interleaved Genesis .smd dumps.
"""
from __future__ import annotations

from array import array
from typing import Optional

# Systems where a normalized form is worth computing.
NORMALIZE_SYSTEMS = {"snes", "nes", "n64", "atarilynx", "atari7800",
                     "genesis", "megadrive"}

_N64_Z64 = b"\x80\x37\x12\x40"   # native big-endian (No-Intro canonical)
_N64_V64 = b"\x37\x80\x40\x12"   # byte-swapped (16-bit)
_N64_N64 = b"\x40\x12\x37\x80"   # little-endian (32-bit word-swapped)


def has(system_id: str) -> bool:
    return system_id in NORMALIZE_SYSTEMS


def _snes(data: bytes) -> Optional[bytes]:
    # A 512-byte copier header makes the size 512 past a 1 KiB boundary.
    if len(data) % 1024 == 512:
        return data[512:]
    return None


def _nes(data: bytes) -> Optional[bytes]:
    if data[:4] == b"NES\x1a":          # iNES header -> headerless form
        return data[16:]
    return None


def _n64(data: bytes) -> Optional[bytes]:
    sig = data[:4]
    if sig == _N64_V64:                 # 16-bit byteswap -> z64
        a = array("H")
        a.frombytes(data if len(data) % 2 == 0 else data[:-1])
        a.byteswap()
        return a.tobytes()
    if sig == _N64_N64:                 # 32-bit byteswap -> z64
        a = array("I")
        a.frombytes(data if len(data) % 4 == 0 else data[: len(data) - len(data) % 4])
        a.byteswap()
        return a.tobytes()
    return None                          # already z64, or unknown


def _lynx(data: bytes) -> Optional[bytes]:
    if data[:4] == b"LYNX":
        return data[64:]
    return None


def _a78(data: bytes) -> Optional[bytes]:
    if len(data) > 128 and data[1:10] == b"ATARI7800":
        return data[128:]
    return None


def _smd(data: bytes) -> Optional[bytes]:
    # Interleaved Super Magic Drive dump: 512-byte header + 16 KiB blocks whose
    # first half holds the odd bytes and second half the even bytes.
    if len(data) % 16384 != 512:
        return None
    body = data[512:]
    out = bytearray(len(body))
    for i in range(0, len(body), 16384):
        block = body[i:i + 16384]
        out[i + 1:i + len(block):2] = block[:8192]
        out[i:i + len(block):2] = block[8192:16384]
    return bytes(out)


def canonical(system_id: str, data: bytes, ext: str) -> Optional[bytes]:
    """The No-Intro-canonical byte stream for this ROM, or None if no transform
    applies (the raw file is already canonical)."""
    if system_id == "snes":
        return _snes(data)
    if system_id == "nes":
        return _nes(data)
    if system_id == "n64":
        return _n64(data)
    if system_id == "atarilynx":
        return _lynx(data)
    if system_id == "atari7800":
        return _a78(data)
    if system_id in ("genesis", "megadrive") and ext == "smd":
        return _smd(data)
    return None
