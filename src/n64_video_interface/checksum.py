"""The N64 cartridge checksum, recovered per CIC variant.

Every retail cartridge stores two check values in its header. Recomputing them
from the data proves the dump is intact, and because each lockout chip seeds and
combines the calculation differently, the chip that produces a match is also the
chip the cartridge carries. That is how `verify` recovers the CIC without any
external database.

Two details cost real time to find and are easy to lose in a rewrite. The 6105
variant reads a rolling value out of the boot table at 0x750 instead of folding
the running sum, and the final combine is exclusive or for most chips, addition
for 6103 and multiplication for 6106. An implementation with only one of those
branches validates some titles and silently fails others.

`verify` answers a narrower question than it appears to. It names the chip whose
seed reproduces the stored values, so it goes quiet on any dump whose header no
longer agrees with its data, and it cannot separate 6101 from 6102 at all because
those two seed identically. `boot_chip` answers the chip on its own terms, by the
boot code the cartridge carries, which is chip-specific and sits below the range
the checksum covers.
"""

from __future__ import annotations

import struct
import zlib

MASK = 0xFFFFFFFF
START = 0x1000
LENGTH = 0x100000
BOOT_TABLE = 0x750

SEEDS = {
    "6101": 0xF8CA4DDC,
    "6102": 0xF8CA4DDC,
    "7102": 0xF8CA4DDC,
    "6103": 0xA3886759,
    "6105": 0xDF26F436,
    "6106": 0x1FEA617A,
}

TRY_ORDER = ("6102", "6105", "6103", "6106", "6101")

IPL3_START = 0x40
IPL3_END = 0x1000

IPL3_CRC32 = {
    0x6170A4A1: "6101",
    0x009E9EA3: "7102",
    0x90BB6CB5: "6102",
    0x0B050EE0: "6103",
    0x98BC2C86: "6105",
    0xACC8580A: "6106",
}


def boot_chip(rom: bytes) -> str | None:
    """The lockout chip named by the boot code, or None for code not in the table.

    The boot code runs before the game does and differs per chip, so it identifies
    the cartridge even when the header has been edited and the checksum no longer
    agrees with the data. It is also the only way to tell 6101 from 6102, which
    share a checksum seed and are therefore indistinguishable to `verify`.

    PAL cartridges pair 7101 with the same boot code as NTSC 6102, so that entry
    reports 6102 for both. The rest of this project treats the two as one chip.
    """
    if len(rom) < IPL3_END:
        return None
    return IPL3_CRC32.get(zlib.crc32(rom[IPL3_START:IPL3_END]) & MASK)


def _rol(value: int, bits: int) -> int:
    bits &= 31
    return ((value << bits) | (value >> (32 - bits))) & MASK


def compute(rom: bytes, cic: str) -> tuple[int, int] | None:
    seed = SEEDS[cic]
    if len(rom) < START + LENGTH:
        return None

    t1 = t2 = t3 = t4 = t5 = t6 = seed
    unpack = struct.unpack_from
    uses_boot_table = cic == "6105"

    for i in range(START, START + LENGTH, 4):
        d = unpack(">I", rom, i)[0]
        carried = (t6 + d) & MASK
        if carried < t6:
            t4 = (t4 + 1) & MASK
        t6 = carried
        t3 ^= d
        rotated = _rol(d, d & 0x1F)
        t5 = (t5 + rotated) & MASK
        t2 ^= rotated if t2 > d else (t6 ^ d)
        if uses_boot_table:
            t1 = (t1 + (unpack(">I", rom, BOOT_TABLE + (i & 0xFF))[0] ^ d)) & MASK
        else:
            t1 = (t1 + (t5 ^ d)) & MASK

    if cic == "6103":
        return ((t6 ^ t4) + t3) & MASK, ((t5 ^ t2) + t1) & MASK
    if cic == "6106":
        return ((t6 * t4) + t3) & MASK, ((t5 * t2) + t1) & MASK
    return (t6 ^ t4 ^ t3) & MASK, (t5 ^ t2 ^ t1) & MASK


def verify(rom: bytes) -> tuple[bool, str | None]:
    """Return whether the dump is intact and, when it is, the CIC that matched."""
    if len(rom) < START + LENGTH:
        return False, None
    stored = (
        struct.unpack_from(">I", rom, 0x10)[0],
        struct.unpack_from(">I", rom, 0x14)[0],
    )
    for cic in TRY_ORDER:
        if compute(rom, cic) == stored:
            return True, cic
    return False, None
