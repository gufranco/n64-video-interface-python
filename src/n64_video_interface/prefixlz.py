from __future__ import annotations

import struct
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from . import lzparse, rewindow
from .bitlz import Copy, Literals, Token, expand, span, split
from .edits import NO_TARGETS, Packed, RegionEdit, Settings, edit_region

SIZE = struct.Struct(">I")
WORD = struct.Struct(">I")
LITERAL_BASE = 0x80
PAIR_BASE = 0x90
LENGTH_BASE = 0xA0
OFFSET_BASE = 0xC0
NIBBLE_BITS = 4
NIBBLE = 0x0F
MID_BITS = 3
MID = 0x07
PAIR_OFFSET = 0x0C
PAIR_SHIFT = 2
PAIR_LENGTH = 0x03
LENGTH_BITS = 5
OFFSET_BITS = 6
LENGTH_MASK = 0x1F
OFFSET_MASK = 0x3F
MIN_COPY = 2
MAX_PREFIXES = 8
UNREACHED = lzparse.UNREACHED
WINDOW = 0x100000
MAX_COPY = 0x100
CANDIDATES = 64
RUN_LIMIT = 16
BOOT = 0x1000
BOOT_END = 0x101000
ENTRY_AT = 8
BOOT_SHIFTS = (0, 0x100000, 0x200000)
DECODER = (0x2C820090, 0x2C8200A0, 0x2C8200C0)
DECODER_REACH = 32
POINTER_REACH = 48
LUI_REACH = 8
OP_LUI, OP_LW = 0x0F, 0x23
REGISTER = 31
HALF = 0xFFFF
SIGN = 0x8000
WRAP = 0x10000
MAX_SIZE = 0x800000
NOUN = "prefix-coded stream"
NO_ROOM = "a changed prefix-coded stream no longer fits where it was"
State = tuple[int, int, int]


class FormatError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Stream:
    at: int
    size: int
    packed: int


def _step(data: bytes, at: int, state: State, produced: int) -> tuple[Token | None, int, State]:
    byte, at = data[at], at + 1
    offset, length, prefixes = state
    if byte < LITERAL_BASE:
        distance = ((offset << NIBBLE_BITS) | (byte & NIBBLE)) + 1
        if distance > produced:
            raise FormatError("a copy reaches before the start of the stream")
        count = (length << MID_BITS) + ((byte >> NIBBLE_BITS) & MID) + prefixes + MIN_COPY
        return Copy(distance, count), at, (0, 0, 0)
    if byte < PAIR_BASE:
        run = data[at : at + (length << NIBBLE_BITS) + (byte & NIBBLE) + 1]
        if len(run) != (length << NIBBLE_BITS) + (byte & NIBBLE) + 1:
            raise IndexError(at)
        return Literals(run), at + len(run), (offset, 0, 0)
    if byte < LENGTH_BASE:
        return None, at, ((byte & PAIR_OFFSET) >> PAIR_SHIFT, byte & PAIR_LENGTH, prefixes + 1)
    if byte < OFFSET_BASE:
        return None, at, (offset, (length << LENGTH_BITS) + (byte & LENGTH_MASK), prefixes + 1)
    return None, at, ((offset << OFFSET_BITS) + (byte & OFFSET_MASK), length, prefixes + 1)


def decode(data: bytes, at: int) -> tuple[tuple[Token, ...], int]:
    size = SIZE.unpack_from(data, at)[0]
    tokens: list[Token] = []
    here, produced, state = at + SIZE.size, 0, (0, 0, 0)
    try:
        while produced < size:
            token, here, state = _step(data, here, state, produced)
            tokens.extend([token] if token else [])
            produced += span(token) if token else 0
    except IndexError as error:
        raise FormatError("the stream ends before its data") from error
    if produced != size:
        raise FormatError("the stream does not decode to its size")
    return lzparse.merged(tokens), here - at


def _count(value: int, width: int) -> int:
    count = 0
    while value >> (width * count) and count < MAX_PREFIXES:
        count += 1
    return count


def _groups(value: int, width: int, count: int, base: int) -> bytes:
    mask = (1 << width) - 1
    return bytes(base | (value >> (width * index)) & mask for index in range(count - 1, -1, -1))


def _layout(distance: int, length: int) -> tuple[int, int, int] | None:
    offsets = _count((distance - 1) >> NIBBLE_BITS, OFFSET_BITS)
    lengths, value = 0, length - MIN_COPY - offsets
    while value >= 0 and value >> MID_BITS >= 1 << (LENGTH_BITS * lengths):
        lengths, value = lengths + 1, value - 1
    return (offsets, lengths, value) if value >= 0 else None


def _copy_bytes(distance: int, length: int) -> bytes | None:
    layout = _layout(distance, length)
    if layout is None:
        return None
    offsets, lengths, value = layout
    head = _groups((distance - 1) >> NIBBLE_BITS, OFFSET_BITS, offsets, OFFSET_BASE)
    middle = _groups(value >> MID_BITS, LENGTH_BITS, lengths, LENGTH_BASE)
    return head + middle + bytes((((value & MID) << NIBBLE_BITS) | ((distance - 1) & NIBBLE),))


