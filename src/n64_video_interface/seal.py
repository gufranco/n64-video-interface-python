from __future__ import annotations

import struct

from .byteorder import swap, to_big_endian
from .checksum import CRC_WORDS_AT, END, boot_chip, compute

UNKNOWN_BOOT_CHIP = "the boot code names no known chip, so the seal is unknown"


class SealError(ValueError):
    pass


def reseal(rom: bytes, cic: str | None = None) -> bytes:
    big, order = to_big_endian(rom)
    if len(big) < END:
        raise SealError(f"need {END} bytes to checksum, got {len(big)}")
    chip = cic or boot_chip(big)
    if chip is None:
        raise SealError(UNKNOWN_BOOT_CHIP)
    sealed = bytearray(big)
    struct.pack_into(">2I", sealed, CRC_WORDS_AT, *compute(big, chip))
    return swap(bytes(sealed), order)
