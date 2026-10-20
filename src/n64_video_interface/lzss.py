from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass

from .edits import NO_TARGETS, Packed, RegionEdit, Settings, edit_region

MAGIC = b"LZSS"
HEADER = struct.Struct(">4s2I")
RING = 0x1000
RING_MASK = RING - 1
LONGEST = 18
SHORTEST = 3
START = RING - LONGEST
REACH = RING - LONGEST
FLAGS = 8
LITERAL_BITS = 9
COPY_BITS = 17
CANDIDATES = 32
IMPROVE_WINDOW = 0x4000
MAX_SIZE = 0x800000
NOUN = "LZSS segment"
NO_ROOM = "a changed LZSS segment no longer fits where it was"


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


@dataclass(frozen=True, slots=True)
class Segment:
    at: int
    size: int
    packed: int

    @property
    def body(self) -> int:
        return self.at + HEADER.size


def span(token: Token) -> int:
    return len(token.data) if isinstance(token, Literals) else token.length


def _item(data: bytes, at: int, literal: bool, produced: int) -> tuple[Token, int]:
    if literal:
        return Literals(data[at : at + 1]), at + 1
    source = data[at] | ((data[at + 1] & 0xF0) << 4)
    distance = ((START + produced - source - 1) & RING_MASK) + 1
    return Copy(distance, (data[at + 1] & 0x0F) + SHORTEST), at + 2


def decode_tokens(data: bytes, start: int, size: int) -> tuple[tuple[Token, ...], int]:
    tokens: list[Token] = []
    at, flags, left, produced = start, 0, 0, 0
    try:
        while produced < size:
            if not left:
                flags, at, left = data[at], at + 1, FLAGS
            token, at = _item(data, at, bool(flags & 1), produced)
            flags, left, produced = flags >> 1, left - 1, produced + span(token)
            tokens.append(token)
    except IndexError as error:
        raise FormatError("the stream ends before its data") from error
    return _merged(tokens), at - start


def expand(tokens: Sequence[Token]) -> bytes:
    out = bytearray()
    for token in tokens:
        if isinstance(token, Literals):
            out += token.data
            continue
        for _ in range(token.length):
            source = len(out) - token.distance
            out.append(out[source] if source >= 0 else 0)
    return bytes(out)


def decode(data: bytes, start: int, size: int) -> tuple[bytes, int]:
    tokens, used = decode_tokens(data, start, size)
    return expand(tokens)[:size], used


def encode(tokens: Sequence[Token]) -> bytes:
    items: list[bytes] = []
    flags: list[int] = []
    produced = 0
    for token in tokens:
        if isinstance(token, Literals):
            items.extend(bytes((byte,)) for byte in token.data)
            flags.extend([1] * len(token.data))
        else:
            source = (START + produced - token.distance) & RING_MASK
            items.append(bytes((source & 0xFF, ((source >> 4) & 0xF0) | (token.length - SHORTEST))))
            flags.append(0)
        produced += span(token)
    out = bytearray()
    for group in range(0, len(items), FLAGS):
        out.append(sum(bit << index for index, bit in enumerate(flags[group : group + FLAGS])))
        out += b"".join(items[group : group + FLAGS])
    return bytes(out)


def cost(token: Token) -> int:
    return LITERAL_BITS * len(token.data) if isinstance(token, Literals) else COPY_BITS


@dataclass(slots=True)
class _Path:
    cost: list[int]
    distance: list[int]
    length: list[int]


def _copies(path: _Path, data: bytes, at: int, offset: int, index: dict[bytes, list[int]]) -> None:
    limit = min(len(path.cost) - 1 - offset, LONGEST)
    longest = SHORTEST - 1
    for source in reversed(index.get(data[at : at + SHORTEST], [])[-CANDIDATES:]):
        if at - source > REACH or longest >= limit:
            return
        length = SHORTEST
        while length < limit and data[source + length] == data[at + length]:
            length += 1
        total = path.cost[offset] + COPY_BITS
        for size in range(longest + 1, length + 1):
            if total < path.cost[offset + size]:
                path.cost[offset + size] = total
                path.distance[offset + size], path.length[offset + size] = at - source, size
        longest = max(longest, length)


def _merged(tokens: Sequence[Token]) -> tuple[Token, ...]:
    merged: list[Token] = []
    for token in tokens:
        if isinstance(token, Literals) and merged and isinstance(merged[-1], Literals):
            merged[-1] = Literals(merged[-1].data + token.data)
        else:
            merged.append(token)
    return tuple(merged)


def _trace(path: _Path, data: bytes, at: int) -> tuple[Token, ...]:
    tokens: list[Token] = []
    offset = len(path.cost) - 1
    while offset:
        size = path.length[offset]
        tokens.append(
            Copy(path.distance[offset], size)
            if size
            else Literals(data[at + offset - 1 : at + offset])
        )
        offset -= max(size, 1)
    return _merged(tokens[::-1])


