"""AES-256 CFB decryption for the encrypted PSARC TOC.

Only the TOC zone (TOC entries + zBlockSizeList) is encrypted on official Ubisoft
DLC and on CDLC produced by the Custom Song Toolkit. The individual files inside
the archive (including .hsan, which is all we care about) are NOT encrypted at
the file level for our use case, so this module is only needed to materialize
the TOC before we can decode it.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

try:  # cryptography >= 45 moved CFB under decrepit
    from cryptography.hazmat.decrepit.ciphers.modes import CFB
except ImportError:  # pragma: no cover
    from cryptography.hazmat.primitives.ciphers.modes import CFB  # noqa: F401

# Public PSARC archive key (PC). Documented in multiple open-source toolkits
# (0x0L/rs-utils, rscustom/rocksmith-custom-song-toolkit). It is the same on
# every install — Rocksmith ships it.
_ARCHIVE_KEY: bytes = bytes.fromhex(
    "C53DB23870A1A2F71CAE64061FDD0E1157309DC85204D4C5BFDF25090DF2572C"
)
_ARCHIVE_IV: bytes = bytes.fromhex("E915AA018FEF71FC508132E4BB4CEB42")

_BLOCK = 16  # AES block size in bytes


def decrypt_toc(ciphertext: bytes) -> bytes:
    """Decrypt the encrypted TOC zone of a PSARC file (AES-256 CFB, 16-byte IV).

    The TOC is padded to a multiple of 16 bytes when written; the caller is
    responsible for trimming back to the declared TOC length.
    """
    # cryptography requires the input to be a multiple of the block size in CFB
    # full-block mode. The on-disk TOC is already padded by the writer; if a
    # caller hands us a partial last block (shouldn't happen with valid files),
    # pad with zeros so we still decrypt as much as we can.
    pad = (-len(ciphertext)) % _BLOCK
    if pad:
        ciphertext = ciphertext + b"\x00" * pad
    cipher = Cipher(algorithms.AES(_ARCHIVE_KEY), CFB(_ARCHIVE_IV))
    decryptor = cipher.decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()
