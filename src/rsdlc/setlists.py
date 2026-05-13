"""Named setlists and the activate / restore mechanic.

A setlist is a named, ordered list of PSARC paths. The user can ``activate`` a
setlist, which temporarily moves files around so Rocksmith only sees the songs
of that setlist. Activation always takes a *snapshot* of the on-disk state
first, so :meth:`SetlistStore.restore` can put every file back exactly where it
was — including songs the user had deliberately disabled before activating.

Two on-disk files in ``~/.rs-dlc-manager/``:

- ``setlists.json`` — the lists themselves ``{ name: [paths] }``.
- ``setlist_state.json`` — when a setlist is active, the active name and the
  snapshot ``{ resolved_path: was_enabled }``. Absent when no setlist is active.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass
class Setlist:
    name: str
    paths: list[str] = field(default_factory=list)   # absolute paths as strings


class SetlistError(Exception):
    """Raised on programmer errors (duplicate name, unknown name, …)."""


class _Library(Protocol):
    """Minimal interface this module needs from :class:`Library`."""
    dlc_dir: Path
    disabled_dir: Path
    def disable(self, path: Path) -> Path: ...
    def enable(self, path: Path) -> Path: ...


class SetlistStore:
    """Holds the user's setlists and the (optional) active-setlist snapshot."""

    def __init__(self, lists_path: Path, state_path: Path) -> None:
        self.lists_path = lists_path
        self.state_path = state_path
        self.setlists: dict[str, Setlist] = {}
        self.active_name: str | None = None
        self.snapshot: dict[str, bool] = {}      # resolved path -> was_enabled
        self._load()

    # ---- persistence ----

    def _load(self) -> None:
        if self.lists_path.is_file():
            try:
                doc = json.loads(self.lists_path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("could not load setlists: %s", exc)
                doc = {}
            if isinstance(doc, dict):
                for name, paths in doc.items():
                    if isinstance(name, str) and isinstance(paths, list):
                        self.setlists[name] = Setlist(
                            name=name,
                            paths=[str(p) for p in paths if isinstance(p, str)],
                        )
        if self.state_path.is_file():
            try:
                state = json.loads(self.state_path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("could not load setlist state: %s", exc)
                state = {}
            if isinstance(state, dict):
                name = state.get("active")
                snap = state.get("snapshot")
                if isinstance(name, str) and isinstance(snap, dict):
                    self.active_name = name
                    self.snapshot = {
                        k: bool(v) for k, v in snap.items() if isinstance(k, str)
                    }

    def save(self) -> None:
        self.lists_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {name: sl.paths for name, sl in self.setlists.items()}
        tmp = self.lists_path.with_suffix(self.lists_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.lists_path)

        if self.active_name is None:
            if self.state_path.is_file():
                self.state_path.unlink()
            return
        state = {"active": self.active_name, "snapshot": self.snapshot}
        tmp2 = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp2.write_text(json.dumps(state), encoding="utf-8")
        tmp2.replace(self.state_path)

    # ---- mutations on the lists themselves ----

    def names(self) -> list[str]:
        return sorted(self.setlists.keys(), key=str.casefold)

    def create(self, name: str) -> Setlist:
        name = name.strip()
        if not name:
            raise SetlistError("empty setlist name")
        if name in self.setlists:
            raise SetlistError(f"setlist already exists: {name}")
        sl = Setlist(name=name)
        self.setlists[name] = sl
        return sl

    def delete(self, name: str) -> None:
        if name not in self.setlists:
            raise SetlistError(f"unknown setlist: {name}")
        if self.active_name == name:
            raise SetlistError("cannot delete an active setlist; restore first")
        del self.setlists[name]

    def rename(self, old: str, new: str) -> None:
        new = new.strip()
        if not new:
            raise SetlistError("empty setlist name")
        if old not in self.setlists:
            raise SetlistError(f"unknown setlist: {old}")
        if new == old:
            return
        if new in self.setlists:
            raise SetlistError(f"already exists: {new}")
        sl = self.setlists.pop(old)
        sl.name = new
        self.setlists[new] = sl
        if self.active_name == old:
            self.active_name = new

    def add_paths(self, name: str, paths: list[Path]) -> int:
        if name not in self.setlists:
            raise SetlistError(f"unknown setlist: {name}")
        existing = set(self.setlists[name].paths)
        added = 0
        for p in paths:
            key = str(p.resolve()) if p.exists() else str(p)
            if key not in existing:
                self.setlists[name].paths.append(key)
                existing.add(key)
                added += 1
        return added

    def remove_paths(self, name: str, paths: list[Path]) -> int:
        if name not in self.setlists:
            raise SetlistError(f"unknown setlist: {name}")
        targets = {str(p.resolve()) if p.exists() else str(p) for p in paths}
        before = len(self.setlists[name].paths)
        self.setlists[name].paths = [
            p for p in self.setlists[name].paths if p not in targets
        ]
        return before - len(self.setlists[name].paths)

    def rename_path(self, old: Path, new: Path) -> None:
        """Track a PSARC that has moved on disk (disable/enable/trash)."""
        old_key = str(old.resolve()) if old.exists() else str(old)
        new_key = str(new.resolve()) if new.exists() else str(new)
        for sl in self.setlists.values():
            sl.paths = [new_key if p == old_key else p for p in sl.paths]
        if old_key in self.snapshot:
            self.snapshot[new_key] = self.snapshot.pop(old_key)

    # ---- activation ----

    def is_active(self) -> bool:
        return self.active_name is not None

    @staticmethod
    def _all_known_paths(library: _Library) -> list[Path]:
        out: list[Path] = []
        if library.dlc_dir.is_dir():
            out.extend(p for p in library.dlc_dir.rglob("*_p.psarc") if p.is_file())
        if library.disabled_dir.is_dir():
            out.extend(p for p in library.disabled_dir.rglob("*_p.psarc") if p.is_file())
        return out

    def take_snapshot(self, library: _Library) -> None:
        """Record which files are currently in ``dlc/`` (active) vs disabled."""
        dlc_resolved = library.dlc_dir.resolve()
        self.snapshot.clear()
        for p in self._all_known_paths(library):
            try:
                resolved = p.resolve()
            except OSError:
                continue
            enabled = False
            try:
                resolved.relative_to(dlc_resolved)
                enabled = True
            except ValueError:
                pass
            self.snapshot[str(resolved)] = enabled

    def activate(self, name: str, library: _Library) -> tuple[int, int, list[tuple[Path, Exception]]]:
        """Apply a setlist: enable songs in it, disable the rest.

        Returns ``(enabled_count, disabled_count, errors)``. ``errors`` is a
        list of (path, exception) for files that could not be moved.

        Songs in the setlist that no longer exist on disk are simply skipped.
        """
        if name not in self.setlists:
            raise SetlistError(f"unknown setlist: {name}")
        if self.active_name is not None:
            raise SetlistError(
                f"setlist '{self.active_name}' is already active; restore first"
            )

        self.take_snapshot(library)
        target_keys = set(self.setlists[name].paths)
        # Resolve target keys against current on-disk positions.
        target_resolved: set[str] = set()
        for p in self._all_known_paths(library):
            try:
                r = p.resolve()
            except OSError:
                continue
            if str(r) in target_keys:
                target_resolved.add(str(r))

        enabled_n = 0
        disabled_n = 0
        errors: list[tuple[Path, Exception]] = []
        renames: list[tuple[str, str]] = []  # (old_key, new_key) — applied after iter

        for path_str, was_enabled in self.snapshot.items():
            p = Path(path_str)
            should_be_enabled = path_str in target_resolved
            if should_be_enabled and not was_enabled:
                try:
                    new = library.enable(p)
                    renames.append((path_str, str(new.resolve())))
                    enabled_n += 1
                except (OSError, ValueError) as exc:
                    errors.append((p, exc))
            elif (not should_be_enabled) and was_enabled:
                try:
                    new = library.disable(p)
                    renames.append((path_str, str(new.resolve())))
                    disabled_n += 1
                except (OSError, ValueError) as exc:
                    errors.append((p, exc))
            # else: leave alone (already in the right state, including the
            # explicitly-disabled-before-activation case).

        for old_key, new_key in renames:
            if old_key in self.snapshot:
                self.snapshot[new_key] = self.snapshot.pop(old_key)

        self.active_name = name
        return enabled_n, disabled_n, errors

    def switch_to(self, name: str, library: _Library) -> tuple[int, int, list[tuple[Path, Exception]]]:
        """Switch to another setlist atomically.

        If a setlist is currently active, it is restored first (so the snapshot
        is taken against the user's *original* state, not against another
        setlist's filtered view). Then the new setlist is activated.
        """
        if self.active_name is not None:
            self.restore(library)
        return self.activate(name, library)

    def restore(self, library: _Library) -> tuple[int, int, list[tuple[Path, Exception]]]:
        """Put every file back where it was when the setlist was activated."""
        if self.active_name is None:
            raise SetlistError("no active setlist to restore")
        enabled_n = 0
        disabled_n = 0
        errors: list[tuple[Path, Exception]] = []
        dlc_resolved = library.dlc_dir.resolve()
        disabled_resolved = library.disabled_dir.resolve()

        for path_str, was_enabled in list(self.snapshot.items()):
            p = Path(path_str)
            if not p.exists():
                # File trashed or moved while setlist was active — leave it.
                continue
            try:
                resolved = p.resolve()
            except OSError:
                continue
            currently_enabled = False
            try:
                resolved.relative_to(dlc_resolved)
                currently_enabled = True
            except ValueError:
                pass
            if was_enabled and not currently_enabled:
                try:
                    library.enable(p)
                    enabled_n += 1
                except (OSError, ValueError) as exc:
                    errors.append((p, exc))
            elif not was_enabled and currently_enabled:
                try:
                    library.disable(p)
                    disabled_n += 1
                except (OSError, ValueError) as exc:
                    errors.append((p, exc))

        self.active_name = None
        self.snapshot.clear()
        return enabled_n, disabled_n, errors


__all__ = ["Setlist", "SetlistError", "SetlistStore"]
