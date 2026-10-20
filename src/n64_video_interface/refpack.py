from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .edits import NO_TARGETS, Packed, RegionEdit, Settings, edit_region

MAGIC = b"\xfb"
FLAGS = (0x10, 0x11)
LARGE_FLAG = 0x01
SIZE_BYTES = 3
MAX_SIZE = 0x800000
SHORT_TOP = 0x80
MEDIUM_TOP = 0xC0
LONG_TOP = 0xE0
BLOCK_TOP = 0xFC
PLAIN_MASK = 0x03
BLOCK_UNIT = 4
BLOCK_LIMIT = 112
SHORT = (3, 10, 1024)
MEDIUM = (4, 67, 16384)
LONG = (5, 1028, 131072)
WINDOW = LONG[2]
MIN_COPY = SHORT[0]
CANDIDATES = 32
IMPROVE_WINDOW = 0x4000
NOUN = "RefPack stream"
NO_ROOM = "a changed RefPack stream no longer fits where it was"


@dataclass(frozen=True, slots=True)
class Literals:
    data: bytes


@dataclass(frozen=True, slots=True)
class Copy:
    distance: int
    length: int


Token = Literals | Copy


@dataclass(frozen=True, slots=True)
class Stream:
    at: int
    size: int
    length: int
    tokens: tuple[Token, ...]


def span(token: Token) -> int:
    return len(token.data) if isinstance(token, Literals) else token.length


def copy_size(copy: Copy) -> int | None:
    for size, (shortest, longest, reach) in ((2, SHORT), (3, MEDIUM), (4, LONG)):
        if shortest <= copy.length <= longest and copy.distance <= reach:
            return size
    return None


def _command(data: bytes, at: int) -> tuple[int, int, int, int]:
    first = data[at]
    if first < SHORT_TOP:
        second = data[at + 1]
        return 2, first & PLAIN_MASK, ((first >> 2) & 7) + 3, ((first & 0x60) << 3) + second + 1
    if first < MEDIUM_TOP:
        second, third = data[at + 1], data[at + 2]
        return 3, second >> 6, (first & 0x3F) + 4, ((second & 0x3F) << 8) + third + 1
    if first < LONG_TOP:
        second, third, fourth = data[at + 1], data[at + 2], data[at + 3]
        length = ((first & 0x0C) << 6) + fourth + 5
        return 4, first & PLAIN_MASK, length, ((first & 0x10) << 12) + (second << 8) + third + 1
    if first < BLOCK_TOP:
        return 1, ((first & 0x1F) << 2) + BLOCK_UNIT, 0, 0
    return 1, first & PLAIN_MASK, -1, 0


def _header(data: bytes, at: int) -> tuple[int, int] | None:
    if data[at] not in FLAGS or data[at + 1 : at + 2] != MAGIC:
        return None
    start = at + 2 + (SIZE_BYTES if data[at] & LARGE_FLAG else 0)
    size = int.from_bytes(data[start : start + SIZE_BYTES], "big")
    return (size, start + SIZE_BYTES) if 0 < size <= MAX_SIZE else None


def _merged(tokens: list[Token], literals: bytes) -> None:
    if not literals:
        return
    if tokens and isinstance(tokens[-1], Literals):
        tokens[-1] = Literals(tokens[-1].data + literals)
        return
    tokens.append(Literals(literals))


def decode(data: bytes, at: int) -> Stream | None:
    header = _header(data, at) if at + 2 <= len(data) else None
    if header is None:
        return None
    size, pos = header
    padded = data + bytes(4)
    tokens: list[Token] = []
    produced = 0
    while pos < len(data):
        command, plain, length, distance = _command(padded, pos)
        _merged(tokens, padded[pos + command : pos + command + plain])
        pos, produced = pos + command + plain, produced + plain
        if length < 0:
            return Stream(at, size, pos - at, tuple(tokens)) if produced == size else None
        if distance > produced or produced + length > size:
            return None
        tokens.extend([Copy(distance, length)] if length else [])
        produced += length
    return None


def expand(tokens: Sequence[Token], history: bytes = b"") -> bytes:
    out = bytearray(history)
    for token in tokens:
        if isinstance(token, Literals):
            out += token.data
            continue
        for _ in range(token.length):
            out.append(out[-token.distance])
    return bytes(out[len(history) :])


def _copy_command(copy: Copy, plain: int) -> bytes:
    gap, size = copy.distance - 1, copy_size(copy)
    if size == 2:
        return bytes((((gap >> 3) & 0x60) | ((copy.length - 3) << 2) | plain, gap & 0xFF))
    if size == 3:
        return bytes((SHORT_TOP | (copy.length - 4), (plain << 6) | (gap >> 8), gap & 0xFF))
    extra = copy.length - 5
    first = MEDIUM_TOP | ((gap >> 12) & 0x10) | ((extra >> 6) & 0x0C) | plain
    return bytes((first, (gap >> 8) & 0xFF, gap & 0xFF, extra & 0xFF))


def _blocks(pending: bytes) -> tuple[bytes, bytes]:
    out = bytearray()
    while len(pending) > PLAIN_MASK:
        count = min(BLOCK_LIMIT, len(pending) - len(pending) % BLOCK_UNIT)
        out += bytes((LONG_TOP | ((count - BLOCK_UNIT) >> 2),)) + pending[:count]
        pending = pending[count:]
    return bytes(out), pending


