from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import takewhile

from .edits import NO_TARGETS, Packed, RegionEdit, Settings, edit_region

FORMAT_BYTE = 3
BYTE_CODED = 1
FORMATS = (BYTE_CODED, FORMAT_BYTE)
HEADER = 5
BLOCK = 0x1000
WINDOW = 0x10000
RUN_LIMIT = 0x7F
MIN_COPY = 3
MAX_COPY = 0xFF
COUNT_BITS = 7
BYTE_BITS = 8
GAMMA_LIMIT = 7
NO_OP = 0x80
COMPRESSED = 0x80000000
SIZE_MASK = 0x7FFFFFFF
RECORD = struct.Struct(">3I")
TABLE_SEARCH = range(0x1000, 0x10000, 4)
MAX_SIZE = 0x800000
NOUN = "compressed segment"
CANDIDATES = 32
IMPROVE_WINDOW = 0x4000
MAX_RECORDS = 256
NO_ROOM = "a changed compressed segment no longer fits where it was"


class FormatError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Literals:
    data: bytes


@dataclass(frozen=True, slots=True)
class Copy:
    distance: int
    length: int


Token = Literals | Copy


def span(token: Token) -> int:
    return len(token.data) if isinstance(token, Literals) else token.length


def distance_width(position: int) -> int:
    width, power = 1, 2
    while position >= power:
        width += 1 if power < WINDOW else 0
        power <<= 1
    return width


def _distance_code(position: int, distance: int) -> tuple[int, int]:
    width = distance_width(position)
    short = ((1 << width) - 1) - min(position, WINDOW)
    if short == -1:
        return distance, width
    return (distance, width - 1) if distance < short else (distance + short, width)


def cost(token: Token, position: int) -> int:
    if isinstance(token, Literals):
        return 1 + COUNT_BITS + BYTE_BITS * len(token.data)
    gamma = 2 * ((token.length - 2).bit_length() - 1) + 1
    return 1 + gamma + _distance_code(position, token.distance)[1]


class Reader:
    def __init__(self, data: bytes, start: int, budget: int) -> None:
        self.data = data
        self.pos = start
        self.budget = budget
        self.byte = 0
        self.left = 0

    def bits(self, width: int) -> int:
        value = 0
        for _ in range(width):
            if not self.left:
                self.byte = self.data[self.pos] if self.pos < len(self.data) else 0
                self.pos, self.left = self.pos + 1, BYTE_BITS
            self.left -= 1
            value = (value << 1) | ((self.byte >> self.left) & 1)
        self.budget -= width
        return value

    def gamma(self) -> int:
        zeros = 0
        while not self.bits(1) and zeros < GAMMA_LIMIT:
            zeros += 1
        return (self.bits(zeros) | (1 << zeros)) if zeros else 1

    def distance(self, position: int) -> int:
        width = distance_width(position)
        short = ((1 << width) - 1) - min(position, WINDOW)
        if short == -1:
            return self.bits(width)
        value = self.bits(width - 1)
        return value if value < short else ((value << 1) | self.bits(1)) - short

    def token(self, position: int) -> Token | None:
        if self.bits(1):
            count = self.bits(COUNT_BITS)
            return Literals(bytes(self.bits(BYTE_BITS) for _ in range(count))) if count else None
        length = (self.gamma() + 2) & MAX_COPY
        distance = self.distance(position)
        if not 0 < distance <= position:
            raise FormatError("a copy reaches before the start of the segment")
        return Copy(distance, length)


def decode(data: bytes, size: int) -> tuple[Token, ...]:
    if data[:1] != bytes([FORMAT_BYTE]):
        raise FormatError("the segment does not start with the format byte 3")
    tokens: list[Token] = []
    produced = 0
    for block in range(0, len(data), BLOCK):
        reader = Reader(data, block, min(BLOCK, len(data) - block) * BYTE_BITS)
        reader.bits(HEADER * BYTE_BITS if block == 0 else 0)
        while reader.budget >= BYTE_BITS and produced < size:
            token = reader.token(produced)
            tokens.extend([token] if token else [])
            produced += span(token) if token else 0
    if produced < size:
        raise FormatError("the segment ends before its data")
    return tuple(tokens)


def expand(tokens: Sequence[Token], history: bytes = b"") -> bytes:
    out = bytearray(history)
    for token in tokens:
        if isinstance(token, Literals):
            out += token.data
            continue
        for _ in range(token.length):
            out.append(out[-token.distance])
    return bytes(out[len(history) :])


