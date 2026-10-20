from __future__ import annotations

import itertools
import struct
import zlib
from collections.abc import Iterator
from dataclasses import dataclass

from .edits import NO_TARGETS, Packed, RegionEdit, Settings, edit_region
from .rarezip import deflate

UNITS = struct.Struct(">H")
WORD = struct.Struct(">I")
UNIT_SHIFT = 4
RAW_DEFLATE = -15
BOOT = 0x1000
STUB_END = 0x11000
PAIR_REACH = 8
OP_LUI, OP_ADDIU = 0x0F, 0x09
REGISTER = 31
HALF = 0xFFFF
SIGN = 0x8000
WRAP = 0x10000
LEFTOVER = 16
MAX_CHAIN = 0x400000
NOUN = "deflate chain"
NO_ROOM = "a changed deflate chain no longer fits its range"


@dataclass(frozen=True, slots=True)
class Stream:
    at: int
    data: bytes
    length: int


@dataclass(frozen=True, slots=True)
class Chain:
    start: int
    end: int
    streams: tuple[Stream, ...]


def _stream_at(rom: bytes, at: int, end: int) -> Stream | None:
    size = UNITS.unpack_from(rom, at)[0] << UNIT_SHIFT
    if not size:
        return None
    inflater = zlib.decompressobj(RAW_DEFLATE)
    window = rom[at + UNITS.size : end]
    try:
        data = inflater.decompress(window, size + 1)
    except zlib.error:
        return None
    if not inflater.eof or len(data) != size:
        return None
    return Stream(at, data, UNITS.size + len(window) - len(inflater.unused_data))


def _chain_at(rom: bytes, start: int, end: int) -> Chain | None:
    streams: list[Stream] = []
    at = start
    while end - at > LEFTOVER:
        stream = _stream_at(rom, at, end)
        if stream is None:
            return None
        streams.append(stream)
        at += stream.length
    return Chain(start, end, tuple(streams)) if streams else None


def _values(rom: bytes) -> Iterator[tuple[int, int]]:
    for at in range(BOOT, min(len(rom), STUB_END) - 4, 4):
        word = WORD.unpack_from(rom, at)[0]
        if word >> 26 != OP_LUI:
            continue
        register, high = (word >> 16) & REGISTER, word & HALF
        for here in range(at + 4, min(len(rom) - 3, at + 4 * PAIR_REACH), 4):
            low = WORD.unpack_from(rom, here)[0]
            if low >> 26 == OP_ADDIU and (low >> 21) & REGISTER == register:
                value = low & HALF
                yield here, (high << 16) + (value - WRAP if value & SIGN else value)
                break


def find_chains(rom: bytes) -> tuple[Chain, ...]:
    values = list(_values(rom))
    ranges = {
        (first, second)
        for (at, first), (there, second) in itertools.pairwise(values)
        if there - at <= 4 * PAIR_REACH
        and BOOT <= first < second <= len(rom)
        and second - first <= MAX_CHAIN
    }
    chains = (_chain_at(rom, start, end) for start, end in sorted(ranges))
    return tuple(chain for chain in chains if chain is not None)


def _edited(
    rom: bytes, chain: Chain, settings: Settings
) -> tuple[bytes, list[RegionEdit]] | str | None:
    edits = [edit_region(stream.data, settings) for stream in chain.streams]
    if not any(edit.changes for edit in edits):
        return None
    body = b"".join(UNITS.pack(len(edit.data) >> UNIT_SHIFT) + deflate(edit.data) for edit in edits)
    span = chain.end - chain.start
    if len(body) > span:
        return NO_ROOM
    return body + bytes(span - len(body)), edits


def edit_chains(rom: bytes, settings: Settings) -> Packed | str:
    done: tuple[RegionEdit, ...] = ()
    spans: tuple[tuple[int, int], ...] = ()
    out, refusal, count = rom, NO_TARGETS, 0
    for chain in find_chains(rom):
        result = _edited(rom, chain, settings)
        if isinstance(result, tuple):
            out = out[: chain.start] + result[0] + out[chain.end :]
            spans = (*spans, (chain.start, chain.end))
            done, count = (*done, *result[1]), count + 1
        refusal = result if isinstance(result, str) else refusal
    return Packed(out, done, count, NOUN, spans) if count else refusal
