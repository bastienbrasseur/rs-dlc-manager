from pathlib import Path

from rsdlc.library import DlcEntry
from rsdlc.stats import compute_stats, format_duration


def _entry(path: str, artist: str = "Anon", title: str = "Title",
           year: int | None = 2000, length: float | None = 180.0,
           tuning: str = "E Standard", enabled: bool = True) -> DlcEntry:
    return DlcEntry(
        path=Path(path), enabled=enabled,
        title=title, artist=artist, album="",
        year=year, length_seconds=length,
        arrangement_label="Lead", tuning_label=tuning,
    )


def test_empty() -> None:
    s = compute_stats([])
    assert s.total_files == 0
    assert s.total_songs == 0
    assert s.total_seconds == 0
    assert s.by_tuning == ()


def test_basic_counts() -> None:
    entries = [
        _entry("/a_p.psarc", artist="A", title="x", year=1985, length=200.0, tuning="E Standard"),
        _entry("/b_p.psarc", artist="B", title="y", year=1992, length=300.0, tuning="Drop D"),
        _entry("/c_p.psarc", artist="A", title="z", year=2018, length=240.0, tuning="E Standard"),
        _entry("/d_p.psarc", artist="A", title="w", year=2018, length=120.0, tuning="Drop D", enabled=False),
    ]
    s = compute_stats(entries)
    assert s.total_files == 4
    assert s.total_songs == 4
    assert s.active_files == 3
    assert s.disabled_files == 1
    assert s.total_seconds == 860.0
    assert dict(s.by_tuning) == {"E Standard": 2, "Drop D": 2}
    assert dict(s.by_decade) == {1980: 1, 1990: 1, 2010: 2}
    assert s.top_artists[0] == ("A", 3)
    assert s.top_artists[1] == ("B", 1)


def test_no_metadata_counted_separately() -> None:
    entries = [
        _entry("/a_p.psarc", artist="(no metadata)", title="?"),
        _entry("/b_p.psarc", artist="A"),
    ]
    s = compute_stats(entries)
    assert s.songs_without_metadata == 1
    assert dict(s.top_artists) == {"A": 1}


def test_song_pack_counts_file_once() -> None:
    # Two entries from the same PSARC (a pack)
    entries = [
        _entry("/pack_p.psarc", artist="X", title="One"),
        _entry("/pack_p.psarc", artist="Y", title="Two"),
    ]
    s = compute_stats(entries)
    assert s.total_files == 1
    assert s.total_songs == 2


def test_format_duration() -> None:
    assert format_duration(0) == "0 min"
    assert format_duration(59) == "0 min"
    assert format_duration(60) == "1 min"
    assert format_duration(3599) == "59 min"
    assert format_duration(3600) == "1 h 00 min"
    assert format_duration(7325) == "2 h 02 min"
