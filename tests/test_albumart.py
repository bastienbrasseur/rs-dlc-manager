from io import BytesIO
from pathlib import Path

import pytest

from rsdlc.albumart import build_thumbnail, cache_filename, cache_path_for
from tests.test_psarc import _make_psarc


def _make_dds_psarc() -> bytes:
    """Build a PSARC containing a fake but Pillow-decodable DDS."""
    pytest.importorskip("PIL")
    from PIL import Image
    img = Image.new("RGBA", (64, 64), (123, 45, 67, 255))
    buf = BytesIO()
    img.save(buf, format="DDS")
    dds = buf.getvalue()
    return _make_psarc([("gfxassets/album_art/album_test_64.dds", dds)])


def test_cache_filename_is_deterministic(tmp_path: Path) -> None:
    p = tmp_path / "foo_p.psarc"
    a = cache_filename(p)
    b = cache_filename(p)
    assert a == b
    assert a.endswith(".png")


def test_cache_paths_differ_for_different_psarcs(tmp_path: Path) -> None:
    p1 = tmp_path / "a_p.psarc"
    p2 = tmp_path / "b_p.psarc"
    assert cache_path_for(p1, tmp_path) != cache_path_for(p2, tmp_path)


def test_build_thumbnail_from_psarc(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    psarc = tmp_path / "track_p.psarc"
    psarc.write_bytes(_make_dds_psarc())
    cache = tmp_path / "thumbs"
    out = build_thumbnail(psarc, cache, target_size=48)
    assert out is not None
    assert out.is_file()
    # Should be a valid PNG
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_build_thumbnail_reuses_cache(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    psarc = tmp_path / "track_p.psarc"
    psarc.write_bytes(_make_dds_psarc())
    cache = tmp_path / "thumbs"
    out1 = build_thumbnail(psarc, cache)
    mtime_1 = out1.stat().st_mtime_ns if out1 else 0
    out2 = build_thumbnail(psarc, cache)
    mtime_2 = out2.stat().st_mtime_ns if out2 else 0
    assert out1 == out2
    assert mtime_1 == mtime_2  # not rewritten


def test_build_thumbnail_no_dds(tmp_path: Path) -> None:
    # A PSARC with no album_art DDS
    psarc = tmp_path / "empty_p.psarc"
    psarc.write_bytes(_make_psarc([("manifests/x.hsan", b"{}")]))
    cache = tmp_path / "thumbs"
    out = build_thumbnail(psarc, cache)
    assert out is None