def encode(tokens: Sequence[Token], size: int) -> bytes:
    out = bytearray((FLAGS[0], MAGIC[0])) + size.to_bytes(SIZE_BYTES, "big")
    pending = b""
    for token in tokens:
        if isinstance(token, Literals):
            pending += token.data
            continue
        blocks, pending = _blocks(pending)
        out += blocks + _copy_command(token, len(pending)) + pending
        pending = b""
    blocks, pending = _blocks(pending)
    return bytes(out + blocks + bytes((BLOCK_TOP | len(pending),)) + pending)


def cost(token: Token) -> float:
    if isinstance(token, Literals):
        return len(token.data) * (1 + 1 / BLOCK_LIMIT)
    return float(copy_size(token) or WINDOW)


@dataclass(slots=True)
class _Path:
    cost: list[float]
    back: list[Copy | None]


def _lengths(longest: int) -> set[int]:
    return set(range(MIN_COPY, min(longest, 34) + 1)) | {
        value for value in (MEDIUM[1], LONG[1], longest) if value <= longest
    }


def _copies(path: _Path, data: bytes, at: int, offset: int, index: dict[bytes, list[int]]) -> None:
    limit = min(len(path.cost) - 1 - offset, LONG[1])
    longest = MIN_COPY - 1
    for source in reversed(index.get(data[at : at + MIN_COPY], [])[-CANDIDATES:]):
        if at - source > WINDOW or longest >= limit:
            return
        length = MIN_COPY
        while length < limit and data[source + length] == data[at + length]:
            length += 1
        for size in _lengths(length) - set(range(longest + 1)):
            copy = Copy(at - source, size)
            total = path.cost[offset] + cost(copy)
            if total < path.cost[offset + size]:
                path.cost[offset + size], path.back[offset + size] = total, copy
        longest = max(longest, length)


def _trace(path: _Path, data: bytes, start: int) -> tuple[Token, ...]:
    tokens: list[Token] = []
    offset = len(path.back) - 1
    while offset:
        copy = path.back[offset]
        if copy is None:
            _merged(tokens, data[start + offset - 1 : start + offset])
            offset -= 1
            continue
        tokens.append(copy)
        offset -= copy.length
    ordered = [Literals(t.data[::-1]) if isinstance(t, Literals) else t for t in reversed(tokens)]
    return tuple(ordered)


def optimal(data: bytes, start: int, end: int) -> tuple[Token, ...]:
    index: dict[bytes, list[int]] = {}
    for at in range(max(0, start - WINDOW), start):
        index.setdefault(data[at : at + MIN_COPY], []).append(at)
    size = end - start
    path = _Path([0.0] + [float("inf")] * size, [None] * (size + 1))
    for offset in range(size):
        at = start + offset
        literal = path.cost[offset] + cost(Literals(data[at : at + 1]))
        if literal < path.cost[offset + 1]:
            path.cost[offset + 1], path.back[offset + 1] = literal, None
        _copies(path, data, at, offset, index)
        index.setdefault(data[at : at + MIN_COPY], []).append(at)
    return _trace(path, data, start)


def _run(distance: int, run: bytes) -> list[Token]:
    copy = Copy(distance, len(run))
    return [copy] if run and copy_size(copy) else ([Literals(run)] if run else [])


def _split_copy(copy: Copy, desired: bytes, out: bytearray) -> list[Token]:
    tokens: list[Token] = []
    start = len(out)
    for _ in range(copy.length):
        at = len(out)
        if out[at - copy.distance] != desired[at]:
            tokens.extend(
                [*_run(copy.distance, bytes(out[start:at])), Literals(desired[at : at + 1])]
            )
            start = at + 1
        out.append(desired[at])
    return [*tokens, *_run(copy.distance, bytes(out[start:]))]


def split(tokens: Sequence[Token], desired: bytes) -> tuple[Token, ...]:
    out = bytearray()
    result: list[Token] = []
    for token in tokens:
        if isinstance(token, Literals):
            _merged(result, desired[len(out) : len(out) + len(token.data)])
            out += desired[len(out) : len(out) + len(token.data)]
            continue
        for piece in _split_copy(token, desired, out):
            if isinstance(piece, Literals):
                _merged(result, piece.data)
            else:
                result.append(piece)
    return tuple(result)


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
    return sum(map(cost, part))


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


def find_streams(rom: bytes) -> tuple[Stream, ...]:
    found: list[Stream] = []
    at = rom.find(MAGIC, 1)
    while at >= 0:
        stream = decode(rom, at - 1) if rom[at - 1] in FLAGS else None
        found.extend([stream] if stream else [])
        at = rom.find(MAGIC, stream.at + stream.length if stream else at + 1)
    return tuple(found)


def _edited(stream: Stream, settings: Settings) -> tuple[bytes, RegionEdit] | str | None:
    edit = edit_region(expand(stream.tokens), settings)
    if not edit.changes:
        return None
    before = expand(stream.tokens)
    target = sum(map(cost, stream.tokens))
    body = encode(improve(split(stream.tokens, edit.data), edit.data, before, target), stream.size)
    return NO_ROOM if len(body) > stream.length else (body + bytes(stream.length - len(body)), edit)


def edit_streams(rom: bytes, settings: Settings) -> Packed | str:
    done: tuple[RegionEdit, ...] = ()
    spans: tuple[tuple[int, int], ...] = ()
    out, refusal = rom, NO_TARGETS
    for stream in find_streams(rom):
        result = _edited(stream, settings)
        if isinstance(result, tuple):
            out = out[: stream.at] + result[0] + out[stream.at + stream.length :]
            spans = (*spans, (stream.at, stream.at + stream.length))
            done = (*done, result[1])
        refusal = result if isinstance(result, str) else refusal
    return Packed(out, done, len(done), NOUN, spans) if done else refusal
