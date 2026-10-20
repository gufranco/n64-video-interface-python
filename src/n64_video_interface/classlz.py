from __future__ import annotations

import re
import struct
from collections.abc import Sequence
from dataclasses import dataclass

from . import lzparse, rewindow
from .bitlz import Copy, Literals, Token, expand, span, split
from .edits import NO_TARGETS, Packed, RegionEdit, Settings, edit_region

MAGIC = b"BRSOHYES"
START = re.compile(re.escape(MAGIC))
SEGMENTS = 4
RECORD = struct.Struct(">2I")
HEADER = len(MAGIC) + RECORD.size * SEGMENTS
CLASSES = ((5, 0x001), (7, 0x021), (9, 0x0A1), (10, 0x2A1))
CLASS_BITS = 2
BYTE_BITS = 8
FLAG_SENTINEL = 0x100
WINDOW = CLASSES[-1][1] + (1 << CLASSES[-1][0]) - 1
MIN_COPY = 2
SHORT_BASE = 2
SHORT_BITS = 2
MIDDLE_BASE = 5
MIDDLE_BITS = 4
LONG_BASE = 0x14
MAX_COPY = LONG_BASE + 0xFF
SHORT_LIMIT = SHORT_BASE + (1 << SHORT_BITS) - 1
MIDDLE_LIMIT = MIDDLE_BASE + (1 << MIDDLE_BITS) - 1
PREFIX_BITS = 2
END_CODE = 0b11 << (SHORT_BITS + MIDDLE_BITS)
END_BITS = PREFIX_BITS + SHORT_BITS + MIDDLE_BITS
LITERAL_BITS = 1 + BYTE_BITS
CANDIDATES = 512
BOOT = 0x1000
ALIGN = 4
MAX_SIZE = 0x800000
NOUN = "packed program"
NO_ROOM = "a changed program segment no longer fits where it was"


class FormatError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Segment:
    at: int
    size: int
    packed: int


@dataclass(frozen=True, slots=True)
class Program:
    at: int
    segments: tuple[Segment, ...]


class Reader:
    def __init__(self, data: bytes, at: int) -> None:
        self.data, self.at, self.flags = data, at, 1

    def byte(self) -> int:
        value = self.data[self.at]
        self.at += 1
        return value

    def bit(self) -> int:
        if self.flags == 1:
            self.flags = self.byte() | FLAG_SENTINEL
        value, self.flags = self.flags & 1, self.flags >> 1
        return value

    def bits(self, count: int, value: int = 0) -> int:
        for _ in range(count):
            value = (value << 1) | self.bit()
        return value

    def length(self) -> int | None:
        if not self.bit():
            return SHORT_BASE
        short = self.bits(SHORT_BITS)
        if short:
            return short + SHORT_BASE
        middle = self.bits(MIDDLE_BITS)
        if middle:
            return middle + MIDDLE_BASE
        long = self.byte()
        return long + LONG_BASE if long else None

    def distance(self) -> int:
        width, base = CLASSES[self.bits(CLASS_BITS)]
        if width < BYTE_BITS:
            return base + self.bits(width)
        return base + self.bits(width - BYTE_BITS, self.byte())


def _tokens(reader: Reader, size: int) -> list[Token]:
    tokens: list[Token] = []
    produced = 0
    while produced <= size:
        if not reader.bit():
            tokens.append(Literals(bytes((reader.byte(),))))
            produced += 1
            continue
        length = reader.length()
        if length is None:
            return tokens
        copy = Copy(reader.distance(), length)
        if copy.distance > produced:
            raise FormatError("a copy reaches before the start of the segment")
        tokens.append(copy)
        produced += length
    raise FormatError("the segment does not decode to its size")


def decode(data: bytes, at: int, size: int) -> tuple[tuple[Token, ...], int]:
    reader = Reader(data, at)
    try:
        tokens = _tokens(reader, size)
    except IndexError as error:
        raise FormatError("the segment ends before its end mark") from error
    if sum(map(span, tokens)) != size:
        raise FormatError("the segment does not decode to its size")
    return lzparse.merged(tokens), reader.at - at


def _class(distance: int) -> int:
    return next(i for i, (width, base) in enumerate(CLASSES) if distance < base + (1 << width))


def _pieces(length: int) -> list[int]:
    pieces: list[int] = []
    left = length
    while left > MAX_COPY:
        pieces.append(min(MAX_COPY, left - MIN_COPY))
        left -= pieces[-1]
    return [*pieces, left]


