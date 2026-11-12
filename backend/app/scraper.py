"""Built-in scraping via the bundled Skyscraper (ScreenScraper) binary.

Skyscraper is two-phase: `gather` queries ScreenScraper and caches resources per
platform under $HOME/.skyscraper/cache/<platform>/, then `generate` renders that
cache into on-disk media + a gamelist.xml. We run both into the library's media/
folders using a raw artwork.xml (one file per media type, no compositing), so
ROMForge and external frontends read the output directly. HOME is /config in the
container, so config + cache persist on the volume.

No API keys needed here: Skyscraper carries its own ScreenScraper dev credential;
the user only supplies their personal ScreenScraper login (stored in settings).
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from . import config


class ScrapeCancelled(Exception):
    """Raised inside scrape() when the job was stopped by the user."""


class ScrapeControl:
    """Handle on the running Skyscraper subprocess so the API can stop/pause/
    resume it. Signals the whole process group (Skyscraper may spawn children)."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._paused = threading.Event()   # set => paused (for in-process backends)
        self.cancelled = False

    def wait_if_paused(self) -> None:
        """Block while paused (used by the in-process GameTDB backend; the
        Skyscraper subprocess is frozen by SIGSTOP instead)."""
        import time as _t
        while self._paused.is_set() and not self.cancelled:
            _t.sleep(0.15)

    def _attach(self, proc: Optional[subprocess.Popen]) -> None:
        with self._lock:
            self._proc = proc

    def _signal(self, sig: int) -> None:
        with self._lock:
            p = self._proc
            if p and p.poll() is None:
                try:
                    os.killpg(os.getpgid(p.pid), sig)
                except (ProcessLookupError, OSError):
                    pass

    def stop(self) -> None:
        self.cancelled = True
        self._signal(signal.SIGCONT)   # in case it was paused
        self._signal(signal.SIGTERM)

    def pause(self) -> None:
        self._paused.set()
        self._signal(signal.SIGSTOP)

    def resume(self) -> None:
        self._paused.clear()
        self._signal(signal.SIGCONT)

BINARY = "/usr/local/bin/Skyscraper"

# ROMForge canonical id -> Skyscraper platform id, only where they differ. Ids
# not listed fall through to the canonical id if Skyscraper knows it (see _VALID).
_OVERRIDES = {
    "amigacd32": "cd32", "colecovision": "coleco", "cps1": "arcade",
    "cps2": "arcade", "cps3": "arcade", "dos": "pc", "fbneo": "fba",
    "genesis": "megadrive", "odyssey2": "videopac", "supergrafx": "pcengine",
    "tg16": "pcengine", "tg16cd": "pcenginecd",
}

# Platform ids Skyscraper 3.22 accepts (from its peas.json).
_VALID = {
    "3do", "3ds", "amiga", "amstradcpc", "apple2", "arcade", "atari2600",
    "atari5200", "atari7800", "atarijaguar", "atarijaguarcd", "atarilynx",
    "atarist", "c128", "c64", "cd32", "cdi", "channelf", "coleco", "daphne",
    "dreamcast", "fba", "fds", "fmtowns", "gameandwatch", "gamegear", "gb",
    "gba", "gbc", "gc", "intellivision", "mame", "mastersystem", "megadrive",
    "msx", "msx2", "n64", "nds", "neogeo", "neogeocd", "nes", "ngp", "ngpc",
    "pc", "pc88", "pc98", "pcengine", "pcenginecd", "pcfx", "pokemini", "ports",
    "ps2", "ps3", "psp", "psvita", "psx", "saturn", "scummvm", "sega32x",
    "segacd", "snes", "switch", "vectrex", "vic20", "videopac", "virtualboy",
    "wii", "wiiu", "wonderswan", "wonderswancolor", "x1", "x68000", "xbox",
    "zxspectrum",
}

_SKY_HOME = Path(config.CONFIG_DIR) / ".skyscraper"   # HOME=/config -> ~/.skyscraper

# Raw per-type output (no compositing): each resource copied straight through to
# its media folder. Videos come via the `videos` flag, metadata via gamelist.xml.
_ARTWORK_XML = """<?xml version="1.0" encoding="UTF-8"?>
<artwork>
  <output type="screenshot" resource="screenshot"/>
  <output type="cover" resource="cover"/>
  <output type="wheel" resource="wheel"/>
  <output type="marquee" resource="marquee"/>
</artwork>
"""

_PROGRESS_RE = re.compile(r"#(\d+)/(\d+)")


def available() -> bool:
    return Path(BINARY).is_file()


def platform(system_id: str) -> Optional[str]:
    p = _OVERRIDES.get(system_id, system_id)
    return p if p in _VALID else None


# Region fallback order; the user's preferred region is moved to the front.
# Skyscraper's own default is eu-first, which is why US games came back with PAL
# names (Star Fox -> Starwing) and art (Sega CD -> Mega CD).
_REGION_BASE = ["us", "wor", "eu", "jp", "ss", "uk"]


