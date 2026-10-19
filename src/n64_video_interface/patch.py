from __future__ import annotations

import struct
from dataclasses import dataclass, fields, replace

from . import rdp
from .byteorder import swap, to_big_endian
from .checksum import verify
from .ctrl import apply_changes
from .features import SWITCHES, SwitchRequest, find_switch_requests
from .modes import ModeEntry, find_mode_tables
from .seal import reseal

NOTHING_REQUESTED = "nothing requested"
DEDITHER_ON = "the dedither filter can be switched off here, never back on"
RDP_DITHER_ON = "the RDP dither can be switched off here, never back on"
NO_TABLE = "no video mode table could be proven present"
NO_MICROCODE = "no supported microcode was found, so the RDP dither cannot be changed"
ALREADY_OFF = "this ROM has no way to switch the dedither filter on, so it is already off"
CHECKSUM_UNKNOWN = (
    "the existing checksum does not validate, so the boot chip is unknown "
    "and the result could not be resealed correctly"
)
NO_CHANGE_NEEDED = "every mode already has the requested settings"
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
class Plan:
    entries: tuple[ModeEntry, ...]
    requests: tuple[SwitchRequest, ...]
    microcodes: int
    cic: str


def _request_refusal(settings: Settings) -> str | None:
    if all(getattr(settings, field.name) is None for field in fields(settings)):
        return NOTHING_REQUESTED
    if settings.dither_filter:
        return DEDITHER_ON
    if settings.rdp_color_dither or settings.rdp_alpha_dither:
        return RDP_DITHER_ON
    return None


def _requests(rom: bytes, settings: Settings) -> tuple[SwitchRequest, ...]:
    return tuple(
        request
        for switch, name in SWITCH_SETTINGS
        if getattr(settings, name) is False
        for request in find_switch_requests(rom, switch)
    )


def _plan(rom: bytes, settings: Settings) -> Plan | str:
    entries = find_mode_tables(rom) if settings.wants_table else ()
    requests = _requests(rom, settings)
    microcodes = rdp.count_microcodes(rom) if settings.rdp_bits else 0
    if settings.rdp_bits and not microcodes:
        return NO_MICROCODE
    if settings.wants_table and not entries and not requests:
        return NO_TABLE
    if not settings.wants_table and not requests and not settings.rdp_bits:
        return ALREADY_OFF
    valid, cic = verify(rom)
    if not valid or cic is None:
        return CHECKSUM_UNKNOWN
    return Plan(entries, requests, microcodes, cic)


def _word(rom: bytes, at: int) -> int:
    return int(struct.unpack_from(">I", rom, at)[0])


def _table_changes(plan: Plan, settings: Settings) -> tuple[Change, ...]:
    edited = (
        (e.ctrl_offset, e.ctrl, apply_changes(e.ctrl, **settings.table)) for e in plan.entries
    )
    return tuple(change for change in edited if change[1] != change[2])


def _switch_changes(rom: bytes, plan: Plan) -> tuple[Change, ...]:
    return tuple(
        (request.guard, _word(rom, request.guard), _word(rom, request.guard) & GUARD_KEEP)
        for request in plan.requests
    )


def _edit(rom: bytes, plan: Plan, settings: Settings) -> tuple[bytes, tuple[Change, ...], int]:
    table = _table_changes(plan, settings)
    changes = (*table, *_switch_changes(rom, plan))
    out = bytearray(rom)
    for offset, _, after in changes:
        struct.pack_into(">I", out, offset, after)
    if not settings.rdp_bits:
        return bytes(out), changes, len(table)
    dithered, rdp_changes = rdp.disable_dither(bytes(out), settings.rdp_bits)
    return dithered, (*changes, *rdp_changes), len(table)


def _describe(plan: Plan, settings: Settings, modes_changed: int) -> str:
    parts = [f"{modes_changed} of {len(plan.entries)} mode entries changed"] if plan.entries else []
    if settings.wants_table and not plan.entries:
        parts.append(f"{NO_TABLE}, so anti-aliasing and the table settings are unchanged")
    for switch in SWITCHES:
        count = sum(1 for request in plan.requests if request.switch == switch)
        parts.extend([f"{count} {switch} request neutralised"] if count else [])
    if settings.rdp_bits:
        noun = "microcode" if plan.microcodes == 1 else "microcodes"
        label = RDP_DITHER_LABELS[settings.rdp_bits]
        parts.append(f"{label} dither forced off in {plan.microcodes} {noun}")
    return ", ".join(parts)


def _patch_big_endian(rom: bytes, settings: Settings) -> PatchResult:
    refusal = _request_refusal(settings)
    if refusal is not None:
        return PatchResult(False, refusal)
    plan = _plan(rom, settings)
    if isinstance(plan, str):
        return PatchResult(False, plan)
    edited, changes, modes_changed = _edit(rom, plan, settings)
    if not changes:
        return PatchResult(False, NO_CHANGE_NEEDED)
    reason = _describe(plan, settings, modes_changed)
    return PatchResult(True, reason, reseal(edited, plan.cic), modes_changed, changes, plan.cic)


def safe_patch(
    rom: bytes,
    *,
    antialiasing: bool | None = None,
    divot: bool | None = None,
    gamma_dither: bool | None = None,
    gamma: bool | None = None,
    dither_filter: bool | None = None,
    rdp_color_dither: bool | None = None,
    rdp_alpha_dither: bool | None = None,
) -> PatchResult:
    big, order = to_big_endian(rom)
    settings = Settings(
        antialiasing, divot, gamma_dither, gamma, dither_filter, rdp_color_dither, rdp_alpha_dither
    )
    result = _patch_big_endian(big, settings)
    return replace(result, data=swap(result.data, order)) if result.applied else result
