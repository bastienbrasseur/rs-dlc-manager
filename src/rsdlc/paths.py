"""Best-effort autodetection of the Rocksmith 2014 install folder."""

from __future__ import annotations

import os
from pathlib import Path

_STEAM_INSTALL = "steamapps/common/Rocksmith2014"


def _candidate_roots() -> list[Path]:
    home = Path.home()
    candidates: list[Path] = []

    # Windows — both 32- and 64-bit Steam locations + custom drive letters.
    for env_var in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432"):
        v = os.environ.get(env_var)
        if v:
            candidates.append(Path(v) / "Steam" / _STEAM_INSTALL)
    for drive in ("C:", "D:", "E:", "F:"):
        candidates.append(Path(drive + "/Program Files (x86)/Steam") / _STEAM_INSTALL)
        candidates.append(Path(drive + "/Program Files/Steam") / _STEAM_INSTALL)
        candidates.append(Path(drive + "/SteamLibrary") / _STEAM_INSTALL)
        candidates.append(Path(drive + "/Steam") / _STEAM_INSTALL)
        candidates.append(Path(drive + "/Games/Steam") / _STEAM_INSTALL)

    # Linux + Proton.
    candidates.append(home / ".steam/steam" / _STEAM_INSTALL)
    candidates.append(home / ".local/share/Steam" / _STEAM_INSTALL)
    candidates.append(home / ".var/app/com.valvesoftware.Steam/data/Steam" / _STEAM_INSTALL)  # flatpak

    # macOS (theoretical).
    candidates.append(home / "Library/Application Support/Steam" / _STEAM_INSTALL)

    return candidates


def autodetect_rocksmith_root() -> Path | None:
    """Return the first candidate folder that exists and contains a ``dlc/`` subdir, or None."""
    for c in _candidate_roots():
        try:
            if c.is_dir() and (c / "dlc").is_dir():
                return c
        except OSError:
            continue
    # Looser fallback: any candidate that exists, even without dlc/.
    for c in _candidate_roots():
        try:
            if c.is_dir():
                return c
        except OSError:
            continue
    return None


def looks_like_rocksmith_root(path: Path) -> bool:
    """True if the folder looks like a Rocksmith2014 install (contains dlc/ or a known exe)."""
    if not path.is_dir():
        return False
    if (path / "dlc").is_dir():
        return True
    for marker in ("Rocksmith2014.exe", "Rocksmith.exe", "Rocksmith2014.app"):
        if (path / marker).exists():
            return True
    return False


__all__ = ["autodetect_rocksmith_root", "looks_like_rocksmith_root"]
