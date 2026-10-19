from __future__ import annotations

import struct
from dataclasses import dataclass

from . import rnc_codec, rnc_parse
from .edits import NO_TARGETS, Packed, RegionEdit, Settings, edit_region
from .rnc_codec import HEADER

MAGIC = b"RNC"
METHOD = 1
MAX_SIZE = 0x800000
LAYOUT = ">3sBIIHHBB"
NOUN = "RNC stream"
NO_ROOM = "a changed RNC stream no longer fits where it was"
CRC_POLYNOMIAL = 0xA001
BYTE = 0xFF
PACKED_SIZE = ">I"
PACKED_SIZE_AT = 8


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
    size: int
    packed: int
    crc: int
    leeway: int
    chunks: int

    @property
    def body(self) -> slice:
        return slice(self.at + HEADER, self.at + HEADER + self.packed)


def header(size: int, crc: int, body: bytes, leeway: int, chunks: int) -> bytes:
    return struct.pack(LAYOUT, MAGIC, METHOD, size, len(body), crc, crc16(body), leeway, chunks)


def _stream_at(rom: bytes, at: int) -> Stream | None:
    if at + HEADER > len(rom):
        return None
    _, method, size, packed, crc, packed_crc, leeway, chunks = struct.unpack_from(LAYOUT, rom, at)
    stream = Stream(at, size, packed, crc, leeway, chunks)
    if method != METHOD or not 0 < size <= MAX_SIZE or not 0 < packed <= MAX_SIZE:
        return None
    if stream.body.stop > len(rom) or crc16(rom[stream.body]) != packed_crc:
        return None
    return stream


def find_streams(rom: bytes) -> tuple[Stream, ...]:
    found: tuple[Stream, ...] = ()
    at = rom.find(MAGIC)
    while at >= 0:
        stream = _stream_at(rom, at)
        found = (*found, stream) if stream else found
        at = rom.find(MAGIC, stream.body.stop if stream else at + 1)
    return found


def leeway(data: bytes, at: int, size: int) -> int:
    packed = int(struct.unpack_from(PACKED_SIZE, data, at + PACKED_SIZE_AT)[0])
    return max(0, rnc_codec.read_all(data, at, size).margin - (size - packed))


def _edited(
    rom: bytes, stream: Stream, settings: Settings
) -> tuple[bytes, RegionEdit] | str | None:
    chunks = rnc_codec.decode(rom, stream.at, stream.size)
    edit = edit_region(rnc_codec.expand(chunks), settings)
    if not edit.changes:
        return None
    rebuilt = rnc_parse.improve(rnc_parse.split(chunks, edit.data), chunks, edit.data)
    body = rnc_codec.encode(rebuilt)
    replaced = header(stream.size, crc16(edit.data), body, stream.leeway, len(rebuilt)) + body
    if len(body) > stream.packed or leeway(replaced, 0, stream.size) > stream.leeway:
        return NO_ROOM
    return replaced + bytes(stream.packed - len(body)), edit


def edit_streams(rom: bytes, settings: Settings) -> Packed | str:
    done: tuple[RegionEdit, ...] = ()
    out, refusal = rom, NO_TARGETS
    for stream in find_streams(rom):
        result = _edited(rom, stream, settings)
        if isinstance(result, tuple):
            out = out[: stream.at] + result[0] + out[stream.body.stop :]
            done = (*done, result[1])
        refusal = result if isinstance(result, str) else refusal
    return Packed(out, done, len(done), NOUN) if done else refusal
