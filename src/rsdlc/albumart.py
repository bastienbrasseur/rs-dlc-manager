"""Extract album cover thumbnails from PSARC files.

Rocksmith DLC ship 3 album-art sizes as DDS textures: 64x64, 128x128, 256x256.
We grab the 64 (or smallest available), decode it via Pillow, and write a
small PNG into a cache directory. The UI then loads PNGs from there.

The cache filename is derived from the PSARC absolute path, so renaming or
moving the file invalidates its thumbnail (a fresh one is built on next pass).
"""

from __future__ import annotations

import hashlib
import logging
from io import BytesIO
from pathlib import Path

from rsdlc.psarc import PsarcArchive, PsarcError

logger = logging.getLogger(__name__)


def cache_filename(psarc_path: Path) -> str:
    """Stable filename for the thumbnail of ``psarc_path``."""
    digest = hashlib.sha1(str(psarc_path.resolve()).encode("utf-8")).hexdigest()
    return f"{digest}.png"


def cache_path_for(psarc_path: Path, cache_dir: Path) -> Path:
    return cache_dir / cache_filename(psarc_path)


def _smallest_dds(names: tuple[str, ...]) -> str | None:
    """Pick the smallest album_art DDS available (prefer 64, then 128, then 256)."""
    candidates = [n for n in names if "album_art" in n and n.endswith(".dds")]
    if not candidates:
        return None
    def key(name: str) -> tuple[int, str]:
        for suffix in ("_64.dds", "_128.dds", "_256.dds"):
            if name.endswith(suffix):
                return (int(suffix.split("_")[1].split(".")[0]), name)
        return (10_000, name)  # unknown → push to back
    return sorted(candidates, key=key)[0]


def extract_thumbnail_png(psarc_path: Path, target_size: int = 48) -> bytes | None:
    """Read the album art from a PSARC and return PNG bytes at ``target_size``.

    Returns None if the PSARC has no album art or cannot be decoded.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow not installed; thumbnails disabled")
        return None
    try:
        with PsarcArchive.open(psarc_path) as a:
            name = _smallest_dds(a.names())
            if name is None:
                return None
            dds = a.read(name)
    except (PsarcError, OSError) as exc:
        logger.debug("could not read %s: %s", psarc_path, exc)
        return None
    try:
        loaded = Image.open(BytesIO(dds))
        loaded.load()
        img: Image.Image = loaded
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        if img.size != (target_size, target_size):
            img = img.resize((target_size, target_size), Image.Resampling.LANCZOS)
        out = BytesIO()
        img.save(out, format="PNG", optimize=False)
        return out.getvalue()
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug("could not decode DDS in %s: %s", psarc_path, exc)
        return None


def build_thumbnail(psarc_path: Path, cache_dir: Path, target_size: int = 48) -> Path | None:
    """Extract+cache one PSARC's thumbnail. Returns the cached PNG path or None.

    If the cache file already exists, returns it without re-extracting.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_path_for(psarc_path, cache_dir)
    if out_path.is_file():
        return out_path
    png = extract_thumbnail_png(psarc_path, target_size=target_size)
    if png is None:
        return None
    try:
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp.write_bytes(png)
        tmp.replace(out_path)
    except OSError as exc:
        logger.warning("could not write thumbnail %s: %s", out_path, exc)
        return None
    return out_path


__all__ = [
    "cache_filename",
    "cache_path_for",
    "extract_thumbnail_png",
    "build_thumbnail",
]