def optimal(data: bytes, start: int, end: int) -> tuple[Token, ...]:
    padded = bytes(REACH) + data
    index: dict[bytes, list[int]] = {}
    for at in range(max(0, start), start + REACH):
        index.setdefault(padded[at : at + SHORTEST], []).append(at)
    size = end - start
    path = _Path([0] + [1 << 62] * size, [0] * (size + 1), [0] * (size + 1))
    for offset in range(size):
        at = REACH + start + offset
        if path.cost[offset] + LITERAL_BITS < path.cost[offset + 1]:
            path.cost[offset + 1], path.length[offset + 1] = path.cost[offset] + LITERAL_BITS, 0
        _copies(path, padded, at, offset, index)
        index.setdefault(padded[at : at + SHORTEST], []).append(at)
    return _trace(path, padded, REACH + start)


def _split_copy(copy: Copy, desired: bytes, out: bytearray) -> list[Token]:
    tokens: list[Token] = []
    run = 0
    for _ in range(copy.length):
        at = len(out)
        source = at - copy.distance
        byte = out[source] if source >= 0 else 0
        if byte == desired[at]:
            out.append(byte)
            run += 1
            continue
        tokens.extend(_run(copy.distance, bytes(out[at - run : at])))
        tokens.append(Literals(desired[at : at + 1]))
        out.append(desired[at])
        run = 0
    return [*tokens, *_run(copy.distance, bytes(out[len(out) - run :]))]


def _run(distance: int, run: bytes) -> list[Token]:
    if len(run) >= SHORTEST:
        return [Copy(distance, len(run))]
    return [Literals(run)] if run else []


def split(tokens: Sequence[Token], desired: bytes) -> tuple[Token, ...]:
    out = bytearray()
    result: list[Token] = []
    for token in tokens:
        if isinstance(token, Literals):
            result.append(Literals(desired[len(out) : len(out) + len(token.data)]))
            out += desired[len(out) : len(out) + len(token.data)]
        else:
            result.extend(_split_copy(token, desired, out))
    return _merged(result)


def _windows(tokens: Sequence[Token]) -> list[tuple[int, int, int, int]]:
    windows: list[tuple[int, int, int, int]] = []
    index, position = 0, 0
    while index < len(tokens):
        end, stop = index, position
        while end < len(tokens) and stop - position < IMPROVE_WINDOW:
            stop += span(tokens[end])
            end += 1
        windows.append((index, end, position, stop))
        index, position = end, stop
    return windows


def improve(
    tokens: Sequence[Token], desired: bytes, before: bytes, target: int
) -> tuple[Token, ...]:
    windows = _windows(tokens)
    parts = [tuple(tokens[first:end]) for first, end, _, _ in windows]
    total = sum(cost(token) for token in tokens)
    changed = [i for i, (_, _, a, b) in enumerate(windows) if desired[a:b] != before[a:b]]
    for i in [*changed, *(i for i in range(len(windows)) if i not in changed)]:
        if i not in changed and total <= target:
            break
        fresh = optimal(desired, windows[i][2], windows[i][3])
        saving = sum(map(cost, parts[i])) - sum(map(cost, fresh))
        parts[i], total = (fresh, total - saving) if saving > 0 else (parts[i], total)
    return tuple(token for part in parts for token in part)


def find_segments(rom: bytes) -> tuple[Segment, ...]:
    found: list[Segment] = []
    at = rom.find(MAGIC)
    while at >= 0:
        segment = _segment_at(rom, at)
        found.extend([segment] if segment else [])
        at = rom.find(MAGIC, at + 1)
    return tuple(found)


def _segment_at(rom: bytes, at: int) -> Segment | None:
    if at + HEADER.size > len(rom):
        return None
    _, size, packed = HEADER.unpack_from(rom, at)
    if (
        not 0 < size <= MAX_SIZE
        or not 0 < packed <= MAX_SIZE
        or at + HEADER.size + packed > len(rom)
    ):
        return None
    try:
        _, used = decode(rom[: at + HEADER.size + packed], at + HEADER.size, size)
    except FormatError:
        return None
    return Segment(at, size, packed) if used <= packed else None


def _edited(
    rom: bytes, segment: Segment, settings: Settings
) -> tuple[bytes, RegionEdit] | str | None:
    tokens, _ = decode_tokens(rom, segment.body, segment.size)
    before = expand(tokens)[: segment.size]
    edit = edit_region(before, settings)
    if not edit.changes:
        return None
    target = sum(cost(token) for token in tokens)
    body = encode(improve(split(tokens, edit.data), edit.data, before, target))
    return (
        NO_ROOM if len(body) > segment.packed else (body + bytes(segment.packed - len(body)), edit)
    )


def edit_segments(rom: bytes, settings: Settings) -> Packed | str:
    done: tuple[RegionEdit, ...] = ()
    out, refusal = rom, NO_TARGETS
    for segment in find_segments(rom):
        result = _edited(rom, segment, settings)
        if isinstance(result, tuple):
            out = out[: segment.body] + result[0] + out[segment.body + segment.packed :]
            done = (*done, result[1])
        refusal = result if isinstance(result, str) else refusal
    return Packed(out, done, len(done), NOUN) if done else refusal
