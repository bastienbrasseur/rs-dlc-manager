"""Aggregate stats over the scanned DLC library.

Pure logic, no Qt. The UI calls :func:`compute_stats` with the current
:class:`DlcEntry` list and renders the result.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from rsdlc.library import DlcEntry


@dataclass(frozen=True, slots=True)
class Stats:
    total_files: int            # unique PSARC files
    total_songs: int            # unique songs (an entry per song; packs count each)
    active_files: int
    disabled_files: int
    total_seconds: float        # sum of song lengths
    by_tuning: tuple[tuple[str, int], ...]      # sorted desc by count
    by_decade: tuple[tuple[int, int], ...]      # sorted asc by decade
    top_artists: tuple[tuple[str, int], ...]    # sorted desc by count, top 10
    songs_without_metadata: int


def _decade(year: int | None) -> int | None:
    if year is None or year <= 0:
        return None
    return (year // 10) * 10


def compute_stats(entries: list[DlcEntry], top_artists: int = 10) -> Stats:
    """Build a :class:`Stats` snapshot from the scanned entries.

    Songs sharing the same PSARC file (song packs) count individually in the
    song totals and time but the file is counted once.
    """
    unique_files: set[Path] = set()
    active_files: set[Path] = set()
    disabled_files: set[Path] = set()
    total_seconds = 0.0
    tuning_counter: Counter[str] = Counter()
    decade_counter: Counter[int] = Counter()
    artist_counter: Counter[str] = Counter()
    songs_no_meta = 0

    for e in entries:
        unique_files.add(e.path)
        (active_files if e.enabled else disabled_files).add(e.path)

        if e.artist in ("", "(unknown)", "(no metadata)"):
            songs_no_meta += 1
        else:
            artist_counter[e.artist] += 1

        if e.length_seconds:
            total_seconds += e.length_seconds

        if e.tuning_label:
            tuning_counter[e.tuning_label] += 1

        d = _decade(e.year)
        if d is not None:
            decade_counter[d] += 1

    return Stats(
        total_files=len(unique_files),
        total_songs=len(entries),
        active_files=len(active_files - disabled_files),
        disabled_files=len(disabled_files - active_files),
        total_seconds=total_seconds,
        by_tuning=tuple(sorted(tuning_counter.items(), key=lambda kv: (-kv[1], kv[0]))),
        by_decade=tuple(sorted(decade_counter.items())),
        top_artists=tuple(
            sorted(artist_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:top_artists]
        ),
        songs_without_metadata=songs_no_meta,
    )


def format_duration(seconds: float) -> str:
    """``"12 h 34 min"`` style, dropping leading zeros."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m = rem // 60
    if h:
        return f"{h} h {m:02d} min"
    return f"{m} min"


__all__ = ["Stats", "compute_stats", "format_duration"]
