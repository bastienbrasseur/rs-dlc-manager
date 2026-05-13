"""Scan a Rocksmith DLC folder, cache parsed metadata, and enable/disable songs.

The on-disk convention:

* Active DLC live in ``<root>/dlc/`` (recursive scan for ``*_p.psarc``).
* Disabled DLC live in a sibling ``<root>/dlc_disabled/``. We mirror the
  relative path of the file inside ``dlc/`` so we can move it back cleanly.

Enabling/disabling is *just* moving the file. We never touch the bytes of a
PSARC. Operations are pushed onto an in-memory undo stack so the UI can undo
the last move with Ctrl+Z.

The cache lives under ``~/.rs-dlc-manager/cache.json``. Each entry is keyed on
``(absolute path, mtime_ns, size)`` so the cache becomes invalid the instant
any of those three changes.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

from rsdlc.manifest import Arrangement, Song, read_songs_from_psarc
from rsdlc.psarc import PsarcError

logger = logging.getLogger(__name__)

CACHE_VERSION = 2  # bump whenever the on-disk schema changes


# ---------------------------------------------------------------------------
# DLC entry
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DlcEntry:
    """One song discovered on disk.

    A PSARC that contains multiple songs (pack) produces several DlcEntry sharing
    the same ``path``. Enabling/disabling moves the file as a whole, so all
    entries sharing a path flip together.
    """
    path: Path                  # absolute path to the .psarc on disk
    enabled: bool
    title: str
    artist: str
    album: str
    year: int | None
    length_seconds: float | None
    arrangement_label: str
    tuning_label: str

    @property
    def filename(self) -> str:
        return self.path.name


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

@dataclass
class _CacheRecord:
    mtime_ns: int
    size: int
    songs: list[dict[str, object]]   # serialized Song list


def _serialize_song(s: Song) -> dict[str, object]:
    return {
        "title": s.title,
        "artist": s.artist,
        "album": s.album,
        "year": s.year,
        "length_seconds": s.length_seconds,
        "arrangements": [
            {"name": a.name, "tuning": list(a.tuning) if a.tuning else None}
            for a in s.arrangements
        ],
    }


def _deserialize_song(d: dict[str, object]) -> Song:
    arr_raw = d.get("arrangements")
    arrangements: list[Arrangement] = []
    if isinstance(arr_raw, list):
        for a in arr_raw:
            if not isinstance(a, dict):
                continue
            name = a.get("name")
            tuning_raw = a.get("tuning")
            tuning: tuple[int, ...] | None = None
            if isinstance(tuning_raw, list) and all(isinstance(x, int) for x in tuning_raw):
                tuning = tuple(int(x) for x in tuning_raw)
            arrangements.append(Arrangement(
                name=str(name) if isinstance(name, str) else "Unknown",
                tuning=tuning,
            ))
    year_raw = d.get("year")
    length_raw = d.get("length_seconds")
    return Song(
        title=str(d.get("title") or "(unknown)"),
        artist=str(d.get("artist") or "(unknown)"),
        album=str(d.get("album") or ""),
        year=year_raw if isinstance(year_raw, int) else None,
        length_seconds=float(length_raw) if isinstance(length_raw, (int, float)) else None,
        arrangements=tuple(arrangements),
    )


class Cache:
    """JSON-backed cache of parsed PSARC metadata, keyed by absolute path."""

    __slots__ = ("path", "_records", "_dirty")

    def __init__(self, path: Path) -> None:
        self.path = path
        self._records: dict[str, _CacheRecord] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            doc = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("could not load cache (%s); starting fresh", exc)
            return
        if not isinstance(doc, dict) or doc.get("version") != CACHE_VERSION:
            logger.info("cache schema mismatch; ignoring")
            return
        records = doc.get("records")
        if not isinstance(records, dict):
            return
        for k, v in records.items():
            if not isinstance(v, dict):
                continue
            mtime_ns = v.get("mtime_ns")
            size = v.get("size")
            songs = v.get("songs")
            if not isinstance(mtime_ns, int) or not isinstance(size, int) or not isinstance(songs, list):
                continue
            self._records[k] = _CacheRecord(mtime_ns=mtime_ns, size=size, songs=songs)

    def get(self, path: Path, mtime_ns: int, size: int) -> list[Song] | None:
        rec = self._records.get(str(path))
        if rec is None or rec.mtime_ns != mtime_ns or rec.size != size:
            return None
        return [_deserialize_song(d) for d in rec.songs if isinstance(d, dict)]

    def put(self, path: Path, mtime_ns: int, size: int, songs: list[Song]) -> None:
        self._records[str(path)] = _CacheRecord(
            mtime_ns=mtime_ns, size=size,
            songs=[_serialize_song(s) for s in songs],
        )
        self._dirty = True

    def forget(self, path: Path) -> None:
        if self._records.pop(str(path), None) is not None:
            self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {
            "version": CACHE_VERSION,
            "records": {
                k: {"mtime_ns": v.mtime_ns, "size": v.size, "songs": v.songs}
                for k, v in self._records.items()
            },
        }
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self.path)
        self._dirty = False


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------

ScanCallback = Callable[[Path, list[DlcEntry]], None]


class Library:
    """Holds the dlc/ + dlc_disabled/ pair for a given Rocksmith root.

    Construction does not touch disk. Call :meth:`scan` (optionally with a
    callback) to populate :attr:`entries`.
    """

    def __init__(self, rocksmith_root: Path, cache_path: Path | None = None) -> None:
        self.rocksmith_root = rocksmith_root
        self.dlc_dir = rocksmith_root / "dlc"
        self.disabled_dir = rocksmith_root / "dlc_disabled"
        self.trash_dir = rocksmith_root / "dlc_trash"
        self.cache_path = cache_path or Path.home() / ".rs-dlc-manager" / "cache.json"
        self.cache = Cache(self.cache_path)
        self.entries: list[DlcEntry] = []
        self._undo: list[tuple[Path, Path]] = []  # list of (from, to) moves

    # ---- scanning ---------------------------------------------------------

    def iter_psarcs(self) -> Iterator[tuple[Path, bool]]:
        """Yield ``(psarc_path, enabled)`` for every ``*_p.psarc`` under dlc/ and dlc_disabled/."""
        for root, enabled in ((self.dlc_dir, True), (self.disabled_dir, False)):
            if not root.is_dir():
                continue
            for path in root.rglob("*_p.psarc"):
                if path.is_file():
                    yield path, enabled

    def scan(
        self,
        force: bool = False,
        on_chunk: ScanCallback | None = None,
        chunk_size: int = 25,
    ) -> list[DlcEntry]:
        """Walk the dlc folders, parse each PSARC (using cache when possible),
        and return the accumulated entries.

        ``on_chunk`` is invoked with ``(last_path, entries_so_far)`` every
        ``chunk_size`` files, so the UI can update progressively.
        """
        # Housekeeping: drop cache entries for paths that no longer exist.
        live_paths: set[str] = set()
        for path, _en in self.iter_psarcs():
            live_paths.add(str(path))
        stale = [k for k in list(self.cache._records) if k not in live_paths]
        for k in stale:
            self.cache._records.pop(k, None)
        if stale:
            self.cache._dirty = True
            logger.info("purged %d stale cache entries", len(stale))

        entries: list[DlcEntry] = []
        new_count = 0
        for path, enabled in self.iter_psarcs():
            try:
                stat = path.stat()
            except OSError as exc:
                logger.warning("stat %s failed: %s", path, exc)
                continue

            songs = None if force else self.cache.get(path, stat.st_mtime_ns, stat.st_size)
            if songs is None:
                try:
                    songs = read_songs_from_psarc(path)
                except (PsarcError, OSError) as exc:
                    logger.warning("could not parse %s: %s", path, exc)
                    songs = []
                except Exception as exc:  # never crash the whole scan
                    logger.warning("unexpected error parsing %s: %s", path, exc, exc_info=True)
                    songs = []
                self.cache.put(path, stat.st_mtime_ns, stat.st_size, songs)
                new_count += 1

            if not songs:
                # PSARC didn't yield any song (tones pack, corrupt, etc.). Still
                # show one row so the user can see it exists and toggle it.
                entries.append(DlcEntry(
                    path=path, enabled=enabled,
                    title=path.stem, artist="(no metadata)", album="",
                    year=None, length_seconds=None,
                    arrangement_label="", tuning_label="",
                ))
            else:
                for s in songs:
                    entries.append(DlcEntry(
                        path=path, enabled=enabled,
                        title=s.title, artist=s.artist, album=s.album,
                        year=s.year, length_seconds=s.length_seconds,
                        arrangement_label=s.arrangement_label,
                        tuning_label=s.primary_tuning_label,
                    ))
            if on_chunk and (len(entries) % chunk_size == 0):
                on_chunk(path, entries)

        if new_count:
            try:
                self.cache.save()
            except OSError as exc:
                logger.warning("could not write cache: %s", exc)
        self.entries = entries
        return entries

    # ---- enable / disable -------------------------------------------------

    def disable(self, path: Path) -> Path:
        """Move ``path`` from dlc/ to dlc_disabled/ (mirroring sub-tree). Returns the new path."""
        return self._move(path, src_root=self.dlc_dir, dst_root=self.disabled_dir)

    def enable(self, path: Path) -> Path:
        """Move ``path`` from dlc_disabled/ back to dlc/. Returns the new path."""
        return self._move(path, src_root=self.disabled_dir, dst_root=self.dlc_dir)

    def _move(self, path: Path, src_root: Path, dst_root: Path) -> Path:
        path = path.resolve()
        try:
            rel = path.relative_to(src_root.resolve())
        except ValueError as exc:
            raise ValueError(
                f"{path} is not inside {src_root}"
            ) from exc
        dst = dst_root / rel
        if dst.exists():
            raise FileExistsError(f"destination already exists: {dst}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dst))
        self._undo.append((path, dst))
        # Cache key is keyed on absolute path; after a move it's stale.
        self.cache.forget(path)
        return dst

    def can_undo(self) -> bool:
        return bool(self._undo)

    def undo(self) -> Path | None:
        """Reverse the last move. Returns the path of the moved file (in its restored location)."""
        if not self._undo:
            return None
        src_before, dst_after = self._undo.pop()
        if not dst_after.exists():
            logger.warning("undo target vanished: %s", dst_after)
            return None
        if src_before.exists():
            logger.warning("undo source already exists: %s", src_before)
            return None
        src_before.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst_after), str(src_before))
        self.cache.forget(dst_after)
        return src_before

    def disable_many(self, paths: Iterable[Path]) -> tuple[list[Path], list[tuple[Path, Exception]]]:
        """Disable multiple files. Returns (ok_destinations, [(failed_path, error)])."""
        ok: list[Path] = []
        errors: list[tuple[Path, Exception]] = []
        for p in paths:
            try:
                ok.append(self.disable(p))
            except (OSError, ValueError) as exc:
                errors.append((p, exc))
        return ok, errors

    def enable_many(self, paths: Iterable[Path]) -> tuple[list[Path], list[tuple[Path, Exception]]]:
        ok: list[Path] = []
        errors: list[tuple[Path, Exception]] = []
        for p in paths:
            try:
                ok.append(self.enable(p))
            except (OSError, ValueError) as exc:
                errors.append((p, exc))
        return ok, errors

    # ---- trash (reversible) and empty_trash (final) ----

    def trash(self, path: Path) -> Path:
        """Move a PSARC to ``dlc_trash/`` (reversible via :meth:`undo`).

        The file's location inside dlc/ or dlc_disabled/ is mirrored so that
        Ctrl+Z restores it to where it came from.
        """
        path = path.resolve()
        if path.is_relative_to(self.dlc_dir.resolve()):
            return self._move(path, src_root=self.dlc_dir, dst_root=self.trash_dir)
        if path.is_relative_to(self.disabled_dir.resolve()):
            return self._move(path, src_root=self.disabled_dir, dst_root=self.trash_dir)
        raise ValueError(f"{path} is not in dlc/ nor dlc_disabled/")

    def trash_files(self) -> list[Path]:
        """Every ``*_p.psarc`` currently in the trash."""
        if not self.trash_dir.is_dir():
            return []
        return [p for p in self.trash_dir.rglob("*_p.psarc") if p.is_file()]

    def empty_trash(self) -> int:
        """Permanently delete every file in the trash. Returns the count deleted."""
        if not self.trash_dir.is_dir():
            return 0
        count = 0
        for f in self.trash_dir.rglob("*"):
            if f.is_file():
                try:
                    f.unlink()
                    count += 1
                except OSError as exc:
                    logger.warning("could not unlink %s: %s", f, exc)
        # Prune empty subdirs (deepest first), but keep dlc_trash itself.
        all_subdirs = [d for d in self.trash_dir.rglob("*") if d.is_dir()]
        for d in sorted(all_subdirs, key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        # Anything we trashed is no longer recoverable; drop affected undo entries.
        trash_resolved = self.trash_dir.resolve()
        self._undo = [
            (a, b) for (a, b) in self._undo
            if not b.resolve().is_relative_to(trash_resolved)
        ]
        return count


__all__ = ["DlcEntry", "Library", "Cache"]