class Writer:
    def __init__(self, header: bytes) -> None:
        self.out = bytearray(header)
        self.byte = 0
        self.used = 0
        self.block_bits = len(header) * BYTE_BITS

    def bits(self, value: int, width: int) -> None:
        for index in range(width - 1, -1, -1):
            self.byte = (self.byte << 1) | ((value >> index) & 1)
            self.used += 1
            if self.used == BYTE_BITS:
                self.out.append(self.byte)
                self.byte, self.used = 0, 0
        self.block_bits += width

    def close_block(self) -> None:
        while BLOCK * BYTE_BITS - self.block_bits >= BYTE_BITS:
            self.bits(NO_OP, BYTE_BITS)
        self.bits(0, BLOCK * BYTE_BITS - self.block_bits)
        self.block_bits = 0

    def token(self, token: Token, position: int) -> None:
        if cost(token, position) > BLOCK * BYTE_BITS - self.block_bits:
            self.close_block()
        if isinstance(token, Literals):
            self.bits(1, 1)
            self.bits(len(token.data), COUNT_BITS)
            for byte in token.data:
                self.bits(byte, BYTE_BITS)
            return
        zeros = (token.length - 2).bit_length() - 1
        self.bits(0, 1)
        self.bits(1, zeros + 1)
        self.bits(token.length - 2, zeros)
        self.bits(*_distance_code(position, token.distance))

    def finish(self) -> bytes:
        self.bits(0, -self.used % BYTE_BITS)
        return bytes(self.out)


def encode(tokens: Sequence[Token], header: bytes) -> bytes:
    writer = Writer(header)
    position = 0
    for token in tokens:
        writer.token(token, position)
        position += span(token)
    return writer.finish()


def _literal_runs(data: bytes) -> list[Token]:
    return [Literals(data[at : at + RUN_LIMIT]) for at in range(0, len(data), RUN_LIMIT)]


@dataclass(slots=True)
class _Path:
    cost: list[float]
    back: list[Copy | None]
    run: list[int]


def _literal(path: _Path, offset: int) -> None:
    total = (
        path.cost[offset] + BYTE_BITS + (1 + COUNT_BITS if path.run[offset] % RUN_LIMIT == 0 else 0)
    )
    if total < path.cost[offset + 1]:
        path.cost[offset + 1] = total
        path.back[offset + 1] = None
        path.run[offset + 1] = path.run[offset] + 1


def _copies(path: _Path, data: bytes, at: int, offset: int, index: dict[bytes, list[int]]) -> None:
    limit = min(len(path.cost) - 1 - offset, MAX_COPY)
    longest = MIN_COPY - 1
    for source in reversed(index.get(data[at : at + MIN_COPY], [])[-CANDIDATES:]):
        if at - source > WINDOW or longest >= limit:
            return
        length = MIN_COPY
        while length < limit and data[source + length] == data[at + length]:
            length += 1
        for size in range(longest + 1, length + 1):
            copy = Copy(at - source, size)
            total = path.cost[offset] + cost(copy, at)
            if total < path.cost[offset + size]:
                path.cost[offset + size], path.back[offset + size], path.run[offset + size] = (
                    total,
                    copy,
                    0,
                )
        longest = max(longest, length)


def _trace(path: _Path, data: bytes, start: int) -> tuple[Token, ...]:
    tokens: list[Token] = []
    offset = len(path.back) - 1
    pending = bytearray()
    while offset:
        copy = path.back[offset]
        if copy is None:
            pending.append(data[start + offset - 1])
            offset -= 1
            continue
        tokens.extend(reversed(_literal_runs(bytes(reversed(pending)))))
        pending = bytearray()
        tokens.append(copy)
        offset -= copy.length
    tokens.extend(reversed(_literal_runs(bytes(reversed(pending)))))
    return tuple(reversed(tokens))


def optimal(data: bytes, start: int, end: int) -> tuple[Token, ...]:
    index: dict[bytes, list[int]] = {}
    for at in range(max(0, start - WINDOW), start):
        index.setdefault(data[at : at + MIN_COPY], []).append(at)
    size = end - start
    path = _Path([0.0] + [float("inf")] * size, [None] * (size + 1), [0] * (size + 1))
    for offset in range(size):
        at = start + offset
        _literal(path, offset)
        _copies(path, data, at, offset, index)
        index.setdefault(data[at : at + MIN_COPY], []).append(at)
    return _trace(path, data, start)


def _split_copy(copy: Copy, desired: bytes, out: bytearray, pending: bytearray) -> list[Token]:
    tokens: list[Token] = []
    run = 0
    for _ in range(copy.length):
        at = len(out)
        if out[at - copy.distance] == desired[at]:
            out.append(desired[at])
            run += 1
            continue
        if run >= MIN_COPY:
            tokens.extend([*_literal_runs(bytes(pending)), Copy(copy.distance, run)])
            pending.clear()
        else:
            pending += out[at - run : at]
        run = 0
        pending.append(desired[at])
        out.append(desired[at])
    if run >= MIN_COPY:
        tokens.extend([*_literal_runs(bytes(pending)), Copy(copy.distance, run)])
        pending.clear()
    else:
        pending += out[len(out) - run :]
    return tokens


