from __future__ import annotations

import struct
from dataclasses import dataclass, replace
from typing import cast

from . import rnc2_codec, rnc2_parse, rnc_codec, rnc_parse
from .edits import NO_TARGETS, Packed, RegionEdit, Settings, edit_region
from .rnc2_codec import Op
from .rnc_codec import HEADER, Chunk, CodecError

MAGIC = b"RNC"
METHODS = (1, 2)
MAX_SIZE = 0x800000
LAYOUT = ">3sBIIHHBB"
NOUN = "RNC stream"
NO_ROOM = "a changed RNC stream no longer fits where it was"
Tokens = tuple[Chunk, ...] | tuple[Op, ...]
CRC_POLYNOMIAL = 0xA001
BYTE = 0xFF


def _table() -> tuple[int, ...]:
    def entry(value: int) -> int:
        for _ in range(8):
            value = (value >> 1) ^ CRC_POLYNOMIAL if value & 1 else value >> 1
        return value

    return tuple(entry(index) for index in range(256))


CRC_TABLE = _table()


def crc16(data: bytes) -> int:
    value = 0
    for byte in data:
        value = (value >> 8) ^ CRC_TABLE[(value ^ byte) & BYTE]
    return value


@dataclass(frozen=True, slots=True)
class Stream:
    at: int
    method: int
    size: int
    packed: int
    crc: int
    leeway: int
    chunks: int
    stamp: int | None = None

    @property
    def body(self) -> slice:
        return slice(self.at + HEADER, self.at + HEADER + self.packed)


def header(stream: Stream, crc: int, body: bytes) -> bytes:
    sealed = crc16(body) if stream.stamp is None else stream.stamp
    fields = (stream.method, stream.size, len(body), crc, sealed, stream.leeway, stream.chunks)
    return struct.pack(LAYOUT, MAGIC, *fields)


def _stream_at(rom: bytes, at: int) -> Stream | None:
    if at + HEADER > len(rom):
        return None
    _, method, size, packed, crc, packed_crc, leeway, chunks = struct.unpack_from(LAYOUT, rom, at)
    stream = Stream(at, method, size, packed, crc, leeway, chunks)
    if method not in METHODS or not 0 < size <= MAX_SIZE or not 0 < packed <= MAX_SIZE:
        return None
    if stream.body.stop > len(rom):
        return None
    if crc16(rom[stream.body]) == packed_crc:
        return stream
    return replace(stream, stamp=packed_crc) if _unpacks(rom, stream) else None


def _unpacks(rom: bytes, stream: Stream) -> bool:
    try:
        data = _decoded(rom, stream)[1][: stream.size]
    except (CodecError, IndexError):
        return False
    return len(data) == stream.size and crc16(data) == stream.crc


def find_streams(rom: bytes) -> tuple[Stream, ...]:
    found: list[Stream] = []
    at = rom.find(MAGIC)
    while at >= 0:
        stream = _stream_at(rom, at)
        found.extend([stream] if stream else [])
        at = rom.find(MAGIC, stream.body.stop if stream else at + 1)
    return tuple(found)


def leeway(data: bytes, stream: Stream) -> int:
    if stream.method == METHODS[0]:
        margin = rnc_codec.read_all(data, stream.at, stream.size).margin
    else:
        margin = rnc2_codec.read_all(data, stream.at, stream.size)[1].margin
    return max(0, margin - (stream.size - stream.packed))


def _decoded(rom: bytes, stream: Stream) -> tuple[Tokens, bytes]:
    if stream.method == METHODS[0]:
        chunks = rnc_codec.decode(rom, stream.at, stream.size)
        return chunks, rnc_codec.expand(chunks)
    ops = rnc2_codec.decode(rom, stream.at, stream.size)
    return ops, rnc2_codec.expand(ops)


def _rebuilt(stream: Stream, tokens: Tokens, desired: bytes) -> tuple[bytes, int]:
    if stream.method == METHODS[0]:
        chunks = cast("tuple[Chunk, ...]", tokens)
        rebuilt = rnc_parse.improve(rnc_parse.split(chunks, desired), chunks, desired)
        return rnc_codec.encode(rebuilt), len(rebuilt)
    ops = cast("tuple[Op, ...]", tokens)
    return rnc2_codec.encode(
        rnc2_parse.improve(rnc2_parse.split(ops, desired), ops, desired)
    ), stream.chunks


def _edited(
    rom: bytes, stream: Stream, settings: Settings
) -> tuple[bytes, RegionEdit] | str | None:
    tokens, data = _decoded(rom, stream)
    edit = edit_region(data, settings)
    if not edit.changes:
        return None
    body, chunks = _rebuilt(stream, tokens, edit.data)
    fresh = replace(stream, at=0, packed=len(body), crc=crc16(edit.data), chunks=chunks)
    replaced = header(fresh, fresh.crc, body) + body
    if len(body) > stream.packed or leeway(replaced, fresh) > leeway(rom, stream):
        return NO_ROOM
    return replaced + bytes(stream.packed - len(body)), edit


def edit_streams(rom: bytes, settings: Settings) -> Packed | str:
    done: tuple[RegionEdit, ...] = ()
    spans: tuple[tuple[int, int], ...] = ()
    out, refusal = rom, NO_TARGETS
    for stream in find_streams(rom):
        result = _edited(rom, stream, settings)
        if isinstance(result, tuple):
            out = out[: stream.at] + result[0] + out[stream.body.stop :]
            spans = (*spans, (stream.at, stream.body.stop))
            done = (*done, result[1])
        refusal = result if isinstance(result, str) else refusal
    return Packed(out, done, len(done), NOUN, spans) if done else refusal
