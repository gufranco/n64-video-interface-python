from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from . import lzparse, rewindow
from .bitlz import Copy, Literals, Token, expand, span, split
from .edits import NO_TARGETS, Packed, RegionEdit, Settings, edit_region

FORMAT_BYTE = 1
START_BYTE = re.compile(b"\x01")
HEADER = 4
SIZE_BYTES = 3
GROUP = 8
RAW_GROUP = 0
SENTINEL = 0x800000
TOP = 0x80000000
WORD = 0xFFFFFFFF
CONTROL_SHIFT = 24
FORCED_AFTER = 7
HIGH_NIBBLE = 0xF0
LOW_NIBBLE = 0x0F
DISTANCE_SHIFT = 4
BYTE = 0xFF
BYTE_BITS = 8
WINDOW = 0xFFF
MIN_COPY = 2
MIN_PIECE = 2
SHORT_LIMIT = 16
LONG_BASE = 0x11
MAX_COPY = LONG_BASE + BYTE
LITERAL_BITS = 9
SHORT_BITS = 17
LONG_BITS = 25
CANDIDATES = 512
BOOT = 0x1000
ALIGN = 4
MIN_SIZE = 0x40
MAX_SIZE = 0x800000
END_MARK = bytes(2)
NOUN = "LZ stream"
NO_ROOM = "a changed LZ stream no longer fits where it was"
Item = tuple[int, bytes]


class FormatError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Stream:
    at: int
    size: int
    packed: int


def _match(data: bytes, at: int, produced: int) -> tuple[Copy | None, int]:
    first, second = data[at], data[at + 1]
    distance = second | ((first & HIGH_NIBBLE) << DISTANCE_SHIFT)
    if not distance:
        return None, at + len(END_MARK)
    if distance > produced:
        raise FormatError("a copy reaches before the start of the stream")
    nibble = first & LOW_NIBBLE
    if not nibble:
        return Copy(distance, data[at + 2] + LONG_BASE), at + 3
    return Copy(distance, MIN_PIECE if nibble == 1 else nibble + 1), at + 2


def _group(data: bytes, at: int, produced: int) -> tuple[list[Token], int, bool]:
    control, at = data[at], at + 1
    if control == RAW_GROUP:
        return [Literals(bytes(data[at + index] for index in range(GROUP)))], at + GROUP, False
    tokens: list[Token] = []
    bits, literals = (control << CONTROL_SHIFT) | SENTINEL, 0
    while bits:
        top, bits = bits & TOP, (bits << 1) & WORD
        if not top and literals < FORCED_AFTER:
            tokens.append(Literals(bytes((data[at],))))
            at, literals, produced = at + 1, literals + 1, produced + 1
            continue
        if bits:
            copy, at = _match(data, at, produced)
            if copy is None:
                return tokens, at, True
            tokens.append(copy)
            literals, produced = 0, produced + copy.length
    return tokens, at, False


def decode(data: bytes, at: int) -> tuple[tuple[Token, ...], int]:
    size = int.from_bytes(data[at + 1 : at + HEADER], "big")
    tokens: list[Token] = []
    here, produced, ended = at + HEADER, 0, False
    try:
        while not ended and produced <= size:
            group, here, ended = _group(data, here, produced)
            tokens.extend(group)
            produced += sum(map(span, group))
    except IndexError as error:
        raise FormatError("the stream ends before its end mark") from error
    if not ended or produced != size:
        raise FormatError("the stream does not decode to its size")
    return lzparse.merged(tokens), here - at


def _pieces(length: int) -> list[int]:
    pieces: list[int] = []
    left = length
    while left > MAX_COPY:
        pieces.append(min(MAX_COPY, left - MIN_PIECE))
        left -= pieces[-1]
    return [*pieces, left]


def _copy_bytes(distance: int, length: int) -> bytes:
    high = (distance >> BYTE_BITS) << DISTANCE_SHIFT
    if length > SHORT_LIMIT:
        return bytes((high, distance & BYTE, length - LONG_BASE))
    return bytes((high | (1 if length == MIN_PIECE else length - 1), distance & BYTE))


def _items(token: Token) -> list[Item]:
    if isinstance(token, Literals):
        return [(0, bytes((byte,))) for byte in token.data]
    return [(1, _copy_bytes(token.distance, piece)) for piece in _pieces(token.length)]


def _group_bytes(items: Sequence[Item]) -> bytes:
    body = b"".join(chunk for _, chunk in items)
    if len(items) == GROUP and not any(flag for flag, _ in items):
        return bytes((RAW_GROUP,)) + body
    control = sum(flag << (BYTE_BITS - 1 - index) for index, (flag, _) in enumerate(items))
    return bytes((control,)) + body


def encode(tokens: Sequence[Token], size: int) -> bytes:
    items = [*(item for token in tokens for item in _items(token)), (1, END_MARK)]
    groups = (items[at : at + GROUP] for at in range(0, len(items), GROUP))
    head = bytes((FORMAT_BYTE,)) + size.to_bytes(SIZE_BYTES, "big")
    return head + b"".join(map(_group_bytes, groups))


def cost(token: Token) -> int:
    if isinstance(token, Literals):
        return LITERAL_BITS * len(token.data)
    return sum(SHORT_BITS if p <= SHORT_LIMIT else LONG_BITS for p in _pieces(token.length))


def _copy_bits(distance: int, length: int) -> int:
    return cost(Copy(distance, length))


SHAPE = lzparse.Shape(WINDOW, MIN_COPY, MAX_COPY, CANDIDATES, LITERAL_BITS, _copy_bits)


def optimal(data: bytes, start: int, end: int) -> tuple[Token, ...]:
    return lzparse.optimal(data, start, end, SHAPE)


def _stream_at(rom: bytes, at: int) -> Stream | None:
    size = int.from_bytes(rom[at + 1 : at + HEADER], "big")
    if not MIN_SIZE <= size <= MAX_SIZE:
        return None
    try:
        _, used = decode(rom, at)
    except FormatError:
        return None
    return Stream(at, size, used)


def find_streams(rom: bytes) -> tuple[Stream, ...]:
    found: list[Stream] = []
    resume = BOOT
    for match in START_BYTE.finditer(rom, BOOT):
        at = match.start()
        stream = None if at < resume or at % ALIGN else _stream_at(rom, at)
        if stream is not None:
            found.append(stream)
            resume = at + stream.packed
    return tuple(found)


def _edited(
    rom: bytes, stream: Stream, settings: Settings
) -> tuple[bytes, RegionEdit] | str | None:
    tokens, _ = decode(rom, stream.at)
    before = expand(tokens)
    edit = edit_region(before, settings)
    if not edit.changes:
        return None
    target = sum(map(cost, tokens))
    improved = rewindow.improve(
        split(tokens, edit.data), edit.data, before, target, optimal=optimal, cost=cost
    )
    body = encode(improved, stream.size)
    if len(body) > stream.packed:
        return NO_ROOM
    return body + bytes(stream.packed - len(body)), edit


def edit_streams(rom: bytes, settings: Settings) -> Packed | str:
    done: tuple[RegionEdit, ...] = ()
    out, refusal = rom, NO_TARGETS
    for stream in find_streams(rom):
        result = _edited(rom, stream, settings)
        if isinstance(result, tuple):
            out = out[: stream.at] + result[0] + out[stream.at + stream.packed :]
            done = (*done, result[1])
        refusal = result if isinstance(result, str) else refusal
    return Packed(out, done, len(done), NOUN) if done else refusal
