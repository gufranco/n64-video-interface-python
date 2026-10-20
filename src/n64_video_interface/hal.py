from __future__ import annotations

import struct
from collections.abc import Collection

DEDITHER = 0x10000
REACH = 4
REGISTER = 31
HALF = 0xFFFF
UPPER = 0xFFFF0000
OP_ANDI, OP_ORI, OP_LUI, OP_LW, OP_SW, OP_ADDIU, OP_BEQ = 0x0C, 0x0D, 0x0F, 0x23, 0x2B, 0x09, 0x04
FUNCT_OR = 0x25
AA_NEEDED = 0x0100
AA_RESAMPLE = 0x0200
ALWAYS_SKIP_ONE = 0x10000001
Change = tuple[int, int, int]


def _word(data: bytes, at: int) -> int:
    return int(struct.unpack_from(">I", data, at)[0])


def _fields(word: int) -> tuple[int, int, int]:
    return word >> 26, (word >> 21) & REGISTER, (word >> 16) & REGISTER


def _loads(word: int, register: int) -> tuple[int, int] | None:
    op, base, target = _fields(word)
    return (base, word & HALF) if op == OP_LW and target == register else None


def _stores(word: int, register: int) -> tuple[int, int] | None:
    op, base, source = _fields(word)
    return (base, word & HALF) if op == OP_SW and source == register else None


def _read_modify_write(data: bytes, at: int, source: int, result: int) -> bool:
    before = {
        found
        for here in range(max(0, at - 4 * REACH), at, 4)
        if (found := _loads(_word(data, here), source)) is not None
    }
    after = {
        found
        for here in range(at + 4, min(len(data) - 3, at + 4 * (REACH + 1)), 4)
        if (found := _stores(_word(data, here), result)) is not None
    }
    return bool(before & after)


def _or_with(data: bytes, at: int, register: int) -> tuple[int, int] | None:
    for here in range(at + 4, min(len(data) - 3, at + 4 * REACH), 4):
        word = _word(data, here)
        if word >> 26 == 0 and word & 0x3F == FUNCT_OR and (word >> 16) & REGISTER == register:
            return (word >> 21) & REGISTER, (word >> 11) & REGISTER
    return None


def _setter(data: bytes, at: int, bits: Collection[int]) -> Change | None:
    word = _word(data, at)
    op, source, result = _fields(word)
    if op == OP_ORI and word & HALF in bits and _read_modify_write(data, at, source, result):
        return at, word, word & UPPER
    if op != OP_LUI or word & HALF != 1 or DEDITHER not in bits:
        return None
    merged = _or_with(data, at, result)
    if merged is None or not _read_modify_write(data, at, *merged):
        return None
    return at, word, word & UPPER


def find_setters(data: bytes, window: range, bits: Collection[int]) -> tuple[Change, ...]:
    candidates = (_setter(data, at, bits) for at in window if at % 4 == 0 and at + 4 <= len(data))
    return tuple(change for change in candidates if change is not None)


def _aa_at(data: bytes, at: int) -> tuple[Change, ...]:
    test, branch, needed, skip, cleared = struct.unpack_from(">5I", data, at)
    flag = (test >> 16) & REGISTER
    target = (needed >> 16) & REGISTER
    shaped = (
        test >> 26 == OP_ANDI
        and test & HALF == 1
        and branch >> 16 == (OP_BEQ << 10) | (flag << 5)
        and needed == (OP_ADDIU << 26) | (target << 16) | AA_NEEDED
        and skip == ALWAYS_SKIP_ONE
        and cleared == (target << 11) | FUNCT_OR
    )
    if not shaped:
        return ()
    resample = (OP_ADDIU << 26) | (target << 16) | AA_RESAMPLE
    return (at + 8, needed, resample), (at + 16, cleared, resample)


def find_aa_rebuild(data: bytes, window: range) -> tuple[Change, ...]:
    return tuple(
        change
        for at in window
        if at % 4 == 0 and at + 20 <= len(data)
        for change in _aa_at(data, at)
    )
