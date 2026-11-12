"""Platform map: source folder name -> canonical id + per-frontend system ids.

Libraries name system folders in the long No-Intro style ("Nintendo - Super
Nintendo Entertainment System"), but ES-DE, Batocera and Retrobat each want a
short system id ("snes"). This table is the single source of truth for that
translation, used by the Phase 3 export engine.

Matching is tolerant: each platform lists several folder aliases, and lookup
also normalizes (lowercase, strip punctuation) so "SNK - Neo Geo", "Neo Geo",
"neogeo" and "NeoGeo" all resolve to the same entry. Unknown folders still work
— resolve() synthesizes a passthrough entry so a new console shows up in the
library immediately; set its export ids with the in-app mapping editor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Platform:
    canonical: str                   # ROMLoom's stable internal id
    display: str                     # human label shown in the UI
    es: Optional[str] = None         # ES-DE system id
    batocera: Optional[str] = None   # Batocera system id
    retrobat: Optional[str] = None   # Retrobat system id
    known: bool = True               # False for synthesized passthrough entries


def _p(canonical, display, es=None, batocera=None, retrobat=None, *, folders):
    """Shorthand: a Platform plus its folder aliases. retrobat defaults to
    batocera (Retrobat largely mirrors Batocera's system names)."""
    return (Platform(canonical, display, es, batocera, retrobat or batocera), folders)


# (Platform, [folder aliases]). The canonical id and display name are added as
# implicit aliases, so you only list extra spellings here. Frontend ids verified
# against ES-DE es_systems.xml, the Batocera wiki, and the Retrobat wiki.
# retrobat defaults to batocera; pass it explicitly only where they differ.
_REGISTRY: List[Tuple[Platform, List[str]]] = [
    # ---- Arcade ----
    _p("arcade", "Arcade", es="arcade", batocera="mame",
       folders=["Arcade", "MAME", "FBNeo - Arcade Games"]),
    _p("fbneo", "FinalBurn Neo", es="fbneo", batocera="fbneo",
       folders=["FBNeo", "FinalBurn Neo", "fba"]),
    _p("cps1", "Capcom CPS-1", es="cps1", batocera="cps1",
       folders=["Capcom - CPS1", "CPS1", "CPS-I"]),
    _p("cps2", "Capcom CPS-2", es="cps2", batocera="cps2",
       folders=["Capcom - CPS2", "CPS2", "CPS-II"]),
    _p("cps3", "Capcom CPS-3", es="cps3", batocera="cps3",
       folders=["Capcom - CPS3", "CPS3", "CPS-III"]),
    _p("daphne", "Daphne (LaserDisc)", es="daphne", batocera="daphne",
       folders=["Daphne", "LaserDisc"]),

    # ---- Nintendo ----
    _p("nes", "Nintendo Entertainment System", es="nes", batocera="nes",
       folders=["Nintendo - Nintendo Entertainment System", "NES", "Famicom", "Nintendo - Family Computer"]),
    _p("fds", "Famicom Disk System", es="fds", batocera="fds",
       folders=["Nintendo - Family Computer Disk System", "Famicom Disk System", "FDS"]),
    _p("snes", "Super Nintendo", es="snes", batocera="snes",
       folders=["Nintendo - Super Nintendo Entertainment System", "SNES", "Super Nintendo", "Super Famicom"]),
    _p("n64", "Nintendo 64", es="n64", batocera="n64",
       folders=["Nintendo - Nintendo 64", "N64"]),
    _p("gc", "GameCube", es="gc", batocera="gamecube",
       folders=["Nintendo - GameCube", "GameCube", "Nintendo GameCube", "ngc"]),
    _p("wii", "Wii", es="wii", batocera="wii",
       folders=["Nintendo - Wii", "Wii"]),
    _p("wiiu", "Wii U", es="wiiu", batocera="wiiu",
       folders=["Nintendo - Wii U", "Wii U"]),
    # Switch: not in Batocera's supported systems — leave batocera blank.
    _p("switch", "Nintendo Switch", es="switch", retrobat="switch",
       folders=["Nintendo - Switch", "Nintendo - Nintendo Switch", "Switch"]),
    _p("gb", "Game Boy", es="gb", batocera="gb",
       folders=["Nintendo - Game Boy", "Game Boy", "GB"]),
    _p("gbc", "Game Boy Color", es="gbc", batocera="gbc",
       folders=["Nintendo - Game Boy Color", "Game Boy Color", "GBC"]),
    _p("gba", "Game Boy Advance", es="gba", batocera="gba",
       folders=["Nintendo - Game Boy Advance", "Game Boy Advance", "GBA"]),
    _p("nds", "Nintendo DS", es="nds", batocera="nds",
       folders=["Nintendo - Nintendo DS", "Nintendo DS", "NDS"]),
    _p("3ds", "Nintendo 3DS", es="n3ds", batocera="3ds",
       folders=["Nintendo - Nintendo 3DS", "Nintendo 3DS", "3DS"]),
    _p("virtualboy", "Virtual Boy", es="virtualboy", batocera="virtualboy",
       folders=["Nintendo - Virtual Boy", "Virtual Boy", "VB"]),
    _p("gameandwatch", "Game & Watch", es="gameandwatch", batocera="gameandwatch",
       folders=["Nintendo - Game & Watch", "Nintendo - Game and Watch", "Game & Watch", "Game and Watch"]),
    _p("pokemini", "Pokemon Mini", es="pokemini", batocera="pokemini",
       folders=["Nintendo - Pokemon Mini", "Pokemon Mini", "Pokémon Mini"]),

    # ---- Sega ----
    _p("sg1000", "SG-1000", es="sg-1000", batocera="sg1000",
       folders=["Sega - SG-1000", "SG-1000", "SG1000"]),
    _p("mastersystem", "Master System", es="mastersystem", batocera="mastersystem",
       folders=["Sega - Master System - Mark III", "Sega - Master System", "Master System", "SMS"]),
    _p("genesis", "Genesis / Mega Drive", es="genesis", batocera="megadrive",
       folders=["Sega - Mega Drive - Genesis", "Sega - Genesis", "Sega - Mega Drive", "Genesis", "Mega Drive", "Megadrive"]),
    _p("segacd", "Sega CD / Mega-CD", es="segacd", batocera="megacd",
       folders=["Sega - Mega-CD - Sega CD", "Sega CD", "Mega-CD", "Mega CD"]),
    _p("sega32x", "Sega 32X", es="sega32x", batocera="sega32x",
       folders=["Sega - 32X", "32X", "Sega 32X"]),
    _p("saturn", "Sega Saturn", es="saturn", batocera="saturn",
       folders=["Sega - Saturn", "Saturn"]),
    _p("dreamcast", "Dreamcast", es="dreamcast", batocera="dreamcast",
       folders=["Sega - Dreamcast", "Dreamcast", "DC"]),
    _p("gamegear", "Game Gear", es="gamegear", batocera="gamegear",
       folders=["Sega - Game Gear", "Game Gear", "GG"]),

    # ---- Sony ----
    _p("psx", "PlayStation", es="psx", batocera="psx",
       folders=["Sony - PlayStation", "PlayStation", "PS1", "PSX"]),
    _p("ps2", "PlayStation 2", es="ps2", batocera="ps2",
       folders=["Sony - PlayStation 2", "PlayStation 2", "PS2"]),
    _p("psp", "PlayStation Portable", es="psp", batocera="psp",
       folders=["Sony - PlayStation Portable", "PlayStation Portable", "PSP"]),
    _p("psvita", "PlayStation Vita", es="psvita", batocera="psvita",
       folders=["Sony - PlayStation Vita", "PS Vita", "Vita"]),

    # ---- Microsoft ----
    _p("xbox", "Xbox", es="xbox", batocera="xbox",
       folders=["Microsoft - Xbox", "Xbox"]),

    # ---- NEC ----
    _p("tg16", "PC Engine / TurboGrafx-16", es="tg16", batocera="pcengine",
       folders=["NEC - PC Engine - TurboGrafx-16", "PC Engine", "TurboGrafx-16", "TG16", "PCEngine"]),
    _p("tg16cd", "PC Engine CD / TurboGrafx-CD", es="tgcd", batocera="pcenginecd",
       folders=["NEC - PC Engine CD - TurboGrafx-CD", "PC Engine CD", "TurboGrafx-CD"]),
    _p("supergrafx", "SuperGrafx", es="supergrafx", batocera="supergrafx",
       folders=["NEC - PC Engine SuperGrafx", "SuperGrafx"]),
    _p("pcfx", "PC-FX", es="pcfx", batocera="pcfx",
       folders=["NEC - PC-FX", "PC-FX"]),
    _p("pc88", "PC-88", es="pc88", batocera="pc88",
       folders=["NEC - PC-8801", "PC-88", "PC-8800"]),
    _p("pc98", "PC-98", es="pc98", batocera="pc98",
       folders=["NEC - PC-9801", "PC-98", "PC-9800"]),

    # ---- Atari ----
    _p("atari2600", "Atari 2600", es="atari2600", batocera="atari2600",
       folders=["Atari - 2600", "Atari 2600", "2600", "VCS"]),
    _p("atari5200", "Atari 5200", es="atari5200", batocera="atari5200",
       folders=["Atari - 5200", "Atari 5200", "5200"]),
    _p("atari7800", "Atari 7800", es="atari7800", batocera="atari7800",
       folders=["Atari - 7800", "Atari 7800", "7800"]),
    _p("atarilynx", "Atari Lynx", es="atarilynx", batocera="lynx",
       folders=["Atari - Lynx", "Lynx", "Atari Lynx"]),
    _p("atarijaguar", "Atari Jaguar", es="atarijaguar", batocera="jaguar",
       folders=["Atari - Jaguar", "Jaguar", "Atari Jaguar"]),
    _p("atarijaguarcd", "Atari Jaguar CD", es="atarijaguarcd", batocera="jaguarcd",
       folders=["Atari - Jaguar CD", "Jaguar CD"]),
    _p("atarist", "Atari ST", es="atarist", batocera="atarist",
       folders=["Atari - ST", "Atari ST"]),

    # ---- SNK ----
    _p("neogeo", "Neo Geo", es="neogeo", batocera="neogeo",
       folders=["SNK - Neo Geo", "Neo Geo", "NeoGeo", "neo-geo"]),
    _p("neogeocd", "Neo Geo CD", es="neogeocd", batocera="neogeocd",
       folders=["SNK - Neo Geo CD", "Neo Geo CD", "NeoGeo CD"]),
    _p("ngp", "Neo Geo Pocket", es="ngp", batocera="ngp",
       folders=["SNK - Neo Geo Pocket", "Neo Geo Pocket"]),
    _p("ngpc", "Neo Geo Pocket Color", es="ngpc", batocera="ngpc",
       folders=["SNK - Neo Geo Pocket Color", "Neo Geo Pocket Color", "NGPC"]),

    # ---- Bandai ----
    _p("wonderswan", "WonderSwan", es="wonderswan", batocera="wswan",
       folders=["Bandai - WonderSwan", "WonderSwan"]),
    _p("wonderswancolor", "WonderSwan Color", es="wonderswancolor", batocera="wswanc",
       folders=["Bandai - WonderSwan Color", "WonderSwan Color"]),

    # ---- Other consoles ----
    _p("3do", "3DO", es="3do", batocera="3do",
       folders=["The 3DO Company - 3DO", "Panasonic - 3DO", "3DO"]),
    _p("colecovision", "ColecoVision", es="colecovision", batocera="colecovision",
       folders=["Coleco - ColecoVision", "ColecoVision"]),
    _p("intellivision", "Intellivision", es="intellivision", batocera="intellivision",
       folders=["Mattel - Intellivision", "Intellivision"]),
    _p("vectrex", "Vectrex", es="vectrex", batocera="vectrex",
       folders=["GCE - Vectrex", "Vectrex"]),
    _p("odyssey2", "Magnavox Odyssey 2", es="odyssey2", batocera="odyssey2",
       folders=["Magnavox - Odyssey2", "Odyssey2", "Odyssey 2", "Videopac", "o2em"]),
    _p("channelf", "Fairchild Channel F", es="channelf", batocera="channelf",
       folders=["Fairchild - Channel F", "Channel F"]),
    _p("cdi", "Philips CD-i", es="cdimono1", batocera="cdi",
       folders=["Philips - CD-i", "CD-i", "CDi"]),

    # ---- Computers ----
    _p("dos", "MS-DOS", es="dos", batocera="dos",
       folders=["DOS", "MS-DOS", "PC - DOS", "dosbox"]),
    # ES-DE uses a single "amiga" dir; Batocera/Retrobat split by model —
    # default to the 500-class, override to amiga1200 per collection if needed.
    _p("amiga", "Commodore Amiga", es="amiga", batocera="amiga500",
       folders=["Commodore - Amiga", "Amiga", "Amiga 500"]),
    _p("amigacd32", "Amiga CD32", es="amigacd32", batocera="amigacd32",
       folders=["Commodore - Amiga CD32", "Amiga CD32", "CD32"]),
    _p("c64", "Commodore 64", es="c64", batocera="c64",
       folders=["Commodore - 64", "Commodore 64", "C64"]),
    _p("c128", "Commodore 128", batocera="c128",
       folders=["Commodore - 128", "Commodore 128", "C128"]),
    _p("vic20", "VIC-20", es="vic20", batocera="c20",
       folders=["Commodore - VIC-20", "VIC-20", "VC-20"]),
    _p("pet", "Commodore PET", es="pet", batocera="pet",
       folders=["Commodore - PET", "Commodore PET", "PET"]),
    _p("amstradcpc", "Amstrad CPC", es="amstradcpc", batocera="amstradcpc",
       folders=["Amstrad - CPC", "Amstrad CPC", "CPC"]),
    _p("zxspectrum", "ZX Spectrum", es="zxspectrum", batocera="zxspectrum",
       folders=["Sinclair - ZX Spectrum", "ZX Spectrum", "Spectrum", "speccy"]),
    _p("msx", "MSX", es="msx", batocera="msx1",
       folders=["Microsoft - MSX", "MSX"]),
    _p("msx2", "MSX2", es="msx2", batocera="msx2",
       folders=["Microsoft - MSX2", "MSX2"]),
    _p("x68000", "Sharp X68000", es="x68000", batocera="x68000",
       folders=["Sharp - X68000", "X68000"]),
    _p("fmtowns", "FM Towns", es="fmtowns", batocera="fmtowns",
       folders=["Fujitsu - FM Towns", "FM Towns", "Towns Marty"]),
    _p("apple2", "Apple II", es="apple2", batocera="apple2",
       folders=["Apple - II", "Apple II"]),

    # ---- Handhelds / misc ----
    _p("gp32", "GP32", batocera="gp32",
       folders=["GamePark - GP32", "GP32", "Game Park 32"]),

    # ---- Utility ----
    _p("scummvm", "ScummVM", es="scummvm", batocera="scummvm",
       folders=["ScummVM"]),
    _p("ports", "Ports", es="ports", batocera="ports",
       folders=["Ports", "PC Ports"]),
]


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _slug(name: str) -> str:
    return _norm(name) or "system"


# Build exact + normalized lookup indexes from the registry.
_BY_FOLDER: Dict[str, Platform] = {}
_BY_NORM: Dict[str, Platform] = {}
for _plat, _folders in _REGISTRY:
    for _alias in (*_folders, _plat.canonical, _plat.display):
        _BY_FOLDER.setdefault(_alias, _plat)
        _BY_NORM.setdefault(_norm(_alias), _plat)


def resolve(folder_name: str) -> Platform:
    """Map a source folder name to a Platform: exact alias, then normalized
    (case/punctuation-insensitive), else a synthesized passthrough entry."""
    hit = _BY_FOLDER.get(folder_name) or _BY_NORM.get(_norm(folder_name))
    if hit:
        return hit
    return Platform(canonical=_slug(folder_name), display=folder_name, known=False)
