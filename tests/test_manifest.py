import json

import pytest

from rsdlc.manifest import ManifestError, parse_hsan, tuning_name


def test_tuning_name_known() -> None:
    assert tuning_name((0, 0, 0, 0, 0, 0)) == "E Standard"
    assert tuning_name((-2, 0, 0, 0, 0, 0)) == "Drop D"
    assert tuning_name((-1, -1, -1, -1, -1, -1)) == "Eb Standard"
    assert tuning_name((-6, -4, -4, -4, -4, -4)) == "Drop Bb"


def test_tuning_name_unknown_falls_back() -> None:
    label = tuning_name((+1, -3, 0, 0, 0, 0))
    assert "+1" in label and "-3" in label


def test_parse_hsan_basic() -> None:
    doc = {
        "Entries": {
            "lead-key": {
                "Attributes": {
                    "SongName": "Defeated",
                    "ArtistName": "If I Were You",
                    "AlbumName": "Inner Signals",
                    "SongYear": 2018,
                    "ArrangementName": "Lead",
                    "Tuning": {f"string{i}": v for i, v in enumerate([-6, -4, -4, -4, -4, -4])},
                    "SongLength": 217.5,
                }
            },
            "vocals-key": {
                "Attributes": {
                    "ArrangementName": "Vocals",
                    # no title/artist — merged into the only song
                }
            },
        }
    }
    songs = parse_hsan(json.dumps(doc).encode("utf-8"))
    assert len(songs) == 1
    s = songs[0]
    assert s.title == "Defeated"
    assert s.artist == "If I Were You"
    assert s.album == "Inner Signals"
    assert s.year == 2018
    assert s.length_seconds == pytest.approx(217.5)
    assert s.arrangement_label == "Lead, Vocals"
    assert s.primary_tuning_label == "Drop Bb"


def test_parse_hsan_no_entries_raises() -> None:
    with pytest.raises(ManifestError):
        parse_hsan(json.dumps({"Entries": []}).encode("utf-8"))


def test_parse_hsan_pack_with_two_songs() -> None:
    # song pack: two distinct songs
    doc = {
        "Entries": {
            "a-lead": {
                "Attributes": {
                    "SongName": "Song A", "ArtistName": "Artist A",
                    "AlbumName": "AlbumA", "SongYear": 2020,
                    "ArrangementName": "Lead",
                    "Tuning": {f"string{i}": 0 for i in range(6)},
                    "SongLength": 100.0,
                }
            },
            "b-lead": {
                "Attributes": {
                    "SongName": "Song B", "ArtistName": "Artist B",
                    "AlbumName": "AlbumB", "SongYear": 2021,
                    "ArrangementName": "Lead",
                    "Tuning": {f"string{i}": -1 for i in range(6)},
                    "SongLength": 200.0,
                }
            },
        }
    }
    songs = parse_hsan(json.dumps(doc).encode("utf-8"))
    titles = sorted(s.title for s in songs)
    assert titles == ["Song A", "Song B"]
