from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass

from . import rdp
from .ctrl import apply_changes
from .features import SWITCHES, SwitchRequest, find_switch_requests
from .modes import find_mode_tables

NO_TABLE = "no video mode table could be proven present"
CHECKSUM_UNKNOWN = (
    "the existing checksum does not validate, so the boot chip is unknown "
    "and the result could not be resealed correctly"
)
TABLE_SETTINGS = ("antialiasing", "divot", "gamma_dither", "gamma")
SWITCH_SETTINGS = (
    ("dedither", "dither_filter"),
    ("divot", "divot"),
    ("gamma dither", "gamma_dither"),
    ("gamma", "gamma"),
)
RDP_COLOR_DITHER = 0xC0
RDP_ALPHA_DITHER = 0x30
RDP_DITHER_LABELS = {
    RDP_COLOR_DITHER: "colour",
    RDP_ALPHA_DITHER: "alpha",
    RDP_COLOR_DITHER | RDP_ALPHA_DITHER: "colour and alpha",
}
GUARD_KEEP = 0xFFFF0000
Change = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class Settings:
    antialiasing: bool | None = None
    divot: bool | None = None
    gamma_dither: bool | None = None
    gamma: bool | None = None
    dither_filter: bool | None = None
    rdp_color_dither: bool | None = None
    rdp_alpha_dither: bool | None = None

    @property
    def table(self) -> dict[str, bool | None]:
        return {name: getattr(self, name) for name in TABLE_SETTINGS}

    @property
    def wants_table(self) -> bool:
        return any(value is not None for value in self.table.values())

    @property
    def rdp_bits(self) -> int:
        color = RDP_COLOR_DITHER if self.rdp_color_dither is False else 0
        return color | (RDP_ALPHA_DITHER if self.rdp_alpha_dither is False else 0)


@dataclass(frozen=True, slots=True)
class PatchResult:
    applied: bool
    reason: str
    data: bytes = b""
    modes_changed: int = 0
    changes: tuple[Change, ...] = ()
    cic: str | None = None


@dataclass(frozen=True, slots=True)
class RegionEdit:
    data: bytes
    changes: tuple[Change, ...]
    entries: int
    modes_changed: int
    requests: tuple[SwitchRequest, ...]
    microcodes: int


def _word(data: bytes, at: int) -> int:
    return int(struct.unpack_from(">I", data, at)[0])


def _requests(data: bytes, settings: Settings) -> tuple[SwitchRequest, ...]:
    return tuple(
        request
        for switch, name in SWITCH_SETTINGS
        if getattr(settings, name) is False
        for request in find_switch_requests(data, switch)
    )


def _write(data: bytes, changes: tuple[Change, ...]) -> bytes:
    out = bytearray(data)
    for offset, _, after in changes:
        struct.pack_into(">I", out, offset, after)
    return bytes(out)


def edit_region(data: bytes, settings: Settings) -> RegionEdit:
    entries = find_mode_tables(data) if settings.wants_table else ()
    table = tuple(
        (entry.ctrl_offset, entry.ctrl, apply_changes(entry.ctrl, **settings.table))
        for entry in entries
    )
    table = tuple(change for change in table if change[1] != change[2])
    requests = _requests(data, settings)
    switches = tuple(
        (r.guard, _word(data, r.guard), _word(data, r.guard) & GUARD_KEEP) for r in requests
    )
    edited = _write(data, (*table, *switches))
    microcodes = rdp.count_microcodes(data) if settings.rdp_bits else 0
    dithered, rdp_changes = (
        rdp.disable_dither(edited, settings.rdp_bits) if microcodes else (edited, ())
    )
    changes = (*table, *switches, *rdp_changes)
    return RegionEdit(dithered, changes, len(entries), len(table), requests, microcodes)


def describe(edits: Sequence[RegionEdit], settings: Settings) -> str:
    entries = sum(edit.entries for edit in edits)
    modes = sum(edit.modes_changed for edit in edits)
    parts = [f"{modes} of {entries} mode entries changed"] if entries else []
    if settings.wants_table and not entries:
        parts.append(f"{NO_TABLE}, so anti-aliasing and the table settings are unchanged")
    for switch in SWITCHES:
        count = sum(1 for edit in edits for request in edit.requests if request.switch == switch)
        parts.extend([f"{count} {switch} request neutralised"] if count else [])
    if settings.rdp_bits:
        microcodes = sum(edit.microcodes for edit in edits)
        noun = "microcode" if microcodes == 1 else "microcodes"
        label = RDP_DITHER_LABELS[settings.rdp_bits]
        parts.append(f"{label} dither forced off in {microcodes} {noun}")
    return ", ".join(parts)
