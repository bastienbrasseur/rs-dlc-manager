import json
import struct
import zlib
from pathlib import Path

from rsdlc.library import Library
from tests.test_psarc import _make_psarc


def _fixture_psarc_with_hsan(title: str = "TestTrack", artist: str = "TestArtist") -> bytes:
    """Build a PSARC containing one .hsan describing a single song."""
    doc = {
        "Entries": {
            "lead-key": {
                "Attributes": {
                    "SongName": title,
                    "ArtistName": artist,
                    "AlbumName": "TestAlbum",
                    "SongYear": 2024,
                    "ArrangementName": "Lead",
                    "Tuning": {f"string{i}": 0 for i in range(6)},
                    "SongLength": 123.4,
                }
            }
        }
    }
    hsan = json.dumps(doc).encode("utf-8")
    return _make_psarc([("manifests/songs_dlc_test/songs_dlc_test.hsan", hsan)])


def test_scan_finds_and_caches(tmp_path: Path) -> None:
    root = tmp_path
    (root / "dlc").mkdir()
    (root / "dlc" / "foo_p.psarc").write_bytes(_fixture_psarc_with_hsan())
    cache = tmp_path / "cache.json"
    lib = Library(root, cache_path=cache)

    entries = lib.scan()
    assert len(entries) == 1
    e = entries[0]
    assert e.enabled is True
    assert e.title == "TestTrack"
    assert e.artist == "TestArtist"
    assert "Lead" in e.arrangement_label
    assert e.tuning_label == "E Standard"
    assert cache.is_file()

    # Second scan hits the cache (no exception when we re-read the same file).
    entries2 = lib.scan()
    assert [x.title for x in entries2] == ["TestTrack"]


def test_disable_then_enable_then_undo(tmp_path: Path) -> None:
    root = tmp_path
    (root / "dlc").mkdir()
    src = root / "dlc" / "subdir" / "track_p.psarc"
    src.parent.mkdir()
    src.write_bytes(_fixture_psarc_with_hsan())
    lib = Library(root, cache_path=tmp_path / "cache.json")
    lib.scan()

    dst = lib.disable(src)
    assert not src.exists()
    assert dst.exists()
    assert dst.is_relative_to(root / "dlc_disabled")
    # path mirroring is preserved
    assert dst == root / "dlc_disabled" / "subdir" / "track_p.psarc"

    restored = lib.enable(dst)
    assert restored == src
    assert src.exists()
    assert not dst.exists()

    # Now undo (reverses the enable, i.e. moves it back to dlc_disabled)
    undo_target = lib.undo()
    assert undo_target is not None
    assert undo_target == dst
    assert dst.exists()
    assert not src.exists()


def test_disable_into_existing_destination_refuses(tmp_path: Path) -> None:
    root = tmp_path
    (root / "dlc").mkdir()
    (root / "dlc_disabled").mkdir()
    src = root / "dlc" / "track_p.psarc"
    src.write_bytes(_fixture_psarc_with_hsan())
    (root / "dlc_disabled" / "track_p.psarc").write_bytes(b"squat")
    lib = Library(root, cache_path=tmp_path / "cache.json")
    import pytest
    with pytest.raises(FileExistsError):
        lib.disable(src)
    # source still in place
    assert src.exists()


def test_scan_yields_disabled_entries_too(tmp_path: Path) -> None:
    root = tmp_path
    (root / "dlc").mkdir()
    (root / "dlc_disabled").mkdir()
    (root / "dlc" / "a_p.psarc").write_bytes(_fixture_psarc_with_hsan("A", "ArtA"))
    (root / "dlc_disabled" / "b_p.psarc").write_bytes(_fixture_psarc_with_hsan("B", "ArtB"))
    lib = Library(root, cache_path=tmp_path / "cache.json")
    entries = lib.scan()
    by_title = {e.title: e for e in entries}
    assert by_title["A"].enabled is True
    assert by_title["B"].enabled is False
