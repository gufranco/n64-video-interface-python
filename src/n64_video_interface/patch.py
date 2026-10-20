from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields, replace

from . import bitlz, gzmember, rarezip, refpack, rnc
from .byteorder import swap, to_big_endian
from .checksum import verify
from .edits import (
    CHECKSUM_UNKNOWN,
    NO_TABLE,
    NO_TARGETS,
    Packed,
    PatchResult,
    RegionEdit,
    Settings,
    describe,
    edit_region,
)
from .seal import reseal

__all__ = [
    "ALREADY_OFF",
    "CHECKSUM_UNKNOWN",
    "DEDITHER_ON",
    "NOTHING_REQUESTED",
    "NO_CHANGE_NEEDED",
    "NO_MICROCODE",
    "NO_TABLE",
    "RDP_DITHER_ON",
    "PatchResult",
    "Settings",
    "safe_patch",
]

NOTHING_REQUESTED = "nothing requested"
DEDITHER_ON = "the dedither filter can be switched off here, never back on"
RDP_DITHER_ON = "the RDP dither can be switched off here, never back on"
NO_MICROCODE = "no supported microcode was found, so the RDP dither cannot be changed"
ALREADY_OFF = "this ROM has no way to switch the dedither filter on, so it is already off"
NO_CHANGE_NEEDED = "every mode already has the requested settings"
PACKERS = (
    rarezip.edit_groups,
    rnc.edit_streams,
    bitlz.edit_segments,
    refpack.edit_streams,
    gzmember.edit_members,
)


def _request_refusal(settings: Settings) -> str | None:
    if all(getattr(settings, field.name) is None for field in fields(settings)):
        return NOTHING_REQUESTED
    if settings.dither_filter:
        return DEDITHER_ON
    if settings.rdp_color_dither or settings.rdp_alpha_dither:
        return RDP_DITHER_ON
    return None


def _nothing_reason(edits: Sequence[RegionEdit], settings: Settings) -> str:
    entries = sum(edit.entries for edit in edits)
    requests = sum(len(edit.requests) for edit in edits)
    if settings.rdp_bits and not sum(edit.microcodes for edit in edits):
        return NO_MICROCODE
    if settings.wants_table and not entries and not requests:
        return NO_TABLE
    if not settings.wants_table and not requests and not settings.rdp_bits:
        return ALREADY_OFF
    return NO_CHANGE_NEEDED


def _compressed(
    data: bytes, settings: Settings
) -> tuple[bytes, tuple[RegionEdit, ...], tuple[str, ...]]:
    edits: tuple[RegionEdit, ...] = ()
    notes: tuple[str, ...] = ()
    for packer in PACKERS:
        packed = packer(data, settings)
        if isinstance(packed, Packed):
            data, edits, notes = packed.data, (*edits, *packed.edits), (*notes, packed.where)
        elif packed != NO_TARGETS:
            notes = (*notes, f"compressed code left alone: {packed}")
    return data, edits, notes


def _patch_big_endian(rom: bytes, settings: Settings) -> PatchResult:
    refusal = _request_refusal(settings)
    if refusal is not None:
        return PatchResult(False, refusal)
    direct = edit_region(rom, settings)
    data, packed, notes = _compressed(direct.data, settings)
    edits = (direct, *packed)
    if not any(edit.changes for edit in edits):
        return PatchResult(False, _nothing_reason(edits, settings))
    valid, cic = verify(rom)
    if not valid or cic is None:
        return PatchResult(False, CHECKSUM_UNKNOWN)
    modes = sum(edit.modes_changed for edit in edits)
    reason = ", ".join((describe(edits, settings), *notes))
    return PatchResult(True, reason, reseal(data, cic), modes, direct.changes, cic)


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
