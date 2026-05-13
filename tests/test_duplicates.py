from pathlib import Path

from rsdlc.duplicates import duplicate_keys, is_duplicate
from rsdlc.library import DlcEntry


def _entry(path: str, artist: str, title: str) -> DlcEntry:
    return DlcEntry(
        path=Path(path), enabled=True,
        title=title, artist=artist, album="",
        year=None, length_seconds=None,
        arrangement_label="", tuning_label="",
    )


def test_no_duplicates() -> None:
    entries = [
        _entry("/a_p.psarc", "U2", "One"),
        _entry("/b_p.psarc", "Metallica", "One"),
    ]
    assert duplicate_keys(entries) == set()


def test_duplicate_across_files() -> None:
    entries = [
        _entry("/a_p.psarc", "U2", "One"),
        _entry("/b_p.psarc", "U2", "One"),
    ]
    keys = duplicate_keys(entries)
    assert keys == {("u2", "one")}
    assert is_duplicate(entries[0], keys)
    assert is_duplicate(entries[1], keys)


def test_case_and_whitespace_insensitive() -> None:
    entries = [
        _entry("/a_p.psarc", " U2 ", "One"),
        _entry("/b_p.psarc", "u2", " one"),
    ]
    assert duplicate_keys(entries) == {("u2", "one")}


def test_same_pack_not_a_duplicate() -> None:
    # A song pack has multiple songs sharing the same PSARC. Two entries with
    # the same artist+title in the same path is the user listing the same row
    # twice, not a duplicate.
    entries = [
        _entry("/pack_p.psarc", "X", "Song"),
        _entry("/pack_p.psarc", "X", "Song"),
    ]
    assert duplicate_keys(entries) == set()


def test_missing_metadata_never_duplicate() -> None:
    entries = [
        _entry("/a_p.psarc", "(no metadata)", ""),
        _entry("/b_p.psarc", "(no metadata)", ""),
    ]
    assert duplicate_keys(entries) == set()
