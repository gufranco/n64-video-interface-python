from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass

from . import lzparse, rewindow
from .bitlz import Copy, Literals, Token, expand, span, split
from .edits import NO_TARGETS, Packed, RegionEdit, Settings, edit_region

MAGIC = b"Yaz0"
SIZE = struct.Struct(">I")
HEADER = 16
RESERVED = slice(8, 16)
GROUP = 8
BYTE_BITS = 8
NIBBLE_BITS = 4
NIBBLE = 0x0F
BYTE = 0xFF
SHORT_BASE = 2
LONG_BASE = 0x12
SHORT_LIMIT = 0x11
MIN_COPY = 3
MAX_COPY = LONG_BASE + BYTE
WINDOW = 0x1000
CANDIDATES = 512
LITERAL_BITS = 9
SHORT_BITS = 17
LONG_BITS = 25
MAX_SIZE = 0x800000
NOUN = "Yaz0 file"
NO_ROOM = "a changed Yaz0 file no longer fits where it was"


class FormatError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class File:
    at: int
    size: int
    packed: int


def _copy(data: bytes, at: int, produced: int) -> tuple[Copy, int]:
    first, second = data[at], data[at + 1]
    distance = (((first & NIBBLE) << BYTE_BITS) | second) + 1
    if distance > produced:
        raise FormatError("a copy reaches before the start of the file")
    if first >> NIBBLE_BITS:
        return Copy(distance, (first >> NIBBLE_BITS) + SHORT_BASE), at + 2
    return Copy(distance, data[at + 2] + LONG_BASE), at + 3


def _group(data: bytes, at: int, produced: int, size: int) -> tuple[list[Token], int]:
    flags, at = data[at], at + 1
    tokens: list[Token] = []
    for bit in range(GROUP - 1, -1, -1):
        if produced >= size:
            break
        if flags >> bit & 1:
            token: Token = Literals(bytes((data[at],)))
            at += 1
        else:
            token, at = _copy(data, at, produced)
        tokens.append(token)
        produced += span(token)
    return tokens, at


def decode(data: bytes, at: int) -> tuple[tuple[Token, ...], int]:
    size = SIZE.unpack_from(data, at + len(MAGIC))[0]
    tokens: list[Token] = []
    here, produced = at + HEADER, 0
    try:
        while produced < size:
            group, here = _group(data, here, produced, size)
            tokens.extend(group)
            produced += sum(map(span, group))
    except IndexError as error:
        raise FormatError("the stream ends before its data") from error
    if produced != size:
        raise FormatError("the stream does not decode to its size")
    return lzparse.merged(tokens), here - at


def _pieces(length: int) -> list[int]:
    pieces: list[int] = []
    left = length
    while left > MAX_COPY:
        pieces.append(min(MAX_COPY, left - MIN_COPY))
        left -= pieces[-1]
    return [*pieces, left]


def _copy_bytes(distance: int, length: int) -> bytes:
    high = (distance - 1) >> BYTE_BITS
    low = (distance - 1) & BYTE
    if length > SHORT_LIMIT:
        return bytes((high, low, length - LONG_BASE))
    return bytes((((length - SHORT_BASE) << NIBBLE_BITS) | high, low))


def _items(token: Token) -> list[tuple[int, bytes]]:
    if isinstance(token, Literals):
        return [(1, bytes((byte,))) for byte in token.data]
    return [(0, _copy_bytes(token.distance, piece)) for piece in _pieces(token.length)]


def _group_bytes(items: Sequence[tuple[int, bytes]]) -> bytes:
    flags = sum(flag << (GROUP - 1 - index) for index, (flag, _) in enumerate(items))
    return bytes((flags,)) + b"".join(chunk for _, chunk in items)


def encode(tokens: Sequence[Token], size: int, reserved: bytes) -> bytes:
    items = [item for token in tokens for item in _items(token)]
    groups = (items[at : at + GROUP] for at in range(0, len(items), GROUP))
    return MAGIC + SIZE.pack(size) + reserved + b"".join(map(_group_bytes, groups))


def _copy_bits(distance: int, length: int) -> int:
    return sum(SHORT_BITS if piece <= SHORT_LIMIT else LONG_BITS for piece in _pieces(length))


def cost(token: Token) -> int:
    if isinstance(token, Literals):
        return LITERAL_BITS * len(token.data)
    return _copy_bits(token.distance, token.length)


SHAPE = lzparse.Shape(WINDOW, MIN_COPY, MAX_COPY, CANDIDATES, LITERAL_BITS, _copy_bits)


def optimal(data: bytes, start: int, end: int) -> tuple[Token, ...]:
    return lzparse.optimal(data, start, end, SHAPE)


def _file_at(rom: bytes, at: int) -> File | None:
    size = SIZE.unpack_from(rom, at + len(MAGIC))[0] if at + HEADER <= len(rom) else 0
    if not 0 < size <= MAX_SIZE:
        return None
    try:
        _, used = decode(rom, at)
    except FormatError:
        return None
    return File(at, size, used)


def find_files(rom: bytes) -> tuple[File, ...]:
    found: list[File] = []
    at = rom.find(MAGIC)
    while at >= 0:
        file = _file_at(rom, at)
        found.extend([file] if file else [])
        at = rom.find(MAGIC, at + (file.packed if file else 1))
    return tuple(found)


def _edited(rom: bytes, file: File, settings: Settings) -> tuple[bytes, RegionEdit] | str | None:
    tokens, _ = decode(rom, file.at)
    before = expand(tokens)
    edit = edit_region(before, settings)
    if not edit.changes:
        return None
    budget = (file.packed - HEADER) * BYTE_BITS
    improved = rewindow.improve(
        split(tokens, edit.data), edit.data, before, budget, optimal=optimal, cost=cost
    )
    reserved = rom[file.at + RESERVED.start : file.at + RESERVED.stop]
    body = encode(improved, file.size, reserved)
    if len(body) > file.packed:
        return NO_ROOM
    return body + bytes(file.packed - len(body)), edit


def edit_files(rom: bytes, settings: Settings) -> Packed | str:
    done: tuple[RegionEdit, ...] = ()
    spans: tuple[tuple[int, int], ...] = ()
    out, refusal = rom, NO_TARGETS
    for file in find_files(rom):
        result = _edited(rom, file, settings)
        if isinstance(result, tuple):
            out = out[: file.at] + result[0] + out[file.at + file.packed :]
            spans = (*spans, (file.at, file.at + file.packed))
            done = (*done, result[1])
        refusal = result if isinstance(result, str) else refusal
    return Packed(out, done, len(done), NOUN, spans) if done else refusal
