"""Decode the per-song JSON manifest (.hsan) embedded in a Rocksmith DLC.

A single .hsan describes one *song* (or pack of songs) with one Entry per
arrangement (Lead, Rhythm, Bass, Vocals, Combo, Showlights). We collapse those
arrangements into a single :class:`Song` keyed on (artist, title, album) — the
GUI shows one row per song, not per arrangement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from rsdlc.psarc import FileNotFoundInPsarc, PsarcArchive, PsarcError


# ---------------------------------------------------------------------------
# Tuning naming
# ---------------------------------------------------------------------------

# Standard E (low to high): E2 A2 D3 G3 B3 E4.
# Tuning fields in hsan are semitone offsets per string (string0 = low E).
_KNOWN_TUNINGS: dict[tuple[int, int, int, int, int, int], str] = {
    (0, 0, 0, 0, 0, 0): "E Standard",
    (-1, -1, -1, -1, -1, -1): "Eb Standard",
    (-2, -2, -2, -2, -2, -2): "D Standard",
    (-3, -3, -3, -3, -3, -3): "C# Standard",
    (-4, -4, -4, -4, -4, -4): "C Standard",
    (-2, 0, 0, 0, 0, 0): "Drop D",
    (-3, -1, -1, -1, -1, -1): "Drop Db",
    (-4, -2, -2, -2, -2, -2): "Drop C",
    (-5, -3, -3, -3, -3, -3): "Drop B",
    (-6, -4, -4, -4, -4, -4): "Drop Bb",
    (-7, -5, -5, -5, -5, -5): "Drop A",
    (0, 0, 0, 0, 0, -2): "DADGAD-ish",  # very rare
}


def tuning_name(offsets: tuple[int, ...]) -> str:
    """Human-readable tuning label, or a fallback like ``(-2,-2,-2,-2,-2,-2)``."""
    if len(offsets) != 6:
        return f"({','.join(str(x) for x in offsets)})"
    key = (offsets[0], offsets[1], offsets[2], offsets[3], offsets[4], offsets[5])
    if key in _KNOWN_TUNINGS:
        return _KNOWN_TUNINGS[key]
    return f"({offsets[0]:+d}, {offsets[1]:+d}, {offsets[2]:+d}, {offsets[3]:+d}, {offsets[4]:+d}, {offsets[5]:+d})"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Arrangement:
    name: str                       # "Lead" / "Rhythm" / "Bass" / "Vocals" / ...
    tuning: tuple[int, ...] | None  # 6 semitone offsets (low to high), or None for non-instrument


@dataclass(frozen=True, slots=True)
class Song:
    title: str
    artist: str
    album: str
    year: int | None
    length_seconds: float | None
    arrangements: tuple[Arrangement, ...]

    @property
    def arrangement_label(self) -> str:
        order = {"Lead": 0, "Rhythm": 1, "Combo": 2, "Bass": 3, "Vocals": 4, "ShowLights": 5}
        names = sorted({a.name for a in self.arrangements},
                       key=lambda n: (order.get(n, 99), n))
        return ", ".join(names)

    @property
    def primary_tuning_label(self) -> str:
        for a in self.arrangements:
            if a.tuning is not None and a.name in ("Lead", "Rhythm", "Combo", "Bass"):
                return tuning_name(a.tuning)
        for a in self.arrangements:
            if a.tuning is not None:
                return tuning_name(a.tuning)
        return ""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class ManifestError(Exception):
    """The .hsan was missing or could not be decoded into a Song."""


def _tuning_tuple(raw: object) -> tuple[int, ...] | None:
    if not isinstance(raw, dict):
        return None
    out: list[int] = []
    for i in range(6):
        v = raw.get(f"string{i}")
        if not isinstance(v, int):
            return None
        out.append(v)
    return tuple(out)


def parse_hsan(data: bytes) -> list[Song]:
    """Parse the raw .hsan bytes into a list of Song.

    Most DLC contain a single Song. SongPacks contain several; grouping is by
    (ArtistName, SongName, AlbumName).
    """
    doc = json.loads(data)
    entries = doc.get("Entries")
    if not isinstance(entries, dict):
        raise ManifestError("hsan has no Entries dict")

    # Group arrangements by song identity.
    grouped: dict[tuple[str, str, str], dict[str, object]] = {}
    for raw_entry in entries.values():
        if not isinstance(raw_entry, dict):
            continue
        attrs = raw_entry.get("Attributes")
        if not isinstance(attrs, dict):
            continue
        title = attrs.get("SongName")
        artist = attrs.get("ArtistName")
        # Some hsan have one entry per arrangement; only the first carries
        # SongName/ArtistName. The vocals entry typically lacks them. We use a
        # placeholder key and merge after.
        album = attrs.get("AlbumName") or ""
        if not isinstance(title, str) or not isinstance(artist, str):
            # Stash arrangement under a sentinel; merged below.
            key = ("", "", "")
        else:
            key = (artist, title, album if isinstance(album, str) else "")

        bucket = grouped.setdefault(key, {
            "title": title if isinstance(title, str) else None,
            "artist": artist if isinstance(artist, str) else None,
            "album": album if isinstance(album, str) else None,
            "year": attrs.get("SongYear") if isinstance(attrs.get("SongYear"), int) else None,
            "length": attrs.get("SongLength") if isinstance(attrs.get("SongLength"), (int, float)) else None,
            "arrangements": [],
        })
        # Update missing scalar fields from later entries (rare).
        for k_attr, k_bucket in (("SongName", "title"), ("ArtistName", "artist"),
                                 ("AlbumName", "album"), ("SongYear", "year"),
                                 ("SongLength", "length")):
            if bucket[k_bucket] is None:
                v = attrs.get(k_attr)
                if v is not None:
                    bucket[k_bucket] = v

        arr_name_raw = attrs.get("ArrangementName")
        arr_name = arr_name_raw if isinstance(arr_name_raw, str) else "Unknown"
        tuning = _tuning_tuple(attrs.get("Tuning"))
        arr_list = bucket["arrangements"]
        assert isinstance(arr_list, list)
        arr_list.append(Arrangement(name=arr_name, tuning=tuning))

    # Merge sentinel ("", "", "") into the single real song if there's exactly one.
    sentinel = grouped.pop(("", "", ""), None)
    if sentinel is not None and len(grouped) == 1:
        only_bucket = next(iter(grouped.values()))
        only_arr = only_bucket["arrangements"]
        sentinel_arr = sentinel["arrangements"]
        assert isinstance(only_arr, list) and isinstance(sentinel_arr, list)
        only_arr.extend(sentinel_arr)
    elif sentinel is not None:
        # Couldn't safely merge — keep as a degraded entry.
        grouped[("", "", "")] = sentinel

    songs: list[Song] = []
    for bucket in grouped.values():
        title = bucket["title"] or "(unknown)"
        artist = bucket["artist"] or "(unknown)"
        album = bucket["album"] or ""
        year_raw = bucket["year"]
        year = year_raw if isinstance(year_raw, int) and year_raw > 0 else None
        length_raw = bucket["length"]
        length = float(length_raw) if isinstance(length_raw, (int, float)) else None
        arr_list = bucket["arrangements"]
        assert isinstance(arr_list, list)
        # Deduplicate arrangements by name keeping first.
        seen: set[str] = set()
        unique_arr: list[Arrangement] = []
        for a in arr_list:
            assert isinstance(a, Arrangement)
            if a.name in seen:
                continue
            seen.add(a.name)
            unique_arr.append(a)
        songs.append(Song(
            title=str(title), artist=str(artist), album=str(album),
            year=year, length_seconds=length, arrangements=tuple(unique_arr),
        ))
    return songs


def read_songs_from_psarc(path: Path) -> list[Song]:
    """Open a PSARC and parse the songs referenced by its .hsan(s)."""
    songs: list[Song] = []
    with PsarcArchive.open(path) as a:
        # A pack PSARC can contain several hsan; iterate them all.
        for name in a.names():
            if not name.endswith(".hsan"):
                continue
            try:
                data = a.read(name)
            except (FileNotFoundInPsarc, PsarcError):
                continue
            try:
                songs.extend(parse_hsan(data))
            except (ManifestError, ValueError, json.JSONDecodeError):
                continue
    return songs


__all__ = [
    "Arrangement",
    "Song",
    "ManifestError",
    "tuning_name",
    "parse_hsan",
    "read_songs_from_psarc",
]
