from __future__ import annotations

import heapq
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import accumulate

HEADER = 18
KEY_BITS = 2
COUNT_BITS = 16
TABLE_SIZE_BITS = 5
LENGTH_BITS = 4
LONGEST_CODE = 15
SYMBOLS = 16
WORD = 2
LOOKAHEAD = 4
HALF_WORD_BITS = 8
WORD_BITS = 16
BYTE_BITS = 8
MIN_MATCH = 2
UNKNOWN_CODE = 40
Tables = tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]


class CodecError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Sub:
    literals: bytes
    distance: int = 0
    length: int = 0


@dataclass(frozen=True, slots=True)
class Chunk:
    tables: Tables
    subs: tuple[Sub, ...]

    @property
    def size(self) -> int:
        return sum(len(sub.literals) + sub.length for sub in self.subs)


def symbol(value: int) -> int:
    return value if value < MIN_MATCH else value.bit_length()


def mirror(value: int, width: int) -> int:
    return int(f"{value:0{width}b}"[::-1], 2)


def codes(lengths: Sequence[int]) -> dict[int, tuple[int, int]]:
    ordered = sorted((width, index) for index, width in enumerate(lengths) if width)
    table: dict[int, tuple[int, int]] = {}
    code, previous = 0, 1
    for width, index in ordered:
        code <<= width - previous
        table[index] = (mirror(code, width), width)
        code, previous = code + 1, width
    return table


class Reader:
    def __init__(self, data: bytes, at: int) -> None:
        self.data = data + bytes(LOOKAHEAD)
        self.pos = at
        self.start = at
        self.produced = 0
        self.margin = -len(data)
        self.chunks: tuple[Chunk, ...] = ()
        self.buffer = self._word32(at)
        self.count = WORD_BITS

    def _word32(self, at: int) -> int:
        return int.from_bytes(self.data[at : at + LOOKAHEAD], "little")

    def advance(self, bits: int) -> None:
        self.buffer >>= bits
        self.count -= bits
        if self.count < WORD_BITS:
            self.pos += WORD
            self.buffer |= self._word32(self.pos) << self.count
            self.count += WORD_BITS

    def read(self, bits: int) -> int:
        value = self.buffer & ((1 << bits) - 1)
        self.advance(bits)
        return value

    def _ahead(self, last: int) -> None:
        self.margin = max(self.margin, last - (self.pos - self.start) - LOOKAHEAD)

    def copied(self, length: int) -> None:
        self._ahead(self.produced + length - 1)
        self.produced += length

    def literals(self, count: int) -> bytes:
        if count:
            self._ahead(self.produced)
        self.produced += count
        taken = self.data[self.pos : self.pos + count]
        self.pos += count
        if count:
            self.count -= WORD_BITS
            self.buffer &= (1 << self.count) - 1
            self.buffer |= self._word32(self.pos) << self.count
            self.count += WORD_BITS
        return taken

    def table(self) -> tuple[int, ...]:
        return tuple(self.read(LENGTH_BITS) for _ in range(self.read(TABLE_SIZE_BITS)))

    def value(self, lookup: dict[tuple[int, int], int]) -> int:
        for width in range(1, WORD_BITS + 1):
            found = lookup.get((self.buffer & ((1 << width) - 1), width))
            if found is not None:
                self.advance(width)
                return found if found < MIN_MATCH else (1 << (found - 1)) | self.read(found - 1)
        raise CodecError("a code is missing from its table")

    def chunk(self) -> Chunk:
        tables = (self.table(), self.table(), self.table())
        raw, distance, length = (_lookup(table) for table in tables)
        subs: list[Sub] = []
        for _ in range(self.read(COUNT_BITS) - 1):
            literals = self.literals(self.value(raw))
            gap = self.value(distance) + 1
            size = self.value(length) + MIN_MATCH
            self.copied(size)
            subs.append(Sub(literals, gap, size))
        return Chunk(tables, (*subs, Sub(self.literals(self.value(raw)))))


def _lookup(lengths: Sequence[int]) -> dict[tuple[int, int], int]:
    return {code: index for index, code in codes(lengths).items()}


def read_all(data: bytes, at: int, size: int) -> Reader:
    reader = Reader(data, at + HEADER)
    reader.advance(KEY_BITS)
    while reader.produced < size:
        before = reader.produced
        reader.chunks = (*reader.chunks, reader.chunk())
        if reader.produced == before:
            raise CodecError("a chunk produced nothing")
    return reader


def decode(data: bytes, at: int, size: int) -> tuple[Chunk, ...]:
    return read_all(data, at, size).chunks


def expand(chunks: Sequence[Chunk], history: bytes = b"") -> bytes:
    out = bytearray(history)
    for chunk in chunks:
        for sub in chunk.subs:
            out += sub.literals
            for _ in range(sub.length):
                out.append(out[-sub.distance])
    return bytes(out[len(history) :])


