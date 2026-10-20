from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence

from . import bitlz
from .bitlz import Copy, Literals, Token, expand, span, split
from .edits import NO_TARGETS, Packed, RegionEdit, Settings, edit_region

HEADER = 5
END = 0
LITERAL_FLAG = 0x80
RUN_LIMIT = 0x7F
MIN_COPY = 3
MAX_COPY = 0x7F
WINDOW = 0xFFFF
BYTE = 0xFF
BYTE_BITS = 8
COPY_BYTES = 3
CANDIDATES = 32
IMPROVE_WINDOW = 0x4000
UNREACHED = 1 << 62
NOUN = "byte-coded segment"
NO_ROOM = "a changed byte-coded segment no longer fits where it was"


class FormatError(ValueError):
    pass


def cost(token: Token) -> int:
    return 1 + len(token.data) if isinstance(token, Literals) else COPY_BYTES


def _token(data: bytes, at: int, produced: int) -> tuple[Token, int]:
    control = data[at]
    if control & LITERAL_FLAG:
        run = data[at + 1 : at + 1 + (control & RUN_LIMIT)]
        if len(run) != control & RUN_LIMIT:
            raise FormatError("the stream ends before its end mark")
        return Literals(run), at + 1 + len(run)
    distance = data[at + 1] | (data[at + 2] << BYTE_BITS)
    if not 0 < distance <= produced:
        raise FormatError("a copy reaches before the start of the segment")
    return Copy(distance, control), at + COPY_BYTES


def decode(data: bytes, size: int) -> tuple[tuple[Token, ...], int]:
    tokens: list[Token] = []
    at, produced = HEADER, 0
    try:
        while data[at] != END and produced < size:
            token, at = _token(data, at, produced)
            tokens.append(token)
            produced += span(token)
        ended = data[at] == END
    except IndexError as error:
        raise FormatError("the stream ends before its end mark") from error
    if produced != size or not ended:
        raise FormatError("the stream does not decode to its size")
    return tuple(tokens), at + 1


def _item(token: Token) -> bytes:
    if isinstance(token, Literals):
        return bytes((LITERAL_FLAG | len(token.data),)) + token.data
    return bytes((token.length, token.distance & BYTE, token.distance >> BYTE_BITS))


def encode(tokens: Sequence[Token], header: bytes) -> bytes:
    return header + b"".join(map(_item, tokens)) + bytes((END,))


def _runs(data: bytes) -> list[Token]:
    return [Literals(data[at : at + RUN_LIMIT]) for at in range(0, len(data), RUN_LIMIT)]


def _matches(
    data: bytes, at: int, limit: int, index: Mapping[bytes, Sequence[int]]
) -> Iterator[Copy]:
    longest = MIN_COPY - 1
    for source in reversed(index.get(data[at : at + MIN_COPY], [])[-CANDIDATES:]):
        if at - source > WINDOW or longest >= limit:
            return
        length = MIN_COPY
        while length < limit and data[source + length] == data[at + length]:
            length += 1
        yield from (Copy(at - source, size) for size in range(longest + 1, length + 1))
        longest = max(longest, length)


def _trace(back: Sequence[Copy | None], data: bytes, start: int) -> tuple[Token, ...]:
    tokens: list[Token] = []
    offset = len(back) - 1
    pending = bytearray()
    while offset:
        copy = back[offset]
        if copy is None:
            pending.append(data[start + offset - 1])
            offset -= 1
            continue
        tokens.extend(reversed(_runs(bytes(reversed(pending)))))
        pending = bytearray()
        tokens.append(copy)
        offset -= copy.length
    tokens.extend(reversed(_runs(bytes(reversed(pending)))))
    return tuple(reversed(tokens))


def optimal(data: bytes, start: int, end: int) -> tuple[Token, ...]:
    index: dict[bytes, list[int]] = {}
    for at in range(max(0, start - WINDOW), start):
        index.setdefault(data[at : at + MIN_COPY], []).append(at)
    size = end - start
    best = [0] + [UNREACHED] * size
    back: list[Copy | None] = [None] * (size + 1)
    run = [0] * (size + 1)
    for offset in range(size):
        at = start + offset
        literal = best[offset] + 1 + (1 if run[offset] % RUN_LIMIT == 0 else 0)
        if literal < best[offset + 1]:
            best[offset + 1], back[offset + 1], run[offset + 1] = literal, None, run[offset] + 1
        for copy in _matches(data, at, min(size - offset, MAX_COPY), index):
            if best[offset] + COPY_BYTES < best[offset + copy.length]:
                best[offset + copy.length] = best[offset] + COPY_BYTES
                back[offset + copy.length], run[offset + copy.length] = copy, 0
        index.setdefault(data[at : at + MIN_COPY], []).append(at)
    return _trace(back, data, start)


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
    total = sum(map(cost, tokens))
    changed = [i for i, (_, _, a, b) in enumerate(windows) if desired[a:b] != before[a:b]]
    for i in [*changed, *(i for i in range(len(windows)) if i not in changed)]:
        if i not in changed and total <= target:
            break
        fresh = optimal(desired, windows[i][2], windows[i][3])
        saving = sum(map(cost, parts[i])) - sum(map(cost, fresh))
        parts[i], total = (fresh, total - saving) if saving > 0 else (parts[i], total)
    return tuple(token for part in parts for token in part)


def find_segments(rom: bytes) -> tuple[bitlz.Segment, ...]:
    return bitlz.find_segments(rom, bitlz.BYTE_CODED)


def _edited(
    rom: bytes, segment: bitlz.Segment, settings: Settings
) -> tuple[bytes, RegionEdit] | str | None:
    data = rom[segment.body]
    try:
        tokens, _ = decode(data, segment.size)
    except FormatError:
        return None
    before = expand(tokens)
    edit = edit_region(before, settings)
    if not edit.changes:
        return None
    target = sum(map(cost, tokens))
    body = encode(improve(split(tokens, edit.data), edit.data, before, target), data[:HEADER])
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
