"""Persistent set of favorite PSARC paths.

Stored as JSON in ``~/.rs-dlc-manager/favorites.json``. Paths are stored as
absolute strings; lookups resolve the input path so a favorite survives the
file being toggled between ``dlc/`` and ``dlc_disabled/``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class Favorites:
    """A set of favorite PSARC paths backed by a JSON file."""

    __slots__ = ("path", "_paths", "_dirty")

    def __init__(self, store_path: Path) -> None:
        self.path = store_path
        self._paths: set[str] = set()
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            doc = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("could not load favorites (%s); starting empty", exc)
            return
        if isinstance(doc, dict):
            paths = doc.get("paths")
            if isinstance(paths, list):
                self._paths = {str(p) for p in paths if isinstance(p, str)}

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({"paths": sorted(self._paths)}), encoding="utf-8")
        tmp.replace(self.path)
        self._dirty = False

    @staticmethod
    def _key(p: Path) -> str:
        try:
            return str(p.resolve())
        except OSError:
            return str(p)

    def contains(self, p: Path) -> bool:
        return self._key(p) in self._paths

    def add(self, p: Path) -> bool:
        key = self._key(p)
        if key in self._paths:
            return False
        self._paths.add(key)
        self._dirty = True
        return True

    def remove(self, p: Path) -> bool:
        key = self._key(p)
        if key not in self._paths:
            return False
        self._paths.discard(key)
        self._dirty = True
        return True

    def toggle(self, p: Path) -> bool:
        """Flip the favorite state. Returns the new state."""
        if self.contains(p):
            self.remove(p)
            return False
        self.add(p)
        return True

    def rename(self, old: Path, new: Path) -> None:
        """Update the stored key when a file has moved on disk."""
        old_key = self._key(old)
        if old_key in self._paths:
            self._paths.discard(old_key)
            self._paths.add(self._key(new))
            self._dirty = True

    def all(self) -> frozenset[str]:
        return frozenset(self._paths)


__all__ = ["Favorites"]
