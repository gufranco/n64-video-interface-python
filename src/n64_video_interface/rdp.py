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
SEND_DELAY = 0x8FB8000C
SHIFT_FROM_W0 = 0x00193202
LENGTH_FROM_W0 = 0x03221004
ONE = 0x20020001
MINUS_ONE = 0x2042FFFF
SHIFT_MASK = 0x00C21004
INVERT_MASK = 0x00401027
CLEAR_FIELD = 0x00431024
SET_FIELD = 0x00581825
STORE_FIELD = 0xACE30000
LOAD_HIGH_WORD = 0x8FB90008
LOAD_FIELD = 0x8CE30000
NOP = 0x00000000
F3DEX_ROUTINE = bytes.fromhex(
    "8CE30000"
    "9365FFFB"
    "9366FFFA"
    "20020001"
    "00A21004"
    "2042FFFF"
    "00C21004"
    "00401027"
    "00431024"
    "00581825"
    "ACE30000"
    "8FB90008"
)
FAST3D_ROUTINE = bytes.fromhex(
    "8CE30000"
    "2008FFFF"
    "9365FFFB"
    "9366FFFA"
    "20020001"
    "00A21004"
    "2042FFFF"
    "00C21004"
    "00481026"
    "00431024"
    "00581825"
    "ACE30000"
    "8FB90008"
)
REWRITTEN_BODY = (
    LOAD_FIELD,
    SHIFT_FROM_W0,
    ONE,
    LENGTH_FROM_W0,
    MINUS_ONE,
    SHIFT_MASK,
    INVERT_MASK,
    CLEAR_FIELD,
    SET_FIELD,
    STORE_FIELD,
    LOAD_HIGH_WORD,
)
INLINE_FAMILIES = {"F3DEX": F3DEX_ROUTINE, "Fast3D": FAST3D_ROUTINE}
IMEM_SIZE = 0x1000
HANDLER_WORDS = 3
BRANCHES = frozenset({0x01, 0x04, 0x05, 0x06, 0x07, 0x14, 0x15, 0x16, 0x17})
HALF = 0xFFFF
SIGN = 0x8000
WRAP = 0x10000


@dataclass(frozen=True, slots=True)
class InlineSite:
    at: int
    family: str


@dataclass(frozen=True, slots=True)
class DitherSite:
    math_jump: int
    setom_jump: int
    cave: int
    cave_imem: int
    rdp_jump: int


@dataclass(frozen=True, slots=True)
class HandlerSite:
    math_jump: int
    handler: int
    handler_imem: int
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
    send = _tail_jump(rom, math)
    if send is None:
        return None
    tail = math + TAIL_AFTER_MATH
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


def _tail_jump(rom: bytes, math: int) -> int | None:
    tail = math + TAIL_AFTER_MATH
    if tail + 12 > len(rom) or _word(rom, tail) != LOAD_OTHERMODE_0:
        return None
    send = _word(rom, tail + 4)
    if send >> 26 != OP_J or _word(rom, tail + 8) != LOAD_OTHERMODE_1:
        return None
    return send


def _handlers(rom: bytes, send: int, floor: int, ceiling: int) -> list[int]:
    return [
        at
        for at in range(floor, ceiling, 4)
        if _word(rom, at) == STORE_OTHERMODE_0
        and _word(rom, at + 4) == send
        and _word(rom, at + 8) == STORE_OTHERMODE_1
    ]


def _targets(word: int, at: int, base: int, page: int) -> int | None:
    if word >> 26 in (OP_J, OP_JAL):
        return (
            base + (((word & JUMP_TARGET) << 2) & IMEM_OFFSET)
            if (word << 2) & IMEM_PAGE == page
            else None
        )
    if word >> 26 in BRANCHES:
        offset = word & HALF
        return at + 4 + ((offset - WRAP) if offset & SIGN else offset) * 4
    return None


def _is_free(rom: bytes, at: int, base: int, page: int) -> bool:
    if _word(rom, at) != NOP:
        return False
    span = range(base, min(len(rom) - 3, base + IMEM_SIZE), 4)
    return all(_targets(_word(rom, word), word, base, page) != at for word in span)


def _is_forced(rom: bytes, floor: int, ceiling: int) -> bool:
    return any(
        _word(rom, at) >> 26 == OP_J and _word(rom, at + 4) & UPPER_HALF == ORI_W0
        for at in range(floor, ceiling, 4)
    )


