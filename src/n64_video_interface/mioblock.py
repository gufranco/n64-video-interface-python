from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass

from . import lzparse, rewindow
from .bitlz import Copy, Literals, Token, expand, span, split
from .edits import NO_TARGETS, Packed, RegionEdit, Settings, edit_region

SLOT = 0x2000
BLOCK = struct.Struct(">4H")
FLAGS = struct.Struct(">I")
LINK = struct.Struct(">H")
RECORD = struct.Struct(">3I")
RECORDS = range(0x1000, 0x101000, 4)
BOOT = 0x1000
FLAG_BITS = 32
SUM_MASK = 0xFFFF
LENGTH_SHIFT = 12
DISTANCE_MASK = 0xFFF
MIN_COPY = 3
MAX_COPY = 18
WINDOW = 0x1000
CANDIDATES = 256
LITERAL_BITS = 9
COPY_BITS = 17
MAX_SIZE = 0x800000
NOUN = "MIO0 block chain"
NO_ROOM = "a changed MIO0 block chain no longer fits its slots"
SHAPE = lzparse.Shape(WINDOW, MIN_COPY, MAX_COPY, CANDIDATES, LITERAL_BITS, lambda _, __: COPY_BITS)
Unit = Literals | Copy


class FormatError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Chain:
    start: int
    end: int
    size: int

    @property
    def capacities(self) -> tuple[int, ...]:
        slots = range(self.start, self.end, SLOT)
        return tuple(min(SLOT, self.end - at) for at in slots)


def _unit(rom: bytes, flag: bool, link: int, chunk: int, produced: int) -> tuple[Unit, int, int]:
    if flag:
        return Literals(bytes((rom[chunk],))), link, chunk + 1
    value = LINK.unpack_from(rom, link)[0]
    copy = Copy((value & DISTANCE_MASK) + 1, (value >> LENGTH_SHIFT) + MIN_COPY)
    if copy.distance > produced:
        raise FormatError("a copy reaches before the start of the chain")
    return copy, link + LINK.size, chunk


def _block(rom: bytes, at: int, produced: int) -> tuple[list[Unit], int, int]:
    tag, size, link, chunk = BLOCK.unpack_from(rom, at)
    units: list[Unit] = []
    link_at, chunk_at, flags_at, made = at + link, at + chunk, at + BLOCK.size, 0
    word, left = 0, 0
    while made < size:
        if not left:
            word, left = FLAGS.unpack_from(rom, flags_at)[0], FLAG_BITS
            flags_at += FLAGS.size
        left -= 1
        flag = bool(word >> left & 1)
        unit, link_at, chunk_at = _unit(rom, flag, link_at, chunk_at, produced + made)
        units.append(unit)
        made += span(unit)
    return units, made, tag


def decode_chain(rom: bytes, start: int, end: int, size: int) -> tuple[Token, ...]:
    tokens: list[Token] = []
    produced = 0
    try:
        for at in range(start, end, SLOT):
            units, made, tag = _block(rom, at, produced)
            if sum(expand(units, expand(tokens))) & SUM_MASK != tag:
                raise FormatError("a block's sum does not match its output")
            tokens.extend(units)
            produced += made
    except (IndexError, struct.error) as error:
        raise FormatError("the chain ends before its data") from error
    if produced != size:
        raise FormatError("the chain does not decode to its size")
    return lzparse.merged(tokens)


def _pieces(length: int) -> list[int]:
    pieces: list[int] = []
    left = length
    while left > MAX_COPY:
        pieces.append(min(MAX_COPY, left - MIN_COPY))
        left -= pieces[-1]
    return [*pieces, left]


def _units(tokens: Sequence[Token]) -> list[Unit]:
    return [
        unit
        for token in tokens
        for unit in (
            [Literals(bytes((byte,))) for byte in token.data]
            if isinstance(token, Literals)
            else [Copy(token.distance, piece) for piece in _pieces(token.length)]
        )
    ]


