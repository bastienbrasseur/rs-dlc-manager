import json
import shutil
from pathlib import Path

import pytest

from rsdlc.library import Library
from rsdlc.setlists import SetlistError, SetlistStore
from tests.test_library import _fixture_psarc_with_hsan


def _store(tmp_path: Path) -> SetlistStore:
    return SetlistStore(tmp_path / "setlists.json", tmp_path / "setlist_state.json")


def _make_library(tmp_path: Path, n: int = 4) -> tuple[Library, list[Path]]:
    (tmp_path / "dlc").mkdir()
    paths: list[Path] = []
    for i in range(n):
        p = tmp_path / "dlc" / f"track_{i}_p.psarc"
        p.write_bytes(_fixture_psarc_with_hsan(title=f"T{i}", artist=f"A{i}"))
        paths.append(p)
    lib = Library(tmp_path, cache_path=tmp_path / "cache.json")
    return lib, paths


def test_create_and_persist(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.create("Concert juin")
    s.create("À bosser")
    s.save()

    s2 = _store(tmp_path)
    assert set(s2.names()) == {"Concert juin", "À bosser"}


def test_create_duplicate_fails(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.create("X")
    with pytest.raises(SetlistError):
        s.create("X")


def test_add_remove_paths(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.create("A")
    p = tmp_path / "fake_p.psarc"
    p.write_text("")
    assert s.add_paths("A", [p]) == 1
    assert s.add_paths("A", [p]) == 0    # idempotent
    assert s.setlists["A"].paths == [str(p.resolve())]
    assert s.remove_paths("A", [p]) == 1
    assert s.setlists["A"].paths == []


def test_activate_and_restore_preserves_disabled(tmp_path: Path) -> None:
    """Songs that were already disabled before the setlist must stay disabled
    after the restore."""
    lib, paths = _make_library(tmp_path, n=4)
    # Before the setlist: track_0 is active, track_3 is deliberately disabled.
    lib.disable(paths[3])

    s = _store(tmp_path)
    s.create("session")
    s.add_paths("session", [paths[1], paths[2]])

    en, dis, errors = s.activate("session", lib)
    assert errors == []
    # During the setlist: only track_1 and track_2 should be enabled.
    assert (lib.dlc_dir / "track_0_p.psarc").exists() is False
    assert (lib.dlc_dir / "track_1_p.psarc").exists() is True
    assert (lib.dlc_dir / "track_2_p.psarc").exists() is True
    # track_3 stays disabled (was already disabled, must not be touched)
    assert (lib.disabled_dir / "track_3_p.psarc").exists() is True

    # Restore: track_0 must come back, track_3 must stay disabled.
    en2, dis2, errors2 = s.restore(lib)
    assert errors2 == []
    assert (lib.dlc_dir / "track_0_p.psarc").exists() is True
    assert (lib.dlc_dir / "track_1_p.psarc").exists() is True
    assert (lib.dlc_dir / "track_2_p.psarc").exists() is True
    # Crucial assertion of the user's requirement:
    assert (lib.disabled_dir / "track_3_p.psarc").exists() is True
    assert (lib.dlc_dir / "track_3_p.psarc").exists() is False


def test_cannot_activate_two_setlists(tmp_path: Path) -> None:
    lib, paths = _make_library(tmp_path, n=2)
    s = _store(tmp_path)
    s.create("A")
    s.create("B")
    s.add_paths("A", [paths[0]])
    s.activate("A", lib)
    with pytest.raises(SetlistError):
        s.activate("B", lib)


def test_cannot_delete_active_setlist(tmp_path: Path) -> None:
    lib, paths = _make_library(tmp_path, n=1)
    s = _store(tmp_path)
    s.create("A")
    s.activate("A", lib)
    with pytest.raises(SetlistError):
        s.delete("A")


def test_state_file_survives_restart(tmp_path: Path) -> None:
    lib, paths = _make_library(tmp_path, n=2)
    s = _store(tmp_path)
    s.create("A")
    s.add_paths("A", [paths[0]])
    s.activate("A", lib)
    s.save()

    s2 = _store(tmp_path)
    assert s2.active_name == "A"
    assert s2.snapshot   # not empty


def test_switch_to_takes_a_fresh_snapshot(tmp_path: Path) -> None:
    """When switching from setlist A to setlist B, the snapshot for B must be
    taken against the user's *original* state (i.e. after restoring A), not
    against A's filtered view. Songs that were deliberately disabled before A
    must therefore still be disabled when we eventually restore B."""
    lib, paths = _make_library(tmp_path, n=4)
    # User deliberately disabled track_3 before touching any setlist.
    lib.disable(paths[3])

    s = _store(tmp_path)
    s.create("A")
    s.create("B")
    s.add_paths("A", [paths[0]])
    s.add_paths("B", [paths[1]])

    s.activate("A", lib)
    s.switch_to("B", lib)
    assert s.active_name == "B"
    # Only track_1 is enabled, the rest disabled (incl. track_3 still disabled)
    assert (lib.dlc_dir / "track_0_p.psarc").exists() is False
    assert (lib.dlc_dir / "track_1_p.psarc").exists() is True
    assert (lib.dlc_dir / "track_2_p.psarc").exists() is False
    assert (lib.dlc_dir / "track_3_p.psarc").exists() is False

    s.restore(lib)
    # Original state preserved: track_0, _1, _2 active, track_3 still disabled
    assert (lib.dlc_dir / "track_0_p.psarc").exists() is True
    assert (lib.dlc_dir / "track_1_p.psarc").exists() is True
    assert (lib.dlc_dir / "track_2_p.psarc").exists() is True
    assert (lib.disabled_dir / "track_3_p.psarc").exists() is True


def test_rename_path_updates_setlist(tmp_path: Path) -> None:
    lib, paths = _make_library(tmp_path, n=1)
    s = _store(tmp_path)
    s.create("A")
    s.add_paths("A", [paths[0]])
    new_p = lib.disable(paths[0])
    s.rename_path(paths[0], new_p)
    assert str(new_p.resolve()) in s.setlists["A"].paths
    assert str(paths[0].resolve()) not in s.setlists["A"].paths
