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
BYTE_BITS = 8
EMPTY_CODE = (0,) * 7
EMPTY_LENGTHS = bytes((0x00, 0x00, 0xFF, 0xFF))
EMPTY_STORED = bytes((0x00,)) + EMPTY_LENGTHS
TAIL_BLOCKS = 16
NO_ROOM = "a changed gzip member no longer fits where it was"
RECORD = struct.Struct(">2I")
RECORDS = range(0x1000, 0x10000, 4)
BOOT_END = 0x1000
MAX_SIZE = 0x800000


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
    return None if start is None else _inflated(rom, at, start)


def _inflated(rom: bytes, at: int, start: int) -> Member | None:
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


def _bare_at(rom: bytes, record: int) -> Member | None:
    at, length = RECORD.unpack_from(rom, record)
    end = at + length
    if at < BOOT_END or length <= TRAILER.size or end > len(rom) or rom[at : at + 3] == MAGIC:
        return None
    if not 0 < TRAILER.unpack_from(rom, end - TRAILER.size)[1] <= MAX_SIZE:
        return None
    member = _inflated(rom, at, at)
    return member if member is not None and member.length == length else None


def _bare_members(rom: bytes) -> list[Member]:
    candidates = (_bare_at(rom, record) for record in RECORDS if record + RECORD.size <= len(rom))
    return [member for member in candidates if member is not None]


def find_members(rom: bytes) -> tuple[Member, ...]:
    return tuple(sorted({*_headed_members(rom), *_bare_members(rom)}, key=lambda m: m.at))


def _headed_members(rom: bytes) -> list[Member]:
    found: list[Member] = []
    at = rom.find(MAGIC)
    while at >= 0:
        member = _member_at(rom, at)
        found.extend([member] if member else [])
        at = rom.find(MAGIC, at + member.length if member else at + 1)
    return found


def _open_stream(data: bytes) -> bytes:
    candidates = []
    for memory in MEMORY_LEVELS:
        for strategy in STRATEGIES:
            packer = zlib.compressobj(LEVEL, zlib.DEFLATED, RAW_DEFLATE, memory, strategy)
            candidates.append(packer.compress(data) + packer.flush(zlib.Z_SYNC_FLUSH))
    return min(candidates, key=len)


def _packed_bits(bits: list[int]) -> bytes:
    padded = bits + [0] * (-len(bits) % BYTE_BITS)
    return bytes(
        sum(bit << index for index, bit in enumerate(padded[at : at + BYTE_BITS]))
        for at in range(0, len(padded), BYTE_BITS)
    )


def _tail(fixed: int, stored: bool) -> bytes:
    bits = [0, 1, 0, *EMPTY_CODE] * fixed
    if stored:
        return _packed_bits([*bits, 1, 0, 0]) + EMPTY_LENGTHS
    return _packed_bits([*bits, 1, 1, 0, *EMPTY_CODE])


def _tails() -> dict[int, bytes]:
    tails: dict[int, bytes] = {}
    for fixed in range(TAIL_BLOCKS):
        for stored in (False, True):
            tail = _tail(fixed, stored)
            tails.setdefault(len(tail), tail)
    return tails


TAILS = _tails()


def deflate(data: bytes, size: int) -> bytes | None:
    stream = _open_stream(data)
    need = size - len(stream)
    for stored in range(max(0, need) // len(EMPTY_STORED) + 1):
        rest = need - stored * len(EMPTY_STORED)
        if rest in TAILS:
            return stream + EMPTY_STORED * stored + TAILS[rest]
    return None


def _edited(member: Member, settings: Settings) -> tuple[bytes, RegionEdit] | str | None:
    edit = edit_region(member.data, settings)
    if not edit.changes:
        return None
    body = deflate(edit.data, member.length - len(member.header) - TRAILER.size)
    if body is None:
        return NO_ROOM
    return member.header + body + TRAILER.pack(zlib.crc32(edit.data), len(edit.data)), edit


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