def split(tokens: Sequence[Token], desired: bytes) -> tuple[Token, ...]:
    out, pending = bytearray(), bytearray()
    result: list[Token] = []
    for token in tokens:
        if isinstance(token, Literals):
            pending += desired[len(out) : len(out) + len(token.data)]
            out += desired[len(out) : len(out) + len(token.data)]
            continue
        result.extend(_split_copy(token, desired, out, pending))
    return (*result, *_literal_runs(bytes(pending)))


def _bits(tokens: Sequence[Token], position: int) -> int:
    total = 0
    for token in tokens:
        total += cost(token, position)
        position += span(token)
    return total


@dataclass(frozen=True, slots=True)
class _Window:
    first: int
    end: int
    position: int
    stop: int


def _windows(tokens: Sequence[Token]) -> list[_Window]:
    windows: list[_Window] = []
    index, position = 0, 0
    while index < len(tokens):
        end, stop = index, position
        while end < len(tokens) and stop - position < IMPROVE_WINDOW:
            stop += span(tokens[end])
            end += 1
        windows.append(_Window(index, end, position, stop))
        index, position = end, stop
    return windows


def _price(part: Sequence[Token], position: int) -> float:
    return _bits(part, position)


def improve(
    tokens: Sequence[Token], desired: bytes, before: bytes, target: float
) -> tuple[Token, ...]:
    windows = _windows(tokens)
    parts = [tuple(tokens[w.first : w.end]) for w in windows]
    total = sum(_price(part, w.position) for part, w in zip(parts, windows, strict=True))
    changed = [
        i
        for i, w in enumerate(windows)
        if desired[w.position : w.stop] != before[w.position : w.stop]
    ]
    for i in [*changed, *(i for i in range(len(windows)) if i not in changed)]:
        if i not in changed and total <= target:
            break
        window = windows[i]
        fresh = optimal(desired, window.position, window.stop)
        saving = _price(parts[i], window.position) - _price(fresh, window.position)
        parts[i], total = (fresh, total - saving) if saving > 0 else (parts[i], total)
    return tuple(token for part in parts for token in part)


@dataclass(frozen=True, slots=True)
class Segment:
    at: int
    size: int
    packed: int

    @property
    def body(self) -> slice:
        return slice(self.at, self.at + self.packed)


def _record(rom: bytes, table: int, at: int) -> tuple[int, int, int, int]:
    offset, size, stored = RECORD.unpack_from(rom, at)
    return offset, size, stored, stored & SIZE_MASK if stored & COMPRESSED else stored


def _records(rom: bytes, table: int) -> list[tuple[int, int, int]]:
    stop = min(len(rom) - RECORD.size, table + MAX_RECORDS * RECORD.size)
    candidates = (_record(rom, table, at) for at in range(table, stop + 1, RECORD.size))
    chained: list[tuple[int, int, int]] = []
    expected = [-1]
    for offset, size, stored, length in takewhile(
        lambda r: (
            expected[-1] in (-1, r[0]) and 0 < r[1] <= MAX_SIZE and table + r[0] + r[3] <= len(rom)
        ),
        candidates,
    ):
        chained.append((offset, size, stored))
        expected.append(offset + length)
    return chained


def _table_at(rom: bytes, table: int, kind: int) -> tuple[Segment, ...] | None:
    offset, _, stored = RECORD.unpack_from(rom, table)
    if not stored & COMPRESSED or not 0 < offset < BLOCK or rom[table + offset] not in FORMATS:
        return None
    records = _records(rom, table)
    if not records:
        return None
    return tuple(
        Segment(table + at, size, stored & SIZE_MASK)
        for at, size, stored in records
        if stored & COMPRESSED and rom[table + at] == kind
    )


def find_segments(rom: bytes, kind: int = FORMAT_BYTE) -> tuple[Segment, ...]:
    for table in TABLE_SEARCH:
        if table + RECORD.size <= len(rom):
            found = _table_at(rom, table, kind)
            if found is not None:
                return found
    return ()


def _edited(
    rom: bytes, segment: Segment, settings: Settings
) -> tuple[bytes, RegionEdit] | str | None:
    data = rom[segment.body]
    try:
        tokens = decode(data, segment.size)
    except FormatError:
        return None
    original = encode(tokens, data[:HEADER])
    if data[: len(original)] != original:
        return None
    edit = edit_region(expand(tokens), settings)
    if not edit.changes:
        return None
    before = expand(tokens)
    body = encode(
        improve(split(tokens, edit.data), edit.data, before, _bits(tokens, 0)), data[:HEADER]
    )
    return (
        NO_ROOM if len(body) > segment.packed else (body + bytes(segment.packed - len(body)), edit)
    )


def edit_segments(rom: bytes, settings: Settings) -> Packed | str:
    done: tuple[RegionEdit, ...] = ()
    out, refusal = rom, NO_TARGETS
    for segment in find_segments(rom):
        result = _edited(rom, segment, settings)
        if isinstance(result, tuple):
            out = out[: segment.at] + result[0] + out[segment.body.stop :]
            done = (*done, result[1])
        refusal = result if isinstance(result, str) else refusal
    return Packed(out, done, len(done), NOUN) if done else refusal
