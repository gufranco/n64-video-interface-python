from __future__ import annotations

import struct
from dataclasses import dataclass

from .ctrl import ViCtrl, plausible

VSYNC_BY_STANDARD = {524: "NTSC", 525: "NTSC", 624: "PAL", 625: "PAL"}
PLAUSIBLE_WIDTHS = (256, 292, 320, 384, 512, 576, 640, 1280)
VSYNC_OFFSET_FROM_CTRL = 12
TYPE_WORD_BEFORE_CTRL = 4
OSVIMODE_WORDS = 20
OSVIMODE_SIZE = OSVIMODE_WORDS * 4
OSVIMODE_TYPE_LIMIT = 56
OSVIMODE_TYPE_SHIFT = 24
OSVIMODE_PADDING_MASK = 0x00FFFFFF
OSVIMODE_NONZERO_WORDS = (3, 5, 6, 7, 8, 11)


@dataclass(frozen=True, slots=True)
class ModeEntry:
    ctrl_offset: int
    ctrl: int
    width: int
    vsync: int
    standard: str

    @property
    def decoded(self) -> ViCtrl:
        return ViCtrl(self.ctrl)


def has_osvimode_shape(rom: bytes, ctrl_offset: int) -> bool:
    start = ctrl_offset - TYPE_WORD_BEFORE_CTRL
    if start < 0 or start + OSVIMODE_SIZE > len(rom):
        return False
    words = struct.unpack_from(f">{OSVIMODE_WORDS}I", rom, start)
    kind = words[0]
    if kind >> OSVIMODE_TYPE_SHIFT >= OSVIMODE_TYPE_LIMIT or kind & OSVIMODE_PADDING_MASK:
        return False
    return all(words[index] for index in OSVIMODE_NONZERO_WORDS)


def _entry_at(rom: bytes, ctrl_offset: int, vsync: int) -> ModeEntry | None:
    if ctrl_offset % 4 or not has_osvimode_shape(rom, ctrl_offset):
        return None
    value, width = struct.unpack_from(">2I", rom, ctrl_offset)
    if not plausible(value) or width not in PLAUSIBLE_WIDTHS:
        return None
    return ModeEntry(ctrl_offset, value, width, vsync, VSYNC_BY_STANDARD[vsync])


def _hits(rom: bytes, needle: bytes) -> list[int]:
    found: list[int] = []
    at = rom.find(needle)
    while at >= 0:
        found.append(at)
        at = rom.find(needle, at + 1)
    return found


def find_mode_tables(rom: bytes) -> tuple[ModeEntry, ...]:
    candidates = (
        _entry_at(rom, at - VSYNC_OFFSET_FROM_CTRL, vsync)
        for vsync in VSYNC_BY_STANDARD
        for at in _hits(rom, struct.pack(">I", vsync))
    )
    found = [entry for entry in candidates if entry is not None]
    return tuple(sorted(found, key=lambda entry: entry.ctrl_offset))
