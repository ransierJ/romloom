"""On-the-fly thumbnail generation with an on-disk cache.

A boxart wall of ~1,700 full PNGs would be brutal to ship to a browser, so media
is resized on first request and cached under CONFIG_DIR/thumbs. Full-size images
are served straight from the source when width=0 (the detail view).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

from PIL import Image

from . import config

# Allowed thumbnail widths — clamped to a small set so the cache can't be
# ballooned by arbitrary ?w= values.
_ALLOWED_WIDTHS = (120, 200, 320, 480)


def clamp_width(w: int) -> int:
    return min(_ALLOWED_WIDTHS, key=lambda x: abs(x - w))


def _cache_path(src: Path, width: int, mtime: float) -> Path:
    h = hashlib.sha1(f"{src}|{width}|{int(mtime)}".encode()).hexdigest()
    return config.THUMB_DIR / f"{h}.jpg"


def thumbnail(src: Path, width: int) -> Optional[Path]:
    """Return a cached JPEG thumbnail of `src` at `width`, generating if needed."""
    try:
        mtime = src.stat().st_mtime
    except OSError:
        return None

    width = clamp_width(width)
    out = _cache_path(src, width, mtime)
    if out.exists():
        return out

    config.THUMB_DIR.mkdir(parents=True, exist_ok=True)
    # The source lives on a NAS mount that intermittently drops reads under the
    # load of a full boxart wall; a transient OSError is worth one quick retry.
    # Decode failures (truncated/corrupt art) are not — bail immediately.
    for attempt in range(2):
        try:
            with Image.open(src) as im:
                im = im.convert("RGB")
                if im.width > width:
                    height = round(im.height * width / im.width)
                    im = im.resize((width, height), Image.LANCZOS)
                # Write to a temp file then rename, so an interrupted save never
                # leaves a corrupt JPEG in the cache for later requests to serve.
                tmp = out.with_suffix(f".{os.getpid()}.tmp")
                im.save(tmp, "JPEG", quality=82, optimize=True)
                os.replace(tmp, out)
            return out
        except OSError:
            if attempt == 0:
                continue      # transient mount hiccup — retry once
            return None
        except Exception:
            return None       # unsupported/corrupt image — don't retry
    return None


def warm(src: Path, widths) -> int:
    """Pre-generate thumbnails at several widths from a single source read, so a
    later boxart wall serves from the local cache without touching the NAS.
    Returns how many were newly created (0 if all cached or the source is
    unreadable)."""
    try:
        mtime = src.stat().st_mtime
    except OSError:
        return 0
    todo = []
    for w in widths:
        w = clamp_width(w)
        out = _cache_path(src, w, mtime)
        if not out.exists() and not any(w == d[0] for d in todo):
            todo.append((w, out))
    if not todo:
        return 0

    config.THUMB_DIR.mkdir(parents=True, exist_ok=True)
    made = 0
    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            for w, out in todo:
                scaled = im
                if im.width > w:
                    height = round(im.height * w / im.width)
                    scaled = im.resize((w, height), Image.LANCZOS)
                tmp = out.with_suffix(f".{os.getpid()}.tmp")
                scaled.save(tmp, "JPEG", quality=82, optimize=True)
                os.replace(tmp, out)
                made += 1
    except Exception:
        return made
    return made