def _handler_site(rom: bytes, math: int) -> HandlerSite | None:
    send = _tail_jump(rom, math)
    floor, ceiling = max(0, math - SEARCH_BACK), min(len(rom) - 16, math + SEARCH_BACK)
    if send is None or _is_forced(rom, floor, ceiling):
        return None
    handlers = _handlers(rom, send, floor, ceiling)
    target = (send & JUMP_TARGET) << 2
    bases = [at - (target & IMEM_OFFSET) for at in _aligned_hits(rom, RDP_SEND, floor, ceiling)]
    if len(handlers) != 1:
        return None
    handler = handlers[0]
    fitting = [
        b for b in bases if b <= min(handler, math) and max(handler, math) - b <= IMEM_OFFSET
    ]
    if len(fitting) != 1 or not _is_free(
        rom, handler + HANDLER_WORDS * 4, fitting[0], target & IMEM_PAGE
    ):
        return None
    handler_imem = ((handler - fitting[0]) & IMEM_OFFSET) | (target & IMEM_PAGE)
    return HandlerSite(math + TAIL_AFTER_MATH + 4, handler, handler_imem, send)


def find_handler_sites(rom: bytes) -> tuple[HandlerSite, ...]:
    caved = {site.math_jump for site in find_dither_sites(rom)}
    sites = (_handler_site(rom, math) for math in _aligned_hits(rom, OTHERMODE_MATH))
    return tuple(site for site in sites if site is not None and site.math_jump not in caved)


def find_inline_sites(rom: bytes) -> tuple[InlineSite, ...]:
    return tuple(
        InlineSite(at, family)
        for family, routine in INLINE_FAMILIES.items()
        for at in _aligned_hits(rom, routine)
        if at + len(routine) + 8 <= len(rom) and _word(rom, at + len(routine) + 4) == SEND_DELAY
    )


def _inline_rewrite(site: InlineSite, rom: bytes, bits: int) -> tuple[tuple[int, int, int], ...]:
    length = len(INLINE_FAMILIES[site.family]) // 4
    old = struct.unpack_from(f">{length}I", rom, site.at)
    padding = (NOP,) * (length - len(REWRITTEN_BODY) - 1)
    new = (*REWRITTEN_BODY, ORI_W0 | bits, *padding)
    return tuple(
        (site.at + index * 4, before, after)
        for index, (before, after) in enumerate(zip(old, new, strict=True))
        if before != after
    )


def count_microcodes(rom: bytes) -> int:
    return len(find_dither_sites(rom)) + len(find_handler_sites(rom)) + len(find_inline_sites(rom))


def _cave_writes(site: DitherSite, rom: bytes, bits: int) -> tuple[tuple[int, int, int], ...]:
    to_cave = (OP_J << 26) | (site.cave_imem >> 2)
    writes = (
        (site.math_jump, to_cave),
        (site.setom_jump, to_cave),
        (site.cave, site.rdp_jump),
        (site.cave + 4, ORI_W0 | bits),
    )
    return tuple((offset, _word(rom, offset), value) for offset, value in writes)


def _handler_writes(site: HandlerSite, rom: bytes, bits: int) -> tuple[tuple[int, int, int], ...]:
    writes = (
        (site.math_jump, (OP_J << 26) | (site.handler_imem >> 2)),
        (site.handler, ORI_W0 | bits),
        (site.handler + 4, STORE_OTHERMODE_0),
        (site.handler + 8, site.rdp_jump),
        (site.handler + 12, STORE_OTHERMODE_1),
    )
    return tuple((offset, _word(rom, offset), value) for offset, value in writes)


def disable_dither(
    rom: bytes, bits: int = DITHER_BITS
) -> tuple[bytes, tuple[tuple[int, int, int], ...]]:
    changes = (
        *(change for site in find_dither_sites(rom) for change in _cave_writes(site, rom, bits)),
        *(
            change
            for site in find_handler_sites(rom)
            for change in _handler_writes(site, rom, bits)
        ),
        *(change for site in find_inline_sites(rom) for change in _inline_rewrite(site, rom, bits)),
    )
    out = bytearray(rom)
    for offset, _, after in changes:
        struct.pack_into(">I", out, offset, after)
    return bytes(out), changes
