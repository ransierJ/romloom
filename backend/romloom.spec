# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the ROMLoom Windows bundle.

Build on Windows, from the backend/ directory:

    pip install -r requirements.txt pyinstaller
    pyinstaller romloom.spec

Output: dist/romloom.exe (a single self-contained executable). The React
frontend in static/ is embedded. Skyscraper is NOT bundled — install it
natively and point ROMLOOM_SKYSCRAPER_BIN at it, or put it on PATH. The keyless
scrapers (GameTDB / libretro / IGDB) need nothing extra.
"""
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH is the directory holding this spec (backend/). Resolve everything
# against it so the build works regardless of the current directory.
HERE = SPECPATH

datas = [(os.path.join(HERE, "static"), "static")]
binaries = []
hiddenimports = []

# Packages whose modules/data PyInstaller's static analysis can miss: compiled
# cores (pydantic_core), lazily imported plugins (PIL), and auto-selected impls.
for pkg in ("uvicorn", "fastapi", "starlette", "pydantic", "pydantic_core",
            "anyio", "PIL"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Our own package, plus uvicorn's dynamically chosen loop/protocol backends and
# starlette's optional multipart form parser (used by art upload).
hiddenimports += collect_submodules("app")
hiddenimports += [
    "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on", "uvicorn.lifespan.off",
    "multipart", "python_multipart",
]

_icon = os.path.join(HERE, "romloom.ico")

a = Analysis(
    [os.path.join(HERE, "desktop.py")],
    pathex=[HERE],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide2"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="romloom",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    icon=_icon if os.path.exists(_icon) else None,
)
