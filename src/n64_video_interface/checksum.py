from __future__ import annotations

import struct
import zlib

MASK = 0xFFFFFFFF
START = 0x1000
LENGTH = 0x100000
END = START + LENGTH
BOOT_TABLE = 0x750
BOOT_TABLE_SPAN = 0xFF
ROTATE_BITS = 0x1F
WORD_BITS = 32
CRC_WORDS_AT = 0x10

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
    if len(rom) < IPL3_END:
        return None
    return IPL3_CRC32.get(zlib.crc32(rom[IPL3_START:IPL3_END]) & MASK)


def _rotate_left(value: int, bits: int) -> int:
    bits &= ROTATE_BITS
    return ((value << bits) | (value >> (WORD_BITS - bits))) & MASK


def _accumulate(rom: bytes, seed: int, folds_boot_table: bool) -> tuple[int, ...]:
    t1 = t2 = t3 = t4 = t5 = t6 = seed
    words = struct.unpack_from(f">{LENGTH // 4}I", rom, START)
    for index, word in enumerate(words):
        carried = (t6 + word) & MASK
        t4 = (t4 + (carried < t6)) & MASK
        t6 = carried
        t3 ^= word
        rotated = _rotate_left(word, word & ROTATE_BITS)
        t5 = (t5 + rotated) & MASK
        t2 ^= rotated if t2 >= word else (t6 ^ word)
        if folds_boot_table:
            at = BOOT_TABLE + ((START + index * 4) & BOOT_TABLE_SPAN)
            t1 = (t1 + (struct.unpack_from(">I", rom, at)[0] ^ word)) & MASK
        else:
            t1 = (t1 + (t5 ^ word)) & MASK
    return t1, t2, t3, t4, t5, t6


def compute(rom: bytes, cic: str) -> tuple[int, int]:
    if len(rom) < END:
        raise ValueError(f"need {END} bytes to checksum, got {len(rom)}")
    t1, t2, t3, t4, t5, t6 = _accumulate(rom, SEEDS[cic], cic == "6105")
    if cic == "6103":
        return ((t6 ^ t4) + t3) & MASK, ((t5 ^ t2) + t1) & MASK
    if cic == "6106":
        return ((t6 * t4) + t3) & MASK, ((t5 * t2) + t1) & MASK
    return (t6 ^ t4 ^ t3) & MASK, (t5 ^ t2 ^ t1) & MASK


def verify(rom: bytes) -> tuple[bool, str | None]:
    if len(rom) < END:
        return False, None
    stored = struct.unpack_from(">2I", rom, CRC_WORDS_AT)
    for cic in TRY_ORDER:
        if compute(rom, cic) == stored:
            return True, cic
    return False, None
