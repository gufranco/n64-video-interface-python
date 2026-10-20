from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from .edits import NO_TARGETS, Packed, RegionEdit, Settings, edit_region

MAGIC = b"\x1f\x8b\x08"
FIXED_HEADER = 10
NAME_FLAG = 0x08
KNOWN_FLAGS = NAME_FLAG
TRAILER = struct.Struct("<2I")
RAW_DEFLATE = -15
WINDOW = 0x400000
LEVEL = 9
MEMORY_LEVELS = range(1, 10)
STRATEGIES = (zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED)
NOUN = "gzip member"
NO_ROOM = "a changed gzip member no longer fits where it was"


@dataclass(frozen=True, slots=True)
class Member:
    at: int
    header: bytes
    data: bytes
    length: int

    @property
    def name(self) -> bytes:
        return self.header[FIXED_HEADER:-1] if self.header[3] & NAME_FLAG else b""


def _header_end(rom: bytes, at: int) -> int | None:
    flags = rom[at + 3] if at + FIXED_HEADER <= len(rom) else 0xFF
    if flags & ~KNOWN_FLAGS:
        return None
    if not flags & NAME_FLAG:
        return at + FIXED_HEADER
    end = rom.find(b"\x00", at + FIXED_HEADER)
    return end + 1 if end >= 0 else None


def _member_at(rom: bytes, at: int) -> Member | None:
    start = _header_end(rom, at)
    if start is None:
        return None
    inflater = zlib.decompressobj(RAW_DEFLATE)
    window = rom[start : start + WINDOW]
    try:
        data = inflater.decompress(window)
    except zlib.error:
        return None
    end = start + len(window) - len(inflater.unused_data)
    if not inflater.eof or end + TRAILER.size > len(rom):
        return None
    if TRAILER.unpack_from(rom, end) != (zlib.crc32(data), len(data) & 0xFFFFFFFF):
        return None
    return Member(at, rom[at:start], data, end + TRAILER.size - at)


def find_members(rom: bytes) -> tuple[Member, ...]:
    found: list[Member] = []
    at = rom.find(MAGIC)
    while at >= 0:
        member = _member_at(rom, at)
        found.extend([member] if member else [])
        at = rom.find(MAGIC, at + member.length if member else at + 1)
    return tuple(found)


def deflate(data: bytes) -> bytes:
    candidates = []
    for memory in MEMORY_LEVELS:
        for strategy in STRATEGIES:
            packer = zlib.compressobj(LEVEL, zlib.DEFLATED, RAW_DEFLATE, memory, strategy)
            candidates.append(packer.compress(data) + packer.flush())
    return min(candidates, key=len)


def _edited(member: Member, settings: Settings) -> tuple[bytes, RegionEdit] | str | None:
    edit = edit_region(member.data, settings)
    if not edit.changes:
        return None
    body = member.header + deflate(edit.data) + TRAILER.pack(zlib.crc32(edit.data), len(edit.data))
    return NO_ROOM if len(body) > member.length else (body + bytes(member.length - len(body)), edit)


def edit_members(rom: bytes, settings: Settings) -> Packed | str:
    done: tuple[RegionEdit, ...] = ()
    out, refusal = rom, NO_TARGETS
    for member in find_members(rom):
        result = _edited(member, settings)
        if isinstance(result, tuple):
            out = out[: member.at] + result[0] + out[member.at + member.length :]
            done = (*done, result[1])
        refusal = result if isinstance(result, str) else refusal
    return Packed(out, done, len(done), NOUN) if done else refusal
