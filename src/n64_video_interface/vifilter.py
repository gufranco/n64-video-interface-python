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
OP_ORI = 0x0D
POINT_SAMPLE = 0x0200
NOP = 0
TARGET_REACH = 0x8000
Change = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class Site:
    store: int
    value: int
    base: int
    moved: int | None = None
    load: int | None = None


@dataclass(frozen=True, slots=True)
class Mask:
    keep: int
    point_sample: bool


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
            return before
    return None


def _targeted(data: bytes, at: int) -> bool:
    window = range(max(0, at - TARGET_REACH), min(len(data) - 3, at + TARGET_REACH), 4)
    return any(
        _is_branch(_word(data, source))
        and _word(data, source) >> 26 not in (OP_J, OP_JAL, 0)
        and branch_target(source, _word(data, source)) == at
        for source in window
    )


def _reads(word: int, register: int) -> bool:
    return register in ((word >> 21) & REGISTER, (word >> 16) & REGISTER)


def _feeds(data: bytes, store: int, value: int, base: int) -> int | None:
    load, origin = _last_write(data, store, value), _last_write(data, store, base)
    if load is None or origin is None:
        return None
    loaded, based = _word(data, load), _word(data, origin)
    if loaded >> 26 != OP_LW or loaded & HALF != CONTROL_FIELD:
        return None
    return load if based == (OP_LUI << 26) | (base << 16) | VI_BASE else None


def _load_site(data: bytes, store: int, value: int, base: int, load: int) -> Site | None:
    source = (_word(data, load) >> 21) & REGISTER
    after = _word(data, load + 4)
    if _is_branch(_word(data, load - 4)) or _is_branch(after) or _targeted(data, load):
        return None
    if _reads(after, value) or _written(after) in (value, source):
        return None
    return Site(store, value, base, load=load)


def _site_at(data: bytes, store: int) -> Site | None:
    word = _word(data, store)
    value, base = (word >> 16) & REGISTER, (word >> 21) & REGISTER
    load = _feeds(data, store, value, base)
    if load is None:
        return None
    after = _word(data, store + 4)
    move = _written(after) in (value, base)
    unsafe = _is_branch(_word(data, store - 4)) or _is_branch(after) or _targeted(data, store)
    if unsafe or (move and (_reads(after, value) or _targeted(data, store + 4))):
        return _load_site(data, store, value, base, load)
    return Site(store, value, base, after if move else None)


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


def _tail(site: Site, at: int, store: int) -> tuple[int, ...]:
    if site.moved is None:
        return _branch(at, site.store + 8), store
    return store, _branch(at + 4, site.store + 8), site.moved


def _forms(site: Site, mask: Mask, cave: int, data: bytes) -> list[tuple[int, ...]]:
    clear = (OP_ANDI << 26) | (site.value << 21) | (site.value << 16) | mask.keep
    resample = (OP_ORI << 26) | (site.value << 21) | (site.value << 16) | POINT_SAMPLE
    if site.load is not None:
        finish = resample if mask.point_sample else NOP
        return [(_word(data, site.load), clear, _branch(cave + 8, site.load + 8), finish)]
    store = (OP_SW << 26) | (site.base << 21) | (site.value << 16)
    if not mask.point_sample:
        return [(clear, *_tail(site, cave + 4, store))]
    pixel = (OP_ANDI << 26) | (site.value << 21) | (site.base << 16) | CTRL_TYPE_MASK
    shift = (site.base << 16) | (site.base << 11) | (AA_SHIFT << 6)
    merge = (site.value << 21) | (site.base << 16) | (site.value << 11) | FUNCT_OR
    restore = (OP_LUI << 26) | (site.base << 16) | VI_BASE
    return [
        (clear, pixel, shift, merge, restore, *_tail(site, cave + 20, store)),
        (clear, resample, *_tail(site, cave + 8, store)),
    ]


def _reachable(site: Site, cave: int) -> bool:
    entry = site.store if site.load is None else site.load
    return abs((cave - entry - 4) >> 2) <= REACH and abs((entry + 8 - cave) >> 2) <= REACH


def _writes(site: Site, body: tuple[int, ...], cave: int) -> list[tuple[int, int]]:
    if site.load is not None:
        return [
            (site.load, _branch(site.load, cave)),
            *((cave + 4 * i, v) for i, v in enumerate(body)),
        ]
    entry = [(site.store, _branch(site.store, cave))]
    entry.extend([(site.store + 4, NOP)] if site.moved is not None else [])
    return [*entry, *((cave + 4 * index, value) for index, value in enumerate(body))]


def _plan(
    data: bytes, sites: Sequence[Site], caves: Sequence[tuple[int, int]], mask: Mask
) -> list[tuple[int, int]]:
    free = list(caves)
    writes: list[tuple[int, int]] = []
    for site in sites:
        options = [
            (index, body)
            for index, (start, end) in enumerate(free)
            if _reachable(site, start)
            for body in _forms(site, mask, start, data)
            if len(body) * 4 <= end - start
        ]
        if options:
            index, body = options[0]
            writes.extend(_writes(site, body, free.pop(index)[0]))
    return writes


def install(
    data: bytes, caves: Sequence[tuple[int, int]], mask: Mask | None
) -> tuple[bytes, tuple[Change, ...]]:
    if mask is None:
        return data, ()
    unreached = [c for c in caves if not any(_targeted(data, at) for at in range(c[0], c[1], 4))]
    writes = _plan(data, find_sites(data), unreached, mask)
    out = bytearray(data)
    changes = tuple((offset, _word(data, offset), value) for offset, value in writes)
    for offset, value in writes:
        struct.pack_into(">I", out, offset, value)
    return bytes(out), changes
