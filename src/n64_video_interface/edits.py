from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass

from . import hal, rdp, viconst, vifilter
from .ctrl import apply_changes
from .features import SWITCHES, SwitchRequest, find_special_features, find_switch_requests
from .modes import find_mode_tables

NO_TABLE = "no video mode table could be proven present"
CHECKSUM_UNKNOWN = (
    "the existing checksum does not validate, so the boot chip is unknown "
    "and the result could not be resealed correctly"
)
NO_TARGETS = "no compressed block holds anything this can change"
NO_MICROCODE_NOTE = "no supported microcode was found, so the RDP dither is unchanged"
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
DEDITHER = "dedither"
HAL_BITS = (
    ("dedither", "dither_filter", hal.DEDITHER),
    ("divot", "divot", 0x0010),
    ("gamma dither", "gamma_dither", 0x0004),
    ("gamma", "gamma", 0x0008),
)
HAL_BEFORE = 0x200
HAL_AFTER = 0x300
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
    spans: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class RegionEdit:
    data: bytes
    changes: tuple[Change, ...]
    entries: int
    modes_changed: int
    requests: tuple[SwitchRequest, ...]
    microcodes: int
    filters: int = 0
    setters: int = 0
    constants: int = 0


@dataclass(frozen=True, slots=True)
class Packed:
    data: bytes
    edits: tuple[RegionEdit, ...]
    count: int
    noun: str
    spans: tuple[tuple[int, int], ...] = ()

    @property
    def where(self) -> str:
        return f"in {self.count} {self.noun}{'' if self.count == 1 else 's'}"


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
    setters = _hal_changes(data, settings, requests)
    constants = viconst.find_constants(data, settings)
    caves = [_dead_block(data, r) for r in requests if r.switch == DEDITHER]
    switched, filters, stores = vifilter.install(
        _write(data, (*table, *switches, *setters, *constants)),
        caves,
        vifilter.mask_for(settings),
    )
    microcodes = rdp.count_microcodes(data) if settings.rdp_bits else 0
    dithered, rdp_changes = (
        rdp.disable_dither(switched, settings.rdp_bits) if microcodes else (switched, ())
    )
    changes = (*table, *switches, *setters, *constants, *filters, *rdp_changes)
    found = (len(entries), len(table), requests, microcodes, stores, len(setters))
    return RegionEdit(dithered, changes, *found, len(constants))


def _hal_changes(
    data: bytes, settings: Settings, requests: Sequence[SwitchRequest]
) -> tuple[Change, ...]:
    guarded = set() if vifilter.integrity_checked(data) else {r.switch for r in requests}
    bits = {
        bit
        for switch, name, bit in HAL_BITS
        if getattr(settings, name) is False and switch not in guarded
    }
    windows = [
        range(max(0, s.offset - HAL_BEFORE), s.masks["antialias"] + HAL_AFTER)
        for s in find_special_features(data)
    ]
    setters = tuple(change for window in windows for change in hal.find_setters(data, window, bits))
    rebuilt = tuple(
        change
        for window in windows
        if settings.antialiasing is False
        for change in hal.find_aa_rebuild(data, window)
    )
    return (*setters, *rebuilt)


def _dead_block(data: bytes, request: SwitchRequest) -> tuple[int, int]:
    target = vifilter.branch_target(request.branch, _word(data, request.branch))
    return request.branch + 8, target


def describe(edits: Sequence[RegionEdit], settings: Settings) -> str:
    entries = sum(edit.entries for edit in edits)
    modes = sum(edit.modes_changed for edit in edits)
    parts = [f"{modes} of {entries} mode entries changed"] if entries else []
    if settings.wants_table and not entries:
        parts.append(f"{NO_TABLE}, so anti-aliasing and the table settings are unchanged")
    for switch in SWITCHES:
        count = sum(1 for edit in edits for request in edit.requests if request.switch == switch)
        parts.extend([f"{count} {switch} request neutralised"] if count else [])
    microcodes = sum(edit.microcodes for edit in edits)
    if settings.rdp_bits and microcodes:
        noun = "microcode" if microcodes == 1 else "microcodes"
        label = RDP_DITHER_LABELS[settings.rdp_bits]
        parts.append(f"{label} dither forced off in {microcodes} {noun}")
    parts.extend([NO_MICROCODE_NOTE] if settings.rdp_bits and not microcodes else [])
    setters = sum(edit.setters for edit in edits)
    parts.extend(
        [f"{setters} VI {'setter' if setters == 1 else 'setters'} neutralised"] if setters else []
    )
    constants = sum(edit.constants for edit in edits)
    parts.extend(
        [f"{constants} VI control {'constant' if constants == 1 else 'constants'} changed"]
        if constants
        else []
    )
    stores = sum(edit.filters for edit in edits)
    parts.extend(
        [f"{stores} VI control {'store' if stores == 1 else 'stores'} filtered"] if stores else []
    )
    return ", ".join(parts)