def _region_prios(region: str) -> str:
    region = (region or "us").lower()
    return ",".join([region] + [r for r in _REGION_BASE if r != region])


def _write_config(user: str, password: str, region: str, lang: str) -> None:
    _SKY_HOME.mkdir(parents=True, exist_ok=True)
    creds = f"{user}:{password}" if user else ""
    region = (region or "us").lower()
    (_SKY_HOME / "config.ini").write_text(
        "[main]\n"
        'videos="true"\n'
        'cacheFolder="' + str(_SKY_HOME / "cache") + '"\n'
        f'region="{region}"\n'
        f'regionPrios="{_region_prios(region)}"\n'
        f'lang="{lang or "en"}"\n'
        "\n"
        "[screenscraper]\n"
        f'userCreds="{creds}"\n',
        encoding="utf-8",
    )
    (_SKY_HOME / "artwork.xml").write_text(_ARTWORK_XML, encoding="utf-8")


def configure(user: str, password: str, region: str = "us", lang: str = "en") -> None:
    """Lay down config.ini (login + region/language preference) + a raw
    artwork.xml. Triggers Skyscraper's first-run init if needed."""
    if not _SKY_HOME.exists():
        # Let Skyscraper populate its data dir (peas.json, aliasMap, …) once.
        try:
            subprocess.run([BINARY, "--version"], capture_output=True, timeout=30)
        except Exception:
            pass
    _write_config(user, password, region, lang)


def _run(args: list, on_line: Optional[Callable[[str], None]],
         control: "Optional[ScrapeControl]" = None) -> tuple:
    """Run Skyscraper, streaming stdout to on_line. Returns (returncode, tail)
    where tail is the last ~25 output lines (for surfacing real errors). Runs in
    its own session so the control object can signal the whole process group."""
    tail: deque = deque(maxlen=25)
    proc = subprocess.Popen(
        [BINARY, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, start_new_session=True,
    )
    if control:
        control._attach(proc)
    for line in proc.stdout:
        line = line.rstrip("\n")
        tail.append(line)
        if on_line:
            on_line(line)
    proc.wait()
    if control:
        control._attach(None)
    return proc.returncode, "\n".join(tail)


def _error_reason(tail: str) -> str:
    """Pull the most informative line(s) out of Skyscraper's output tail."""
    lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if re.search(r"error|fail|unknown option|no (such|files|credentials)|closed|quota|forbidden",
                     ln, re.IGNORECASE):
            return ln
    return lines[-1] if lines else "no output"


def scrape(system_id: str, folder: str, filenames=None,
           on_progress: Optional[Callable[[int, int, str], None]] = None,
           control: "Optional[ScrapeControl]" = None) -> dict:
    """Gather from ScreenScraper then generate media + gamelist into the
    library's media/ folder. `filenames` limits to specific ROMs (single/bulk
    rescrape, via --includefrom); None scrapes the whole system. Raises
    ScrapeCancelled if control.stop() was called."""
    plat = platform(system_id)
    if plat is None:
        raise ValueError(f"Skyscraper has no platform for '{system_id}'")

    rom_dir = str(config.ROMS_ROOT / folder)
    media_out = str(config.ROMS_ROOT / folder / "media")

    # Scope to specific ROMs by writing an include-list (one filename per line);
    # --includefrom matches exact names, so parentheses/brackets are literal.
    inc: list = []
    inc_path = None
    if filenames:
        _SKY_HOME.mkdir(parents=True, exist_ok=True)
        inc_path = _SKY_HOME / "romforge-includefrom.txt"
        inc_path.write_text("\n".join(filenames) + "\n", encoding="utf-8")
        inc = ["--includefrom", str(inc_path)]

    def emit(line: str, phase: str):
        if on_progress:
            m = _PROGRESS_RE.search(line)
            if m:
                on_progress(int(m.group(1)), int(m.group(2)), phase)

    def _check_cancel():
        if control and control.cancelled:
            raise ScrapeCancelled()

    try:
        _check_cancel()
        # --refresh re-queries ScreenScraper instead of reusing the cache, so the
        # current region/language settings actually apply (a cached game would
        # otherwise keep its old-region name/art, e.g. PAL "Starwing").
        gather = ["-p", plat, "-s", "screenscraper", "-i", rom_dir,
                  "--flags", "unattend,videos", "--refresh", *inc]
        rc, tail = _run(gather, lambda ln: emit(ln, "gather"), control)
        _check_cancel()
        if rc != 0:
            raise RuntimeError(f"Skyscraper gather failed: {_error_reason(tail)}")
        gen = ["-p", plat, "-i", rom_dir, "-g", rom_dir, "-o", media_out,
               "-f", "emulationstation", "--flags", "unattend,videos", *inc]
        rc, tail = _run(gen, lambda ln: emit(ln, "generate"), control)
        _check_cancel()
        if rc != 0:
            raise RuntimeError(f"Skyscraper generate failed: {_error_reason(tail)}")
    finally:
        if inc_path:
            try:
                inc_path.unlink()
            except OSError:
                pass

    return {"platform": plat, "count": len(filenames) if filenames else None}