class Writer:
    def __init__(self) -> None:
        self.out = bytearray()
        self.slot = 0
        self.used = WORD_BITS

    def bits(self, value: int, width: int) -> None:
        for index in range(width):
            if self.used == WORD_BITS:
                self.slot, self.used = len(self.out), 0
                self.out += bytes(WORD)
            if (value >> index) & 1:
                self.out[self.slot + self.used // BYTE_BITS] |= 1 << (self.used % BYTE_BITS)
            self.used += 1

    def value(self, table: dict[int, tuple[int, int]], value: int) -> None:
        code, width = table[symbol(value)]
        self.bits(code, width)
        self.bits(value, symbol(value) - 1 if value >= MIN_MATCH else 0)

    def chunk(self, chunk: Chunk) -> None:
        for table in chunk.tables:
            self.bits(len(table), TABLE_SIZE_BITS)
            for width in table:
                self.bits(width, LENGTH_BITS)
        raw, distance, length = (codes(table) for table in chunk.tables)
        self.bits(len(chunk.subs), COUNT_BITS)
        for sub in chunk.subs:
            self.value(raw, len(sub.literals))
            self.out += sub.literals
            if sub.length:
                self.value(distance, sub.distance - 1)
                self.value(length, sub.length - MIN_MATCH)

    def finish(self) -> bytes:
        short = self.slot == len(self.out) - WORD and self.used <= HALF_WORD_BITS
        return bytes(self.out[:-1] if short else self.out)


def encode(chunks: Sequence[Chunk]) -> bytes:
    writer = Writer()
    writer.bits(0, KEY_BITS)
    for chunk in chunks:
        writer.chunk(chunk)
    return writer.finish()


def _depths(weights: Sequence[int]) -> tuple[int, ...]:
    heap: list[tuple[int, int, tuple[int, ...]]] = [
        (weight, index, (index,)) for index, weight in enumerate(weights) if weight
    ]
    heapq.heapify(heap)
    depth = [0] * len(weights)
    order = len(weights)
    while len(heap) > 1:
        first, _, left = heapq.heappop(heap)
        second, _, right = heapq.heappop(heap)
        for index in left + right:
            depth[index] += 1
        order += 1
        heapq.heappush(heap, (first + second, order, left + right))
    return tuple(max(level, 1) if weights[index] else 0 for index, level in enumerate(depth))


def _halved(weights: Sequence[int]) -> tuple[int, ...]:
    return tuple((weight + 1) // 2 for weight in weights)


def huffman(weights: Sequence[int]) -> tuple[int, ...]:
    rounds = accumulate(range(WORD_BITS), lambda scaled, _: _halved(scaled), initial=tuple(weights))
    return next(depth for depth in map(_depths, rounds) if max(depth) <= LONGEST_CODE)


def _counts(subs: Sequence[Sub]) -> tuple[list[int], list[int], list[int]]:
    counts: tuple[list[int], list[int], list[int]] = ([0] * SYMBOLS, [0] * SYMBOLS, [0] * SYMBOLS)
    for sub in subs:
        counts[0][symbol(len(sub.literals))] += 1
        if sub.length:
            counts[1][symbol(sub.distance - 1)] += 1
            counts[2][symbol(sub.length - MIN_MATCH)] += 1
    return counts


def _trimmed(weights: Sequence[int]) -> tuple[int, ...]:
    used = max((index for index, weight in enumerate(weights) if weight), default=-1)
    return huffman(weights[: used + 1]) if used >= 0 else ()


def tables_for(subs: Sequence[Sub]) -> Tables:
    raw, distance, length = (_trimmed(weights) for weights in _counts(subs))
    return raw, distance, length


def cost(lengths: Sequence[int]) -> tuple[int, ...]:
    table = codes(lengths)
    return tuple(
        table.get(index, (0, UNKNOWN_CODE))[1] + max(index - 1, 0) for index in range(SYMBOLS)
    )


def _sub_bits(sub: Sub, raw: Sequence[int], distance: Sequence[int], length: Sequence[int]) -> int:
    literal = raw[symbol(len(sub.literals))] + BYTE_BITS * len(sub.literals)
    if not sub.length:
        return literal
    return literal + distance[symbol(sub.distance - 1)] + length[symbol(sub.length - MIN_MATCH)]


def bits(chunk: Chunk) -> int:
    raw, distance, length = (cost(table) for table in chunk.tables)
    header = COUNT_BITS + sum(TABLE_SIZE_BITS + LENGTH_BITS * len(t) for t in chunk.tables)
    return header + sum(_sub_bits(sub, raw, distance, length) for sub in chunk.subs)
