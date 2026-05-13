from pathlib import Path

from rsdlc.favorites import Favorites


def test_empty(tmp_path: Path) -> None:
    f = Favorites(tmp_path / "favs.json")
    assert f.contains(tmp_path / "x_p.psarc") is False
    assert f.all() == frozenset()


def test_add_save_reload(tmp_path: Path) -> None:
    a = tmp_path / "a_p.psarc"
    a.write_text("")
    f = Favorites(tmp_path / "favs.json")
    assert f.add(a) is True
    assert f.add(a) is False  # already present
    f.save()

    f2 = Favorites(tmp_path / "favs.json")
    assert f2.contains(a) is True


def test_toggle(tmp_path: Path) -> None:
    a = tmp_path / "a_p.psarc"
    a.write_text("")
    f = Favorites(tmp_path / "favs.json")
    assert f.toggle(a) is True
    assert f.contains(a)
    assert f.toggle(a) is False
    assert not f.contains(a)


def test_rename_updates_key(tmp_path: Path) -> None:
    src = tmp_path / "old_p.psarc"
    src.write_text("")
    f = Favorites(tmp_path / "favs.json")
    f.add(src)
    # Simulate a move
    dst = tmp_path / "dest_p.psarc"
    src.rename(dst)
    f.rename(src, dst)
    assert not f.contains(src)
    assert f.contains(dst)


def test_corrupt_json_starts_empty(tmp_path: Path) -> None:
    p = tmp_path / "favs.json"
    p.write_text("not json {", encoding="utf-8")
    f = Favorites(p)
    assert f.all() == frozenset()
