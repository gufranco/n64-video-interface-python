from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .ctrl import CTRL_AA_MASK, CTRL_DIVOT, CTRL_GAMMA, CTRL_GAMMA_DITHER, CTRL_TYPE_MASK

VI_BASE = 0xA440
CONTROL_FIELD = 0x000C
LOOKBACK = 16
HALF = 0xFFFF
SIGN = 0x8000
WRAP = 0x10000
REACH = 0x7FFF
REGISTER = 31
OP_REGIMM, OP_J, OP_JAL, OP_BEQ = 0x01, 0x02, 0x03, 0x04
BRANCHES = frozenset({OP_REGIMM, OP_J, OP_JAL, 0x04, 0x05, 0x06, 0x07, 0x14, 0x15, 0x16, 0x17})
JUMP_REGISTER = frozenset({0x08, 0x09})
OP_ANDI, OP_LUI, OP_LW, OP_SW = 0x0C, 0x0F, 0x23, 0x2B
WRITES_RT = frozenset({*range(0x08, 0x10), *range(0x20, 0x28)})
FUNCT_OR = 0x25
AA_SHIFT = 8
ALWAYS = OP_BEQ << 10
Change = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class Site:
    store: int
    value: int
    base: int


@dataclass(frozen=True, slots=True)
class Mask:
    keep: int
    point_sample: bool

    @property
    def words(self) -> int:
        return 7 if self.point_sample else 3


def _word(data: bytes, at: int) -> int:
    return int(struct.unpack_from(">I", data, at)[0])


def _is_branch(word: int) -> bool:
    return word >> 26 in BRANCHES or (word >> 26 == 0 and word & 0x3F in JUMP_REGISTER)


def _written(word: int) -> int | None:
    op = word >> 26
    if op in WRITES_RT:
        return (word >> 16) & REGISTER
    if op == 0 and word & 0x3F not in JUMP_REGISTER:
        return (word >> 11) & REGISTER
    return REGISTER if op == OP_JAL else None


def _last_write(data: bytes, at: int, register: int) -> int | None:
    for before in range(at - 4, max(-4, at - 4 * LOOKBACK), -4):
        if _written(_word(data, before)) == register:
            return _word(data, before)
    return None


def _site_at(data: bytes, store: int) -> Site | None:
    word = _word(data, store)
    value, base = (word >> 16) & REGISTER, (word >> 21) & REGISTER
    after = _word(data, store + 4)
    if _is_branch(_word(data, store - 4)) or _is_branch(after) or _written(after) in (value, base):
        return None
    load, origin = _last_write(data, store, value), _last_write(data, store, base)
    if load is None or load >> 26 != OP_LW or load & HALF != CONTROL_FIELD:
        return None
    return Site(store, value, base) if origin == (OP_LUI << 26) | (base << 16) | VI_BASE else None


def find_sites(data: bytes) -> tuple[Site, ...]:
    stores = (
        at
        for at in range(4, len(data) - 8, 4)
        if _word(data, at) >> 26 == OP_SW and _word(data, at) & HALF == 0
    )
    sites = (_site_at(data, at) for at in stores)
    return tuple(site for site in sites if site is not None)


class Requested(Protocol):
    @property
    def antialiasing(self) -> bool | None: ...
    @property
    def divot(self) -> bool | None: ...
    @property
    def gamma_dither(self) -> bool | None: ...
    @property
    def gamma(self) -> bool | None: ...
    @property
    def dither_filter(self) -> bool | None: ...


def mask_for(settings: Requested) -> Mask | None:
    wanted = (
        (settings.divot, CTRL_DIVOT),
        (settings.gamma_dither, CTRL_GAMMA_DITHER),
        (settings.gamma, CTRL_GAMMA),
    )
    cleared = sum(bit for setting, bit in wanted if setting is False)
    point_sample = settings.antialiasing is False
    keep = HALF & ~cleared & ~(CTRL_AA_MASK if point_sample else 0)
    return Mask(keep, point_sample) if settings.dither_filter is False else None


def branch_target(at: int, word: int) -> int:
    offset = word & HALF
    return at + 4 + ((offset - WRAP) if offset & SIGN else offset) * 4


def _branch(at: int, target: int) -> int:
    return (OP_BEQ << 26) | (((target - at - 4) >> 2) & HALF)


def _filter(site: Site, mask: Mask, cave: int) -> tuple[int, ...]:
    clear = (OP_ANDI << 26) | (site.value << 21) | (site.value << 16) | mask.keep
    store = (OP_SW << 26) | (site.base << 21) | (site.value << 16)
    if not mask.point_sample:
        return clear, _branch(cave + 4, site.store + 8), store
    pixel = (OP_ANDI << 26) | (site.value << 21) | (site.base << 16) | CTRL_TYPE_MASK
    shift = (site.base << 16) | (site.base << 11) | (AA_SHIFT << 6)
    merge = (site.value << 21) | (site.base << 16) | (site.value << 11) | FUNCT_OR
    restore = (OP_LUI << 26) | (site.base << 16) | VI_BASE
    return clear, pixel, shift, merge, restore, _branch(cave + 20, site.store + 8), store


def _reachable(site: Site, cave: int) -> bool:
    return abs((cave - site.store - 4) >> 2) <= REACH and abs((site.store + 8 - cave) >> 2) <= REACH


def _writes(site: Site, mask: Mask, cave: int) -> list[tuple[int, int]]:
    body = enumerate(_filter(site, mask, cave))
    return [
        (site.store, _branch(site.store, cave)),
        *((cave + 4 * index, value) for index, value in body),
    ]


def install(
    data: bytes, caves: Sequence[tuple[int, int]], mask: Mask | None
) -> tuple[bytes, tuple[Change, ...]]:
    if mask is None:
        return data, ()
    usable = [start for start, end in caves if (end - start) // 4 >= mask.words]
    pairs = zip(find_sites(data), usable, strict=False)
    writes = [
        write
        for site, cave in pairs
        if _reachable(site, cave)
        for write in _writes(site, mask, cave)
    ]
    out = bytearray(data)
    changes = tuple((offset, _word(data, offset), value) for offset, value in writes)
    for offset, value in writes:
        struct.pack_into(">I", out, offset, value)
    return bytes(out), changes
