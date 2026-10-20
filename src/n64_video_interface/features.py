from __future__ import annotations

import bisect
import struct
from dataclasses import dataclass

from .ctrl import CTRL_DIVOT, CTRL_GAMMA, CTRL_GAMMA_DITHER

MASK_CONSTANTS = (
    ("gamma", 0xFFF7),
    ("gamma_dither", 0xFFFB),
    ("divot", 0xFFEF),
    ("antialias", 0xFCFF),
)
MASK_SEARCH_WINDOW = 256
OP_ADDIU = 0x09
OP_BEQ = 0x04
OP_BEQL = 0x14
OP_ANDI = 0x0C
OP_ORI = 0x0D
OP_LUI = 0x0F
IMMEDIATE_OFFSET = 2
LUI_DITHER_FILTER_UPPER = 0x0001
SEARCH_BACK = 0x100
SEARCH_FORWARD = 0x80
REGISTER = 31
HALF = 0xFFFF
SIGN = 0x8000
HAL_DEDITHER = 0x4000


@dataclass(frozen=True, slots=True)
class SpecialFeaturesSite:
    offset: int
    masks: dict[str, int]


@dataclass(frozen=True, slots=True)
class SwitchSpec:
    flag: int
    opcode: int
    immediate: int
    alternate: int | None = None


SWITCHES = {
    "dedither": SwitchSpec(
        flag=0x0040, opcode=OP_LUI, immediate=LUI_DITHER_FILTER_UPPER, alternate=HAL_DEDITHER
    ),
    "divot": SwitchSpec(flag=0x0010, opcode=OP_ORI, immediate=CTRL_DIVOT),
    "gamma dither": SwitchSpec(flag=0x0004, opcode=OP_ORI, immediate=CTRL_GAMMA_DITHER),
    "gamma": SwitchSpec(flag=0x0001, opcode=OP_ORI, immediate=CTRL_GAMMA),
}


@dataclass(frozen=True, slots=True)
class SwitchRequest:
    switch: str
    setter: int
    branch: int
    guard: int
    flag: int


def _word(rom: bytes, at: int) -> int:
    return int(struct.unpack_from(">I", rom, at)[0])


def _addiu_offsets(rom: bytes, immediate: int) -> list[int]:
    needle = immediate.to_bytes(2, "big")
    found: list[int] = []
    at = rom.find(needle, IMMEDIATE_OFFSET)
    while at >= 0:
        word = at - IMMEDIATE_OFFSET
        if word % 4 == 0 and rom[word] >> 2 == OP_ADDIU:
            found.append(word)
        at = rom.find(needle, at + 1)
    return found


def _next_within(offsets: list[int], after: int, before: int) -> int | None:
    index = bisect.bisect_right(offsets, after)
    return offsets[index] if index < len(offsets) and offsets[index] < before else None


def _site_at(rom: bytes, offset: int, found: dict[int, list[int]]) -> SpecialFeaturesSite | None:
    masks = {MASK_CONSTANTS[0][0]: offset}
    cursor = offset
    for name, immediate in MASK_CONSTANTS[1:]:
        hit = _next_within(found[immediate], cursor, min(len(rom) - 3, cursor + MASK_SEARCH_WINDOW))
        if hit is None:
            return None
        masks[name] = hit
        cursor = hit
    return SpecialFeaturesSite(offset, masks)


def find_special_features(rom: bytes) -> tuple[SpecialFeaturesSite, ...]:
    found = {immediate: _addiu_offsets(rom, immediate) for _, immediate in MASK_CONSTANTS}
    candidates = (_site_at(rom, offset, found) for offset in found[MASK_CONSTANTS[0][1]])
    return tuple(site for site in candidates if site is not None)


def _sets_bit(word: int, spec: SwitchSpec) -> bool:
    if word >> 26 != spec.opcode or word & HALF != spec.immediate:
        return False
    return spec.opcode != OP_LUI or (word >> 21) & REGISTER == 0


def _branch_target(branch: int, word: int) -> int:
    immediate = word & HALF
    return branch + 4 + (immediate - 0x10000 if immediate & SIGN else immediate) * 4


def _andi_before(rom: bytes, branch: int, register: int, floor: int) -> tuple[int, int] | None:
    for guard in range(branch - 4, floor, -4):
        word = _word(rom, guard)
        if word >> 26 == OP_ANDI and (word >> 16) & REGISTER == register:
            return guard, word & HALF
    return None


def _guard_for(rom: bytes, setter: int, floor: int) -> tuple[int, int, int] | None:
    for branch in range(setter - 4, floor, -4):
        word = _word(rom, branch)
        if word >> 26 not in (OP_BEQ, OP_BEQL) or (word >> 16) & REGISTER != 0:
            continue
        if _branch_target(branch, word) <= setter:
            continue
        andi = _andi_before(rom, branch, (word >> 21) & REGISTER, floor)
        return None if andi is None else (branch, *andi)
    return None


def _requests_in(rom: bytes, site: SpecialFeaturesSite, switch: str) -> list[SwitchRequest]:
    spec = SWITCHES[switch]
    floor = max(0, site.offset - SEARCH_BACK)
    ceiling = min(len(rom) - 4, site.masks["antialias"] + SEARCH_FORWARD)
    found: list[SwitchRequest] = []
    for setter in range(floor, ceiling, 4):
        if not _sets_bit(_word(rom, setter), spec):
            continue
        guard = _guard_for(rom, setter, floor)
        if guard is not None and guard[2] in (spec.flag, spec.alternate):
            found.append(SwitchRequest(switch, setter, guard[0], guard[1], guard[2]))
    return found


def find_switch_requests(rom: bytes, switch: str) -> tuple[SwitchRequest, ...]:
    return tuple(
        request
        for site in find_special_features(rom)
        for request in _requests_in(rom, site, switch)
    )