class Writer:
    def __init__(self) -> None:
        self.out, self.flag, self.used = bytearray(), 0, BYTE_BITS

    def byte(self, value: int) -> None:
        self.out.append(value)

    def bit(self, value: int) -> None:
        if self.used == BYTE_BITS:
            self.flag, self.used = len(self.out), 0
            self.out.append(0)
        self.out[self.flag] |= value << self.used
        self.used += 1

    def bits(self, value: int, count: int) -> None:
        for shift in range(count - 1, -1, -1):
            self.bit((value >> shift) & 1)

    def length(self, length: int) -> None:
        if length == SHORT_BASE:
            self.bits(0b10, PREFIX_BITS)
        elif length <= SHORT_LIMIT:
            self.bits(0b11, PREFIX_BITS)
            self.bits(length - SHORT_BASE, SHORT_BITS)
        elif length <= MIDDLE_LIMIT:
            self.bits(0b11 << SHORT_BITS, PREFIX_BITS + SHORT_BITS)
            self.bits(length - MIDDLE_BASE, MIDDLE_BITS)
        else:
            self.bits(END_CODE, END_BITS)
            self.byte(length - LONG_BASE)

    def distance(self, distance: int) -> None:
        index = _class(distance)
        width, base = CLASSES[index]
        value = distance - base
        self.bits(index, CLASS_BITS)
        if width >= BYTE_BITS:
            self.byte(value >> (width - BYTE_BITS))
        self.bits(value, width % BYTE_BITS if width >= BYTE_BITS else width)

    def token(self, token: Token) -> None:
        if isinstance(token, Literals):
            for value in token.data:
                self.bit(0)
                self.byte(value)
            return
        for piece in _pieces(token.length):
            self.length(piece)
            self.distance(token.distance)

    def end(self) -> bytes:
        self.bits(END_CODE, END_BITS)
        self.byte(0)
        return bytes(self.out)


def encode(tokens: Sequence[Token]) -> bytes:
    writer = Writer()
    for token in tokens:
        writer.token(token)
    return writer.end()


def _length_bits(length: int) -> int:
    if length == SHORT_BASE:
        return PREFIX_BITS
    if length <= SHORT_LIMIT:
        return PREFIX_BITS + SHORT_BITS
    if length <= MIDDLE_LIMIT:
        return END_BITS
    return END_BITS + BYTE_BITS


def _copy_bits(distance: int, length: int) -> int:
    width = CLASSES[_class(distance)][0]
    return sum(_length_bits(piece) + CLASS_BITS + width for piece in _pieces(length))


def cost(token: Token) -> int:
    if isinstance(token, Literals):
        return LITERAL_BITS * len(token.data)
    return _copy_bits(token.distance, token.length)


SHAPE = lzparse.Shape(WINDOW, MIN_COPY, MAX_COPY, CANDIDATES, LITERAL_BITS, _copy_bits)


def optimal(data: bytes, start: int, end: int) -> tuple[Token, ...]:
    return lzparse.optimal(data, start, end, SHAPE)


def _segments(rom: bytes, at: int) -> tuple[Segment, ...] | None:
    records = [RECORD.unpack_from(rom, at + len(MAGIC) + RECORD.size * i) for i in range(SEGMENTS)]
    if not all(0 < size <= MAX_SIZE and 0 < packed <= MAX_SIZE for size, packed in records):
        return None
    starts = [at + HEADER + sum(packed for _, packed in records[:i]) for i in range(SEGMENTS)]
    return tuple(
        Segment(start, size, packed) for start, (size, packed) in zip(starts, records, strict=True)
    )


def _decodes(rom: bytes, segment: Segment) -> bool:
    try:
        _, used = decode(rom, segment.at, segment.size)
    except FormatError:
        return False
    return used <= segment.packed


def _program_at(rom: bytes, at: int) -> Program | None:
    segments = _segments(rom, at)
    if segments is None or not all(_decodes(rom, segment) for segment in segments):
        return None
    return Program(at, segments)


def find_programs(rom: bytes) -> tuple[Program, ...]:
    starts = (m.start() for m in START.finditer(rom, BOOT) if m.start() % ALIGN == 0)
    found = (_program_at(rom, at) for at in starts if at + HEADER <= len(rom))
    return tuple(program for program in found if program is not None)


def _repacked(rom: bytes, segment: Segment, before: bytes, desired: bytes) -> bytes | None:
    tokens, _ = decode(rom, segment.at, segment.size)
    target = sum(map(cost, tokens))
    improved = rewindow.improve(
        split(tokens, desired), desired, before, target, optimal=optimal, cost=cost
    )
    body = encode(improved)
    return body + bytes(segment.packed - len(body)) if len(body) <= segment.packed else None


def _rebuilt(rom: bytes, program: Program, parts: Sequence[bytes], image: bytes) -> bytes | None:
    out, start = rom, 0
    for segment, before in zip(program.segments, parts, strict=True):
        desired = image[start : start + segment.size]
        start += segment.size
        if desired == before:
            continue
        body = _repacked(rom, segment, before, desired)
        if body is None:
            return None
        out = out[: segment.at] + body + out[segment.at + segment.packed :]
    return out


def _edited(
    rom: bytes, program: Program, settings: Settings
) -> tuple[bytes, RegionEdit] | str | None:
    parts = [expand(decode(rom, s.at, s.size)[0]) for s in program.segments]
    edit = edit_region(b"".join(parts), settings)
    if not edit.changes:
        return None
    out = _rebuilt(rom, program, parts, edit.data)
    return NO_ROOM if out is None else (out, edit)


def _span(program: Program) -> tuple[int, int]:
    last = program.segments[-1]
    return program.at, last.at + last.packed


def edit_programs(rom: bytes, settings: Settings) -> Packed | str:
    done: tuple[RegionEdit, ...] = ()
    spans: tuple[tuple[int, int], ...] = ()
    out, refusal = rom, NO_TARGETS
    for program in find_programs(rom):
        result = _edited(out, program, settings)
        if isinstance(result, tuple):
            out, done = result[0], (*done, result[1])
            spans = (*spans, _span(program))
        refusal = result if isinstance(result, str) else refusal
    return Packed(out, done, len(done), NOUN, spans) if done else refusal
