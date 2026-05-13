"""PSARC v1.4 reader (Rocksmith 2014, PC).

Pure stdlib parser. Lazy: only the 32-byte header, the TOC entries, the block
size list and the manifest (first file) are eagerly decoded. File payloads are
zlib-inflated on demand via :meth:`PsarcArchive.read`.

The format:
    - Big-endian throughout.
    - Header (32 bytes): magic, version, compression, toc_total_size,
      toc_entry_size, num_entries, block_size, archive_flags.
    - TOC entries (num_entries * 30 bytes): md5(16), z_index_begin(u32),
      length(40-bit), offset(40-bit).
    - zBlockSizeList: variable-width entries, width = ceil(log256(block_size)).
      A value of 0 means "uncompressed full block of block_size bytes".
    - When `archive_flags & 4`, the [TOC entries + zBlockSizeList] zone is
      AES-256 CFB encrypted (see :mod:`rsdlc.crypto`).
    - Entry #0 is the manifest itself: a newline-separated list of paths for
      entries #1..N. md5 of the manifest entry is md5(b"").

Public exceptions and classes are listed in __all__ at module bottom.
"""

from __future__ import annotations

import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Self

from rsdlc.crypto import decrypt_toc

_HEADER_FMT = ">4sIIIIIIII"  # 9 fields, 36 bytes? -> we only need 32 bytes
# Actually 4s + 8*I = 4 + 32 = 36. We want 4s + 7*I (with version split below).
_HEADER_STRUCT = struct.Struct(">4s4s4sIIIII")  # magic, version, compression, 5*u32
assert _HEADER_STRUCT.size == 32

_TOC_ENTRY_SIZE = 30
_FLAG_TOC_ENCRYPTED = 0b100  # bit 2

_ZLIB_MAGIC_HIGH = 0x78  # zlib streams start with 0x78 ??


class PsarcError(Exception):
    """Base class for all PSARC parsing errors."""


class MalformedPsarc(PsarcError):
    """The header or TOC is corrupt or fails sanity checks."""


class UnsupportedPsarcVersion(PsarcError):
    """Only PSARC v1.4 is supported."""


class UnsupportedCompression(PsarcError):
    """Only zlib compression is supported."""


class FileNotFoundInPsarc(PsarcError):
    """The requested path is not listed in the archive's manifest."""

    def __init__(self, name: str) -> None:
        super().__init__(f"File not found in PSARC: {name!r}")
        self.name = name


@dataclass(frozen=True, slots=True)
class PsarcHeader:
    version_major: int
    version_minor: int
    compression: str
    toc_total_size: int
    toc_entry_size: int
    num_entries: int
    block_size: int
    archive_flags: int

    @property
    def toc_encrypted(self) -> bool:
        return bool(self.archive_flags & _FLAG_TOC_ENCRYPTED)


@dataclass(frozen=True, slots=True)
class PsarcEntry:
    name: str            # "" for entry #0 (the manifest)
    md5: bytes           # 16 bytes
    z_index_begin: int
    length: int          # uncompressed size (40-bit)
    offset: int          # absolute offset in the file (40-bit)


def _read_u40_be(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off : off + 5], "big")


def _zblock_width(block_size: int) -> int:
    """Bytes per entry in the zBlockSizeList.

    Width is wide enough to encode any compressed block size, which is at most
    ``block_size - 1`` bytes (a block that would be ``block_size`` or larger is
    stored uncompressed and signaled by the sentinel value 0). So for the
    canonical 64 KiB blocks (max value 0xFFFF), 2 bytes suffice.
    """
    if block_size <= 1:
        raise MalformedPsarc(f"invalid block_size: {block_size}")
    n = 0
    v = block_size - 1
    while v:
        n += 1
        v >>= 8
    return n  # 2 for 65536, 3 for 16M, 4 for 4G


