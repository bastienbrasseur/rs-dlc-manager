"""Round-trip tests for the PSARC parser using fixtures built in memory."""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path

import pytest

from rsdlc.psarc import (
    FileNotFoundInPsarc,
    MalformedPsarc,
    PsarcArchive,
    UnsupportedCompression,
    UnsupportedPsarcVersion,
)


# ---------------------------------------------------------------------------
# Fixture builder — produces a non-encrypted PSARC v1.4 in memory
# ---------------------------------------------------------------------------

def _make_psarc(files: list[tuple[str, bytes]], block_size: int = 65536) -> bytes:
    """Build a minimal PSARC v1.4 archive in memory (no encryption).

    Files are added in the given order. The manifest (file #0) is generated
    automatically. Compression is per-block zlib with the 0-sentinel for full
    uncompressed blocks NOT used (we always compress, simpler test).
    """
    # zblock width.
    if block_size <= 1:
        raise ValueError
    width = 0
    v = block_size - 1
    while v:
        width += 1
        v >>= 8

    # Manifest entry #0 is the list of names of files #1..N, newline-separated.
    manifest_bytes = "\n".join(name for name, _ in files).encode("utf-8")

    # Prepend the manifest as entry #0 (its name is empty).
    payload_entries: list[tuple[str, bytes]] = [("", manifest_bytes)] + list(files)

    # Compress each entry into blocks; collect block-size list.
    zblock_sizes: list[int] = []
    bodies_blob = bytearray()
    entry_meta: list[tuple[bytes, int, int, int]] = []  # md5, z_index, length, offset

    next_offset_placeholder = 0  # we will rewrite offsets once header size known
    for name, data in payload_entries:
        md5 = hashlib.md5(name.encode("utf-8")).digest()
        z_index = len(zblock_sizes)
        length = len(data)
        offset_marker = len(bodies_blob)
        # split into blocks of block_size raw bytes
        for off in range(0, max(len(data), 1), block_size):
            chunk = data[off : off + block_size]
            if not chunk and off == 0:
                # empty file: emit a single zero-byte zlib stream so zindex is consumed
                comp = zlib.compress(b"")
                zblock_sizes.append(len(comp))
                bodies_blob.extend(comp)
                break
            comp = zlib.compress(chunk)
            # Test that the compressed size fits in the width.
            if len(comp) >= 1 << (8 * width):
                # spill: store raw (would normally use sentinel-0, but for tests
                # we just pad with another full block of zeros — never happens
                # with tiny test payloads anyway).
                raise AssertionError("compressed block too big for width")
            zblock_sizes.append(len(comp))
            bodies_blob.extend(comp)
        entry_meta.append((md5, z_index, length, offset_marker))

    # Now compute the final header + TOC layout.
    n = len(payload_entries)
    toc_entries_len = n * 30
    zblock_zone_len = len(zblock_sizes) * width
    toc_total = 32 + toc_entries_len + zblock_zone_len

    # Patch absolute offsets in entry_meta.
    final_entries = []
    for md5, z_index, length, off_marker in entry_meta:
        final_entries.append((md5, z_index, length, toc_total + off_marker))

    # Build header.
    out = bytearray()
    out += b"PSAR"
    out += struct.pack(">HH", 1, 4)
    out += b"zlib"
    out += struct.pack(">I", toc_total)
    out += struct.pack(">I", 30)
    out += struct.pack(">I", n)
    out += struct.pack(">I", block_size)
    out += struct.pack(">I", 0)  # archive_flags: no encryption

    # TOC entries (30 bytes each).
    for md5, z_index, length, offset in final_entries:
        out += md5
        out += struct.pack(">I", z_index)
        out += length.to_bytes(5, "big")
        out += offset.to_bytes(5, "big")

    # zBlockSizeList.
    for sz in zblock_sizes:
        out += sz.to_bytes(width, "big")

    # Bodies.
    out += bytes(bodies_blob)
    return bytes(out)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_roundtrip_basic(tmp_path: Path) -> None:
    files = [
        ("hello.txt", b"hello world\n"),
        ("data/payload.bin", bytes(range(256)) * 4),  # 1024 bytes
        ("empty.dat", b""),
    ]
    raw = _make_psarc(files)
    p = tmp_path / "test.psarc"
    p.write_bytes(raw)

    with PsarcArchive.open(p) as a:
        assert a.header.version_major == 1 and a.header.version_minor == 4
        assert a.header.num_entries == 4  # manifest + 3 files
        assert a.header.toc_encrypted is False
        assert set(a.names()) == {"hello.txt", "data/payload.bin", "empty.dat"}
        for name, content in files:
            assert a.read(name) == content


def test_large_file_multiple_blocks(tmp_path: Path) -> None:
    # Force multiple blocks by using a small block size.
    files = [("big.bin", bytes(range(256)) * 200)]  # 51200 bytes
    raw = _make_psarc(files, block_size=4096)
    p = tmp_path / "test.psarc"
    p.write_bytes(raw)

    with PsarcArchive.open(p) as a:
        assert a.header.block_size == 4096
        assert a.read("big.bin") == files[0][1]


def test_missing_file_raises(tmp_path: Path) -> None:
    raw = _make_psarc([("present.txt", b"ok")])
    p = tmp_path / "test.psarc"
    p.write_bytes(raw)
    with PsarcArchive.open(p) as a:
        with pytest.raises(FileNotFoundInPsarc):
            a.read("absent.txt")


def test_bad_magic(tmp_path: Path) -> None:
    raw = bytearray(_make_psarc([("a", b"a")]))
    raw[0:4] = b"NOPE"
    p = tmp_path / "test.psarc"
    p.write_bytes(bytes(raw))
    with pytest.raises(MalformedPsarc):
        PsarcArchive.open(p)


def test_unsupported_version(tmp_path: Path) -> None:
    raw = bytearray(_make_psarc([("a", b"a")]))
    raw[4:8] = struct.pack(">HH", 1, 3)
    p = tmp_path / "test.psarc"
    p.write_bytes(bytes(raw))
    with pytest.raises(UnsupportedPsarcVersion):
        PsarcArchive.open(p)


def test_unsupported_compression(tmp_path: Path) -> None:
    raw = bytearray(_make_psarc([("a", b"a")]))
    raw[8:12] = b"lzma"
    p = tmp_path / "test.psarc"
    p.write_bytes(bytes(raw))
    with pytest.raises(UnsupportedCompression):
        PsarcArchive.open(p)


def test_has_and_find(tmp_path: Path) -> None:
    raw = _make_psarc([("songs/foo.hsan", b"{}"), ("audio/foo.wem", b"\x00")])
    p = tmp_path / "test.psarc"
    p.write_bytes(raw)
    with PsarcArchive.open(p) as a:
        assert a.has("songs/foo.hsan")
        assert not a.has("songs/bar.hsan")
        assert a.find(lambda n: n.endswith(".hsan")) == "songs/foo.hsan"
        assert a.find(lambda n: n.endswith(".xml")) is None
