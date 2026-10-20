from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from .ctrl import apply_changes, plausible
from .vifilter import BASE_LOOKBACK, LOOKBACK, VI_BASE, is_branch, last_write

OP_ADDIU, OP_ORI, OP_LUI, OP_SW = 0x09, 0x0D, 0x0F, 0x2B
REGISTER = 31
HALF = 0xFFFF
SIGN = 0x8000
UPPER = 0xFFFF0000
Change = tuple[int, int, int]

if TYPE_CHECKING:
    from .edits import Settings


def _word(data: bytes, at: int) -> int:
    return int(struct.unpack_from(">I", data, at)[0])


def _fields(word: int) -> tuple[int, int, int]:
    return word >> 26, (word >> 21) & REGISTER, (word >> 16) & REGISTER


def _based(data: bytes, store: int, base: int) -> bool:
    origin = last_write(data, store, base, BASE_LOOKBACK)
    if origin is None or _word(data, origin) != (OP_LUI << 26) | (base << 16) | VI_BASE:
        return False
    return not any(is_branch(_word(data, at)) for at in range(origin + 4, store - 4, 4))


def _source(data: bytes, store: int, value: int) -> tuple[int, ...] | None:
    at = last_write(data, store, value, LOOKBACK)
    if at is None:
        return None
    op, source, _ = _fields(_word(data, at))
    if op in (OP_ADDIU, OP_ORI) and source == 0:
        return (at,)
    high = last_write(data, at, value, LOOKBACK) if op == OP_ORI and source == value else None
    return (high, at) if high is not None and _word(data, high) >> 26 == OP_LUI else None


def _value(data: bytes, places: tuple[int, ...]) -> int:
    words = [_word(data, at) for at in places]
    return sum((word & HALF) << (16 * (len(words) - 1 - i)) for i, word in enumerate(words))


def _rewrite(data: bytes, places: tuple[int, ...], value: int) -> tuple[Change, ...] | None:
    words = [_word(data, at) for at in places]
    if len(words) == 1 and (value > HALF or (words[0] >> 26 == OP_ADDIU and value & SIGN)):
        return None
    parts = [value] if len(words) == 1 else [value >> 16, value & HALF]
    return tuple(
        (at, word, (word & UPPER) | part)
        for at, word, part in zip(places, words, parts, strict=True)
        if word & HALF != part
    )


def _constant(data: bytes, store: int, settings: Settings) -> tuple[Change, ...]:
    _, base, value = _fields(_word(data, store))
    places = _source(data, store, value) if _based(data, store, base) else None
    if places is None or not plausible(_value(data, places)):
        return ()
    changed = apply_changes(
        _value(data, places), dither_filter=settings.dither_filter, **settings.table
    )
    return _rewrite(data, places, changed) or ()


def find_constants(data: bytes, settings: Settings) -> tuple[Change, ...]:
    stores = (
        at
        for at in range(4, len(data) - 3, 4)
        if _word(data, at) >> 26 == OP_SW and _word(data, at) & HALF == 0
    )
    return tuple(change for store in stores for change in _constant(data, store, settings))