class PsarcArchive:
    """Read-only access to a PSARC archive.

    Use as a context manager so the underlying file handle is closed promptly::

        with PsarcArchive.open(path) as a:
            data = a.read("manifests/songs_dlc_foo/songs_dlc_foo.hsan")
    """

    __slots__ = ("path", "header", "entries", "_zblocks", "_fh")

    path: Path
    header: PsarcHeader
    entries: tuple[PsarcEntry, ...]
    _zblocks: tuple[int, ...]
    _fh: BinaryIO

    def __init__(self, path: Path) -> None:
        self.path = path
        fh = open(path, "rb")
        try:
            header_bytes = fh.read(32)
            if len(header_bytes) < 32:
                raise MalformedPsarc("file shorter than PSARC header")
            magic, version_b, compression_b, toc_total, toc_entry_sz, n, block_sz, flags = \
                _HEADER_STRUCT.unpack(header_bytes)
            if magic != b"PSAR":
                raise MalformedPsarc(f"bad magic: {magic!r}")
            major, minor = struct.unpack(">HH", version_b)
            if (major, minor) != (1, 4):
                raise UnsupportedPsarcVersion(f"PSARC v{major}.{minor} not supported")
            if compression_b != b"zlib":
                raise UnsupportedCompression(f"compression {compression_b!r} not supported")
            if toc_entry_sz != _TOC_ENTRY_SIZE:
                raise MalformedPsarc(f"unexpected TOC entry size: {toc_entry_sz}")
            if n == 0:
                raise MalformedPsarc("PSARC has no entries (not even a manifest)")

            self.header = PsarcHeader(
                version_major=major,
                version_minor=minor,
                compression="zlib",
                toc_total_size=toc_total,
                toc_entry_size=toc_entry_sz,
                num_entries=n,
                block_size=block_sz,
                archive_flags=flags,
            )

            toc_zone_len = toc_total - 32
            if toc_zone_len <= 0:
                raise MalformedPsarc("toc_total_size <= header size")
            toc_zone = fh.read(toc_zone_len)
            if len(toc_zone) < toc_zone_len:
                raise MalformedPsarc("truncated TOC zone")

            if self.header.toc_encrypted:
                toc_zone = decrypt_toc(toc_zone)[:toc_zone_len]

            entries_len = n * _TOC_ENTRY_SIZE
            if entries_len > len(toc_zone):
                raise MalformedPsarc("TOC entries overflow toc zone")

            raw_entries: list[PsarcEntry] = []
            for i in range(n):
                base = i * _TOC_ENTRY_SIZE
                md5 = bytes(toc_zone[base : base + 16])
                z_index = int.from_bytes(toc_zone[base + 16 : base + 20], "big")
                length = _read_u40_be(toc_zone, base + 20)
                offset = _read_u40_be(toc_zone, base + 25)
                raw_entries.append(
                    PsarcEntry(name="", md5=md5, z_index_begin=z_index,
                               length=length, offset=offset)
                )

            zblock_bytes = toc_zone[entries_len:]
            width = _zblock_width(block_sz)
            if len(zblock_bytes) % width != 0:
                # The encrypted TOC zone is padded to AES block boundary; trim the
                # tail so the division is exact.
                trim = (len(zblock_bytes) // width) * width
                zblock_bytes = zblock_bytes[:trim]
            zblocks = tuple(
                int.from_bytes(zblock_bytes[i : i + width], "big")
                for i in range(0, len(zblock_bytes), width)
            )
            self._zblocks = zblocks
            self._fh = fh

            # Eagerly read entry #0 (the manifest) and decode it.
            manifest_bytes = self._extract(raw_entries[0])
            manifest_text = manifest_bytes.decode("utf-8", errors="replace")
            names = [line for line in manifest_text.split("\n") if line]
            if len(names) != n - 1:
                # Don't crash — some toolkit-built CDLC have trailing junk in the
                # manifest. Pad or truncate so we at least line up the entries.
                names = (names + [""] * (n - 1))[: n - 1]

            named: list[PsarcEntry] = [raw_entries[0]]
            for i, e in enumerate(raw_entries[1:]):
                named.append(
                    PsarcEntry(name=names[i], md5=e.md5, z_index_begin=e.z_index_begin,
                               length=e.length, offset=e.offset)
                )
            self.entries = tuple(named)
        except BaseException:
            fh.close()
            raise

    @classmethod
    def open(cls, path: Path) -> Self:
        return cls(path)

    def names(self) -> tuple[str, ...]:
        """All file paths in the archive (manifest entry #0 excluded)."""
        return tuple(e.name for e in self.entries[1:])

    def has(self, name: str) -> bool:
        return any(e.name == name for e in self.entries[1:])

    def find(self, predicate: Callable[[str], bool]) -> str | None:
        for e in self.entries[1:]:
            if predicate(e.name):
                return e.name
        return None

    def read(self, name: str) -> bytes:
        for e in self.entries[1:]:
            if e.name == name:
                return self._extract(e)
        raise FileNotFoundInPsarc(name)

    def _extract(self, entry: PsarcEntry) -> bytes:
        """Inflate the blocks of a single entry."""
        if entry.length == 0:
            return b""
        fh = self._fh
        fh.seek(entry.offset)
        out = bytearray()
        idx = entry.z_index_begin
        block_size = self.header.block_size
        while len(out) < entry.length:
            if idx >= len(self._zblocks):
                raise MalformedPsarc(
                    f"block index {idx} out of range (entry {entry.name!r})"
                )
            csize = self._zblocks[idx]
            idx += 1
            if csize == 0:
                # Full uncompressed block of block_size bytes (or remainder).
                remaining = entry.length - len(out)
                chunk = fh.read(min(block_size, remaining))
                out.extend(chunk)
                continue
            raw = fh.read(csize)
            if len(raw) < csize:
                raise MalformedPsarc("unexpected EOF while reading block")
            if raw[:1] == bytes([_ZLIB_MAGIC_HIGH]):
                try:
                    out.extend(zlib.decompress(raw))
                except zlib.error:
                    # Some toolkit blocks are not actually zlib even though the
                    # high byte matches. Fall back to raw bytes.
                    out.extend(raw)
            else:
                out.extend(raw)
        return bytes(out[: entry.length])

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help"):
        print("usage: python -m rsdlc.psarc <fichier.psarc> [--find <substr>]")
        return 1
    path = Path(args[0])
    if not path.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 2
    find_substr: str | None = None
    if len(args) >= 3 and args[1] == "--find":
        find_substr = args[2]
    try:
        with PsarcArchive.open(path) as a:
            h = a.header
            print(f"# PSARC v{h.version_major}.{h.version_minor}  "
                  f"{h.num_entries} entries  block={h.block_size}  "
                  f"encrypted={h.toc_encrypted}")
            for e in a.entries[1:]:
                if find_substr and find_substr not in e.name:
                    continue
                print(f"{e.length:>10}  {e.name}")
    except PsarcError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PsarcError",
    "MalformedPsarc",
    "UnsupportedPsarcVersion",
    "UnsupportedCompression",
    "FileNotFoundInPsarc",
    "PsarcHeader",
    "PsarcEntry",
    "PsarcArchive",
]