def _literal_bytes(run: bytes) -> bytes:
    value = len(run) - 1
    prefix = _groups(
        value >> NIBBLE_BITS, LENGTH_BITS, _count(value >> NIBBLE_BITS, LENGTH_BITS), LENGTH_BASE
    )
    return prefix + bytes((LITERAL_BASE | (value & NIBBLE),)) + run


def _pieces(tokens: Sequence[Token], data: bytes) -> Iterator[bytes]:
    position, pending = 0, b""
    for token in tokens:
        encoded = None if isinstance(token, Literals) else _copy_bytes(token.distance, token.length)
        if encoded is None:
            pending += data[position : position + span(token)]
        else:
            yield from [_literal_bytes(pending)] if pending else []
            yield encoded
            pending = b""
        position += span(token)
    yield from [_literal_bytes(pending)] if pending else []


def encode(tokens: Sequence[Token], data: bytes) -> bytes:
    return SIZE.pack(len(data)) + b"".join(_pieces(tokens, data))


def _copy_cost(distance: int, length: int) -> int:
    layout = _layout(distance, length)
    return UNREACHED if layout is None else 1 + layout[0] + layout[1]


def cost(token: Token) -> int:
    if isinstance(token, Copy):
        return _copy_cost(token.distance, token.length)
    return len(_literal_bytes(token.data))


SHAPE = lzparse.Shape(WINDOW, MIN_COPY, MAX_COPY, CANDIDATES, 1, _copy_cost, 1, RUN_LIMIT)


def optimal(data: bytes, start: int, end: int) -> tuple[Token, ...]:
    return lzparse.optimal(data, start, end, SHAPE)


def _decoders(rom: bytes) -> Iterator[int]:
    words = range(BOOT, min(len(rom), BOOT_END) - 4 * DECODER_REACH, 4)
    for at in words:
        if WORD.unpack_from(rom, at)[0] != DECODER[0]:
            continue
        nearby = [WORD.unpack_from(rom, at + 4 * k)[0] for k in range(DECODER_REACH)]
        if all(word in nearby for word in DECODER):
            yield at


def _lui(rom: bytes, at: int, register: int) -> int | None:
    for here in range(at - 4, max(BOOT, at - 4 * LUI_REACH) - 4, -4):
        word = WORD.unpack_from(rom, here)[0]
        if word >> 26 == OP_LUI and (word >> 16) & REGISTER == register:
            return int(word) & HALF
    return None


def _pointers(rom: bytes, decoder: int) -> Iterator[int]:
    for at in range(max(BOOT, decoder - 4 * POINTER_REACH), decoder, 4):
        word = WORD.unpack_from(rom, at)[0]
        high = _lui(rom, at, (word >> 21) & REGISTER) if word >> 26 == OP_LW else None
        if high is not None:
            low = word & HALF
            yield (high << 16) + (low - WRAP if low & SIGN else low)


def _stream_at(rom: bytes, at: int) -> Stream | None:
    if not BOOT <= at < BOOT_END - SIZE.size:
        return None
    size = SIZE.unpack_from(rom, at)[0]
    if not 0 < size <= MAX_SIZE:
        return None
    try:
        _, used = decode(rom[:BOOT_END], at)
    except FormatError:
        return None
    return Stream(at, size, used)


def find_streams(rom: bytes) -> tuple[Stream, ...]:
    if len(rom) < BOOT_END:
        return ()
    entry = WORD.unpack_from(rom, ENTRY_AT)[0]
    places = {
        pointer - (entry - shift) + BOOT
        for decoder in _decoders(rom)
        for pointer in _pointers(rom, decoder)
        for shift in BOOT_SHIFTS
    }
    found = (_stream_at(rom, at) for at in sorted(places))
    return tuple(stream for stream in found if stream is not None)


def _edited(
    rom: bytes, stream: Stream, settings: Settings
) -> tuple[bytes, RegionEdit] | str | None:
    tokens, _ = decode(rom, stream.at)
    before = expand(tokens)
    edit = edit_region(before, settings)
    if not edit.changes:
        return None
    budget = stream.packed - SIZE.size
    improved = rewindow.improve(
        split(tokens, edit.data), edit.data, before, budget, optimal=optimal, cost=cost
    )
    body = encode(improved, edit.data)
    if len(body) > stream.packed:
        return NO_ROOM
    return body + bytes(stream.packed - len(body)), edit


def edit_streams(rom: bytes, settings: Settings) -> Packed | str:
    done: tuple[RegionEdit, ...] = ()
    spans: tuple[tuple[int, int], ...] = ()
    out, refusal = rom, NO_TARGETS
    for stream in find_streams(rom):
        result = _edited(rom, stream, settings)
        if isinstance(result, tuple):
            out = out[: stream.at] + result[0] + out[stream.at + stream.packed :]
            spans = (*spans, (stream.at, stream.at + stream.packed))
            done = (*done, result[1])
        refusal = result if isinstance(result, str) else refusal
    return Packed(out, done, len(done), NOUN, spans) if done else refusal
