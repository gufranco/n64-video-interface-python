from __future__ import annotations

import re
import struct
import zlib
from collections.abc import Sequence
from dataclasses import dataclass

from .checksum import verify
from .edits import (
    CHECKSUM_UNKNOWN,
    NO_TARGETS,
    Packed,
    PatchResult,
    RegionEdit,
    Settings,
    describe,
    edit_region,
)
from .seal import reseal

MAGIC = b"\x11\x72"
SIZE_WIDTHS = {MAGIC: 4, b"\x11\x73": 3}
MAGIC_PATTERN = re.compile(rb"\x11[\x72\x73]")
WINDOW = 0x200000
MIN_SIZE = 0x40
MAX_SIZE = 0x800000
ALIGN = 16
WORD = 0xFFFFFFFF
SHIFT_MASK = 0x17
RAW_DEFLATE = -15
LEVEL = 9
MEMORY_LEVELS = range(1, 10)
STRATEGIES = (zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED)
NO_CHECKSUM = "a changed block's integrity checksum could not be located, so it is left alone"
NO_ROOM = "a changed block no longer fits where it was"
NOUN = "compressed group"
NONZERO = re.compile(rb"[^\x00]")


@dataclass(frozen=True, slots=True)
class Stream:
    start: int
    end: int
    data: bytes
    magic: bytes


@dataclass(frozen=True, slots=True)
class Group:
    start: int
    end: int
    streams: tuple[Stream, ...]


def bk_crc(data: bytes) -> tuple[int, int]:
    total, mixed = 0, WORD
    for byte in data:
        total = (total + byte) & WORD
        mixed ^= (byte << (total & SHIFT_MASK)) & WORD
    return total, mixed


def _header(magic: bytes, size: int) -> bytes:
    return magic + size.to_bytes(SIZE_WIDTHS[magic], "big")


def _stream_at(rom: bytes, at: int) -> Stream | None:
    magic = rom[at : at + 2]
    header = len(magic) + SIZE_WIDTHS[magic]
    if at + header > len(rom):
        return None
    size = int.from_bytes(rom[at + len(magic) : at + header], "big")
    if not MIN_SIZE <= size <= MAX_SIZE:
        return None
    window = memoryview(rom)[at + header : at + header + WINDOW]
    inflater = zlib.decompressobj(RAW_DEFLATE)
    try:
        data = inflater.decompress(window, size + 1)
    except zlib.error:
        return None
    if not inflater.eof or len(data) != size:
        return None
    return Stream(at, at + header + len(window) - len(inflater.unused_data), data, magic)


def _streams(rom: bytes) -> list[Stream]:
    found: list[Stream] = []
    match = MAGIC_PATTERN.search(rom)
    while match:
        stream = _stream_at(rom, match.start())
        found.extend([stream] if stream else [])
        match = MAGIC_PATTERN.search(rom, stream.end if stream else match.start() + 1)
    return found


def find_groups(rom: bytes) -> tuple[Group, ...]:
    streams = _streams(rom)
    runs: list[list[Stream]] = []
    for stream in streams:
        if runs and runs[-1][-1].end == stream.start:
            runs[-1].append(stream)
        else:
            runs.append([stream])
    limits = [*(run[0].start for run in runs[1:]), len(rom)][: len(runs)]
    return tuple(
        Group(run[0].start, _padding_end(rom, run[-1].end, limit), tuple(run))
        for run, limit in zip(runs, limits, strict=True)
    )


def _padding_end(rom: bytes, at: int, limit: int) -> int:
    found = NONZERO.search(rom, at, limit)
    return found.start() if found else limit


def deflate(data: bytes) -> bytes:
    candidates = []
    for memory in MEMORY_LEVELS:
        for strategy in STRATEGIES:
            packer = zlib.compressobj(LEVEL, zlib.DEFLATED, RAW_DEFLATE, memory, strategy)
            candidates.append(packer.compress(data) + packer.flush())
    return min(candidates, key=len)


def repack(group: Group, contents: Sequence[bytes]) -> bytes | None:
    packed = b"".join(
        _header(stream.magic, len(data)) + deflate(data)
        for stream, data in zip(group.streams, contents, strict=True)
    )
    padded = packed + bytes(-len(packed) % ALIGN)
    span = group.end - group.start
    return padded + bytes(span - len(padded)) if len(padded) <= span else None


def _with_checksums(rom: bytes, group: Group, edits: Sequence[RegionEdit]) -> bytes | None:
    out = bytearray(rom)
    changed = [
        (s, e)
        for s, e in zip(group.streams, edits, strict=True)
        if e.data != s.data and s.magic == MAGIC
    ]
    for stream, edit in changed:
        at = rom.find(struct.pack(">2I", *bk_crc(stream.data)))
        if at < 0:
            return None
        struct.pack_into(">2I", out, at, *bk_crc(edit.data))
    return bytes(out)


def _repacked(rom: bytes, group: Group, edits: Sequence[RegionEdit]) -> bytes | str:
    packed = repack(group, [edit.data for edit in edits])
    if packed is None:
        return NO_ROOM
    sealed = _with_checksums(rom, group, edits)
    if sealed is None:
        return NO_CHECKSUM
    return sealed[: group.start] + packed + sealed[group.end :]


def edit_groups(rom: bytes, settings: Settings) -> Packed | str:
    plans = [(g, [edit_region(s.data, settings) for s in g.streams]) for g in find_groups(rom)]
    targeted = [(group, edits) for group, edits in plans if any(e.changes for e in edits)]
    if not targeted:
        return NO_TARGETS
    done: tuple[RegionEdit, ...] = ()
    out, groups, refusal = rom, 0, NO_TARGETS
    for group, edits in targeted:
        result = _repacked(out, group, edits)
        if isinstance(result, str):
            refusal = result
            continue
        out, done, groups = result, (*done, *edits), groups + 1
    return Packed(out, done, groups, NOUN) if groups else refusal


def patch(rom: bytes, settings: Settings) -> PatchResult:
    packed = edit_groups(rom, settings)
    if isinstance(packed, str):
        return PatchResult(False, packed)
    valid, cic = verify(rom)
    if not valid or cic is None:
        return PatchResult(False, CHECKSUM_UNKNOWN)
    reason = f"{describe(packed.edits, settings)}, {packed.where}"
    modes = sum(edit.modes_changed for edit in packed.edits)
    return PatchResult(True, reason, reseal(packed.data, cic), modes, (), cic)
