"""Detect duplicate songs across multiple PSARC files.

A duplicate is the same ``(artist, title)`` appearing in more than one PSARC
file (different on-disk path). The comparison is case-insensitive and ignores
leading/trailing whitespace. Songs without artist/title metadata are never
counted as duplicates of each other.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from rsdlc.library import DlcEntry


def _norm(s: str) -> str:
    return s.casefold().strip()


def duplicate_keys(entries: list[DlcEntry]) -> set[tuple[str, str]]:
    """Return the set of ``(artist_norm, title_norm)`` pairs that appear in
    more than one distinct PSARC path.
    """
    path_by_key: dict[tuple[str, str], set[Path]] = defaultdict(set)
    for e in entries:
        if not e.artist or e.artist in ("(unknown)", "(no metadata)"):
            continue
        if not e.title or e.title == "(unknown)":
            continue
        key = (_norm(e.artist), _norm(e.title))
        path_by_key[key].add(e.path)
    return {k for k, paths in path_by_key.items() if len(paths) > 1}


def is_duplicate(entry: DlcEntry, dup_set: set[tuple[str, str]]) -> bool:
    """Whether the given entry's (artist, title) is part of a duplicate group."""
    if not entry.artist or entry.artist in ("(unknown)", "(no metadata)"):
        return False
    if not entry.title or entry.title == "(unknown)":
        return False
    return (_norm(entry.artist), _norm(entry.title)) in dup_set


__all__ = ["duplicate_keys", "is_duplicate"]
