from __future__ import annotations

import struct
from dataclasses import dataclass

OTHERMODE_MATH = bytes.fromhex("3C0280000322100700190A0200221006004010270062182400781825")
LOAD_OTHERMODE_0 = 0x8C1900C8
LOAD_OTHERMODE_1 = 0x8C1800CC
STORE_OTHERMODE_0 = 0xAC1900C8
STORE_OTHERMODE_1 = 0xAC1800CC
RDP_SEND = bytes.fromhex("AEF80004AEF90000")
OP_J = 0x02
OP_JAL = 0x03
JUMP_TARGET = 0x03FFFFFF
IMEM_OFFSET = 0x0FFF
IMEM_PAGE = 0xF000
SEARCH_BACK = 0x1400
ORI_W0 = 0x37390000
DITHER_BITS = 0x00F0
OPCODE = 0xFC000000
UPPER_HALF = 0xFFFF0000
WHOLE = 0xFFFFFFFF
DMA_IO_SIGNATURE = (
    (OPCODE, OP_JAL << 26),
    (UPPER_HALF, 0x87740000),
    (WHOLE, 0x33330FF8),
    (WHOLE, 0x0014A083),
    (OPCODE, OP_J << 26),
    (UPPER_HALF, 0x201F0000),
)
TAIL_AFTER_MATH = len(OTHERMODE_MATH) + 4


@dataclass(frozen=True, slots=True)
class DitherSite:
    math_jump: int
    setom_jump: int
    cave: int
    cave_imem: int
    rdp_jump: int


def _word(rom: bytes, at: int) -> int:
    return int(struct.unpack_from(">I", rom, at)[0])


def _aligned_hits(rom: bytes, needle: bytes, start: int = 0, end: int | None = None) -> list[int]:
    stop = len(rom) if end is None else end
    hits: list[int] = []
    at = rom.find(needle, start, stop)
    while at >= 0:
        if at % 4 == 0:
            hits.append(at)
        at = rom.find(needle, at + 1, stop)
    return hits


def _is_dma_io(rom: bytes, at: int) -> bool:
    return all(
        _word(rom, at + index * 4) & mask == value
        for index, (mask, value) in enumerate(DMA_IO_SIGNATURE)
    )


def _nearest_before(candidates: list[int], limit: int) -> int | None:
    below = [at for at in candidates if at < limit]
    return max(below) if below else None


def _site(rom: bytes, math: int) -> DitherSite | None:
    tail = math + TAIL_AFTER_MATH
    send = _word(rom, tail + 4)
    if _word(rom, tail) != LOAD_OTHERMODE_0 or send >> 26 != OP_J:
        return None
    if _word(rom, tail + 8) != LOAD_OTHERMODE_1:
        return None
    floor = max(0, math - SEARCH_BACK)
    window = range(floor, math, 4)
    rdp = _nearest_before(_aligned_hits(rom, RDP_SEND, floor, math), math)
    setom = _nearest_before(
        [
            at
            for at in window
            if _word(rom, at) == STORE_OTHERMODE_0
            and _word(rom, at + 4) == send
            and _word(rom, at + 8) == STORE_OTHERMODE_1
        ],
        math,
    )
    cave = _nearest_before([at for at in window if _is_dma_io(rom, at)], math)
    if rdp is None or setom is None or cave is None:
        return None
    target = (send & JUMP_TARGET) << 2
    base = rdp - (target & IMEM_OFFSET)
    if not (0 <= setom - base <= IMEM_OFFSET and 0 <= cave - base <= IMEM_OFFSET):
        return None
    cave_imem = ((cave - base) & IMEM_OFFSET) | (target & IMEM_PAGE)
    return DitherSite(tail + 4, setom + 4, cave, cave_imem, send)


def find_dither_sites(rom: bytes) -> tuple[DitherSite, ...]:
    sites = (_site(rom, math) for math in _aligned_hits(rom, OTHERMODE_MATH))
    return tuple(site for site in sites if site is not None)


def disable_dither(
    rom: bytes, bits: int = DITHER_BITS
) -> tuple[bytes, tuple[tuple[int, int, int], ...]]:
    out = bytearray(rom)
    changes: list[tuple[int, int, int]] = []
    for site in find_dither_sites(rom):
        to_cave = (OP_J << 26) | (site.cave_imem >> 2)
        writes = (
            (site.math_jump, to_cave),
            (site.setom_jump, to_cave),
            (site.cave, site.rdp_jump),
            (site.cave + 4, ORI_W0 | bits),
        )
        for offset, value in writes:
            changes.append((offset, _word(rom, offset), value))
            struct.pack_into(">I", out, offset, value)
    return bytes(out), tuple(changes)