def _block_size(count: int, copies: int) -> int:
    words = -(-count // FLAG_BITS)
    return BLOCK.size + FLAGS.size * words + LINK.size * copies + (count - copies)


def _block_bytes(units: Sequence[Unit], output: bytes) -> bytes:
    flags = [isinstance(unit, Literals) for unit in units]
    padded = flags + [False] * (-len(flags) % FLAG_BITS)
    words = b"".join(
        FLAGS.pack(
            sum(bit << (FLAG_BITS - 1 - i) for i, bit in enumerate(padded[at : at + FLAG_BITS]))
        )
        for at in range(0, len(padded), FLAG_BITS)
    )
    copies = [unit for unit in units if isinstance(unit, Copy)]
    links = b"".join(
        LINK.pack(((c.length - MIN_COPY) << LENGTH_SHIFT) | (c.distance - 1)) for c in copies
    )
    literals = b"".join(unit.data for unit in units if isinstance(unit, Literals))
    link = BLOCK.size + len(words)
    head = BLOCK.pack(sum(output) & SUM_MASK, len(output), link, link + len(links))
    return head + words + links + literals


def _fill(units: Sequence[Unit], first: int, capacity: int, reserve: int) -> int:
    end, copies = first, 0
    while end < len(units) - reserve:
        more = copies + isinstance(units[end], Copy)
        if _block_size(end + 1 - first, more) > capacity:
            break
        end, copies = end + 1, more
    return end


def pack(
    tokens: Sequence[Token], data: bytes, capacities: Sequence[int]
) -> tuple[bytes, ...] | None:
    units = _units(tokens)
    blocks: list[bytes] = []
    first, position = 0, 0
    for slot, capacity in enumerate(capacities):
        end = _fill(units, first, capacity, len(capacities) - slot - 1)
        if end == first:
            return None
        made = sum(span(unit) for unit in units[first:end])
        blocks.append(_block_bytes(units[first:end], data[position : position + made]))
        first, position = end, position + made
    return tuple(blocks) if first == len(units) else None


def cost(token: Token) -> int:
    if isinstance(token, Literals):
        return LITERAL_BITS * len(token.data)
    return COPY_BITS * len(_pieces(token.length))


def optimal(data: bytes, start: int, end: int) -> tuple[Token, ...]:
    return lzparse.optimal(data, start, end, SHAPE)


def _chain_at(rom: bytes, record: int) -> Chain | None:
    start, end, size = RECORD.unpack_from(rom, record)
    if not BOOT <= start < end <= len(rom) or not 0 < size <= MAX_SIZE:
        return None
    try:
        decode_chain(rom, start, end, size)
    except FormatError:
        return None
    return Chain(start, end, size)


def find_chains(rom: bytes) -> tuple[Chain, ...]:
    records = (record for record in RECORDS if record + RECORD.size <= len(rom))
    chains = {chain for chain in (_chain_at(rom, record) for record in records) if chain}
    return tuple(sorted(chains, key=lambda chain: chain.start))


def _edited(rom: bytes, chain: Chain, settings: Settings) -> tuple[bytes, RegionEdit] | str | None:
    tokens = decode_chain(rom, chain.start, chain.end, chain.size)
    before = expand(tokens)
    edit = edit_region(before, settings)
    if not edit.changes:
        return None
    target = sum(map(cost, tokens))
    improved = rewindow.improve(
        split(tokens, edit.data), edit.data, before, target, optimal=optimal, cost=cost
    )
    blocks = pack(improved, edit.data, chain.capacities)
    if blocks is None:
        return NO_ROOM
    padded = b"".join(
        block + bytes(capacity - len(block))
        for block, capacity in zip(blocks, chain.capacities, strict=True)
    )
    return padded, edit


def edit_chains(rom: bytes, settings: Settings) -> Packed | str:
    done: tuple[RegionEdit, ...] = ()
    out, refusal = rom, NO_TARGETS
    for chain in find_chains(rom):
        result = _edited(rom, chain, settings)
        if isinstance(result, tuple):
            out = out[: chain.start] + result[0] + out[chain.end :]
            done = (*done, result[1])
        refusal = result if isinstance(result, str) else refusal
    return Packed(out, done, len(done), NOUN) if done else refusal
