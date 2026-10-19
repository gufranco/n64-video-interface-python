"""Reading and changing the N64 Video Interface configuration inside a ROM.

The blur people dislike on real hardware is mostly not anti-aliasing. The VI has
a dedither filter, bit 16 of VI_CTRL, whose job is to reconstruct a 32-bit image
from a dithered 16-bit framebuffer, and it is that filter which softens the
picture. Edge anti-aliasing, bits 9:8, is a separate thing.

The two are coupled in hardware. The dedither filter only behaves correctly when
AA_MODE is REPLICATE or AA_ALWAYS, so libultra's `osViSetSpecialFeatures` forces
AA_MODE when the filter is switched on and restores it from the mode table when
switched off. That is why turning the filter off is the small, safe change and
clearing AA_MODE is the larger one that trades softness for shimmer.

Two places in a ROM decide this:

  the VI mode table, an array of OSViMode structs the game hands to the library,
  whose ctrl field holds the values above

  osViSetSpecialFeatures itself, which masks those bits at runtime according to
  what the game asks for

This module locates both by signature rather than by per-game offsets, so one
implementation covers any title built against the standard library. Every field
name and bit position below is from the published hardware documentation, and the
mode table signature was verified against retail ROMs before being relied on.
"""

from __future__ import annotations

import bisect
import struct
from dataclasses import dataclass, replace

from . import rdp
from .byteorder import ByteOrder, detect, swap

CTRL_TYPE_MASK = 0x00003
CTRL_GAMMA_DITHER = 0x00004
CTRL_GAMMA = 0x00008
CTRL_DIVOT = 0x00010
CTRL_VBUS_CLOCK = 0x00020
CTRL_SERRATE = 0x00040
CTRL_TEST_MODE = 0x00080
CTRL_AA_MASK = 0x00300
CTRL_KILL_WE = 0x00800
CTRL_PIXEL_ADVANCE_MASK = 0x0F000
CTRL_DITHER_FILTER = 0x10000

AA_SHIFT = 8
AA_ALWAYS, AA_NEEDED, AA_RESAMPLE, AA_REPLICATE = 0, 1, 2, 3

AA_NAMES = {
    AA_ALWAYS: "AA_ALWAYS (anti-aliasing on)",
    AA_NEEDED: "AA_NEEDED (anti-aliasing on)",
    AA_RESAMPLE: "RESAMPLE (anti-aliasing off)",
    AA_REPLICATE: "REPLICATE (anti-aliasing off, no resample)",
}

PIXEL_TYPES = {0: "blank", 1: "reserved", 2: "16-bit", 3: "32-bit"}
PIXEL_32_BIT = 3

VSYNC_BY_STANDARD = {524: "NTSC", 525: "NTSC", 624: "PAL", 625: "PAL"}
PLAUSIBLE_WIDTHS = (256, 320, 384, 512, 576, 640, 1280)


MASK_CONSTANTS = (
    ("gamma", 0xFFF7),
    ("gamma_dither", 0xFFFB),
    ("divot", 0xFFEF),
    ("antialias", 0xFCFF),
)
MASK_SEARCH_WINDOW = 256
OP_ADDIU = 0x09
IMMEDIATE_OFFSET = 2

MODE_ENTRY_STRIDE = 4


@dataclass(frozen=True)
class ViCtrl:
    raw: int

    @property
    def pixel_type(self) -> int:
        return self.raw & CTRL_TYPE_MASK

    @property
    def gamma_dither(self) -> bool:
        return bool(self.raw & CTRL_GAMMA_DITHER)

    @property
    def gamma(self) -> bool:
        return bool(self.raw & CTRL_GAMMA)

    @property
    def divot(self) -> bool:
        return bool(self.raw & CTRL_DIVOT)

    @property
    def serrate(self) -> bool:
        return bool(self.raw & CTRL_SERRATE)

    @property
    def aa_mode(self) -> int:
        return (self.raw & CTRL_AA_MASK) >> AA_SHIFT

    @property
    def antialiasing(self) -> bool:
        return self.aa_mode in (AA_ALWAYS, AA_NEEDED)

    @property
    def dither_filter(self) -> bool:
        return bool(self.raw & CTRL_DITHER_FILTER)

    @property
    def pixel_advance(self) -> int:
        return (self.raw & CTRL_PIXEL_ADVANCE_MASK) >> 12

    @property
    def aa_name(self) -> str:
        return AA_NAMES[self.aa_mode]

    @property
    def type_name(self) -> str:
        return PIXEL_TYPES[self.pixel_type]

    @property
    def blur_sources(self) -> tuple[str, ...]:
        out = []
        if self.dither_filter:
            out.append("dither filter")
        if self.divot:
            out.append("divot")
        if self.gamma_dither:
            out.append("gamma dither")
        return tuple(out)


def decode_ctrl(value: int) -> ViCtrl:
    return ViCtrl(raw=value & 0xFFFFFFFF)


def apply_changes(
    value: int,
    *,
    dither_filter: bool | None = None,
    divot: bool | None = None,
    gamma_dither: bool | None = None,
    gamma: bool | None = None,
    antialiasing: bool | None = None,
) -> int:
    """Set only the requested bits, leaving pixel type, serrate and timing alone."""
    out = value
    for flag, bit in (
        (dither_filter, CTRL_DITHER_FILTER),
        (divot, CTRL_DIVOT),
        (gamma_dither, CTRL_GAMMA_DITHER),
        (gamma, CTRL_GAMMA),
    ):
        if flag is True:
            out |= bit
        elif flag is False:
            out &= ~bit
    if antialiasing is False:
        point_sampled = AA_REPLICATE if out & CTRL_TYPE_MASK == PIXEL_32_BIT else AA_RESAMPLE
        out = (out & ~CTRL_AA_MASK) | (point_sampled << AA_SHIFT)
    elif antialiasing is True:
        out = (out & ~CTRL_AA_MASK) | (AA_NEEDED << AA_SHIFT)
    return out & 0xFFFFFFFF


@dataclass(frozen=True)
class ModeEntry:
    ctrl_offset: int
    ctrl: int
    width: int
    vsync: int
    standard: str

    @property
    def decoded(self) -> ViCtrl:
        return decode_ctrl(self.ctrl)


def _plausible_ctrl(value: int) -> bool:
    if value >> 17:
        return False
    if value & CTRL_TYPE_MASK not in (2, 3):
        return False
    return not value & (CTRL_VBUS_CLOCK | CTRL_TEST_MODE | CTRL_KILL_WE)


VSYNC_OFFSET_FROM_CTRL = 12
OSVIMODE_SIZE = 0x50
OSVIMODE_TYPE_LIMIT = 56
OSVIMODE_TYPE_SHIFT = 24
OSVIMODE_PADDING_MASK = 0x00FFFFFF
OSVIMODE_NONZERO_WORDS = (3, 5, 6, 7, 8, 11)


def _has_osvimode_shape(rom: bytes, ctrl_offset: int) -> bool:
    start = ctrl_offset - 4
    if start < 0 or start + OSVIMODE_SIZE > len(rom):
        return False
    words = struct.unpack_from(">20I", rom, start)
    kind = words[0]
    if kind >> OSVIMODE_TYPE_SHIFT >= OSVIMODE_TYPE_LIMIT or kind & OSVIMODE_PADDING_MASK:
        return False
    return all(words[index] for index in OSVIMODE_NONZERO_WORDS)


def find_mode_tables(rom: bytes) -> tuple[ModeEntry, ...]:
    """Locate OSViMode entries by their ctrl, width and vSync fields together.

    A ctrl value alone is far too common to key on. Requiring a plausible width
    and a real video standard immediately after it is what makes the match safe.

    The search is anchored on vSync rather than on ctrl. vSync takes one of four
    values, progressive or interlaced for each standard, so scanning for it in C
    through bytes.find and then checking backwards is far cheaper than decoding
    every word in a 64 MiB ROM. The acceptance test is identical either way.
    """
    found: list[ModeEntry] = []
    for vsync, standard in VSYNC_BY_STANDARD.items():
        needle = struct.pack(">I", vsync)
        at = rom.find(needle)
        while at >= 0:
            ctrl_offset = at - VSYNC_OFFSET_FROM_CTRL
            if ctrl_offset % 4 == 0 and _has_osvimode_shape(rom, ctrl_offset):
                ctrl = struct.unpack_from(">I", rom, ctrl_offset)[0]
                if _plausible_ctrl(ctrl):
                    width = struct.unpack_from(">I", rom, ctrl_offset + 4)[0]
                    if width in PLAUSIBLE_WIDTHS:
                        found.append(
                            ModeEntry(
                                ctrl_offset=ctrl_offset,
                                ctrl=ctrl,
                                width=width,
                                vsync=vsync,
                                standard=standard,
                            )
                        )
            at = rom.find(needle, at + 1)
    found.sort(key=lambda m: m.ctrl_offset)
    return tuple(found)


@dataclass(frozen=True)
class SpecialFeaturesSite:
    offset: int
    masks: dict[str, int]


def _addiu_offsets(rom: bytes, imm: int) -> list[int]:
    needle = imm.to_bytes(2, "big")
    offsets: list[int] = []
    at = rom.find(needle, IMMEDIATE_OFFSET)
    while at >= 0:
        word = at - IMMEDIATE_OFFSET
        if word % 4 == 0 and rom[word] >> 2 == OP_ADDIU:
            offsets.append(word)
        at = rom.find(needle, at + 1)
    return offsets


def _next_within(offsets: list[int], after: int, before: int) -> int | None:
    index = bisect.bisect_right(offsets, after)
    if index < len(offsets) and offsets[index] < before:
        return offsets[index]
    return None


def find_special_features(rom: bytes) -> tuple[SpecialFeaturesSite, ...]:
    """Locate osViSetSpecialFeatures by the four AND masks it applies in order.

    The routine clears GAMMA, GAMMA_DITHER, DIVOT and AA_MODE using immediate
    constants 0xFFF7, 0xFFFB, 0xFFEF and 0xFCFF. Those four appearing in order
    and close together is a strong signature for the library routine, and it is
    how the established tooling finds it too.
    """
    found = {imm: _addiu_offsets(rom, imm) for _, imm in MASK_CONSTANTS}
    first_name, first_imm = MASK_CONSTANTS[0]
    sites: list[SpecialFeaturesSite] = []
    for offset in found[first_imm]:
        masks = {first_name: offset}
        cursor = offset
        for name, imm in MASK_CONSTANTS[1:]:
            hit = _next_within(found[imm], cursor, min(len(rom) - 3, cursor + MASK_SEARCH_WINDOW))
            if hit is None:
                break
            masks[name] = hit
            cursor = hit
        if len(masks) == len(MASK_CONSTANTS):
            sites.append(SpecialFeaturesSite(offset=offset, masks=masks))
    return tuple(sites)


OP_BEQ = 0x04
OP_BEQL = 0x14
OP_ANDI = 0x0C
OP_LUI = 0x0F
LUI_DITHER_FILTER_UPPER = 0x0001
DITHER_SEARCH_BACK = 0x100
DITHER_SEARCH_FORWARD = 0x80


OP_ORI = 0x0D


@dataclass(frozen=True, slots=True)
class SwitchSpec:
    flag: int
    opcode: int
    immediate: int


SWITCHES = {
    "dedither": SwitchSpec(flag=0x0040, opcode=OP_LUI, immediate=LUI_DITHER_FILTER_UPPER),
    "divot": SwitchSpec(flag=0x0010, opcode=OP_ORI, immediate=CTRL_DIVOT),
    "gamma dither": SwitchSpec(flag=0x0004, opcode=OP_ORI, immediate=CTRL_GAMMA_DITHER),
    "gamma": SwitchSpec(flag=0x0001, opcode=OP_ORI, immediate=CTRL_GAMMA),
}


@dataclass(frozen=True)
class SwitchRequest:
    switch: str
    setter: int
    branch: int
    guard: int
    target: int
    flag: int


def _sets_bit(word: int, spec: SwitchSpec) -> bool:
    if word >> 26 != spec.opcode or word & 0xFFFF != spec.immediate:
        return False
    return spec.opcode != OP_LUI or (word >> 21) & 31 == 0


def _guard_for(rom: bytes, setter: int, floor: int) -> tuple[int, int, int, int] | None:
    """The branch guarding `setter`, and the andi feeding it.

    Searched backwards from the setter, taking the first branch-on-zero whose
    target clears the setter. Anchoring on the branch rather than on the nearest
    `andi 0x0040` matters: a backward scan for the immediate alone can cross into
    an unrelated block and return a guard that has nothing to do with this code.

    The andi is then sought strictly before the branch. A MIPS delay slot runs
    after the branch condition has been read, so an andi sitting in this branch's
    own delay slot cannot be what the branch tested.
    """
    for branch in range(setter - 4, floor, -4):
        word = struct.unpack_from(">I", rom, branch)[0]
        if word >> 26 not in (OP_BEQ, OP_BEQL) or (word >> 16) & 31 != 0:
            continue
        immediate = word & 0xFFFF
        signed = immediate - 0x10000 if immediate & 0x8000 else immediate
        target = branch + 4 + signed * 4
        if target <= setter:
            continue
        register = (word >> 21) & 31
        for guard in range(branch - 4, floor, -4):
            candidate = struct.unpack_from(">I", rom, guard)[0]
            if candidate >> 26 == OP_ANDI and (candidate >> 16) & 31 == register:
                return branch, guard, target, candidate & 0xFFFF
        return None
    return None


def find_switch_requests(rom: bytes, switch: str) -> tuple[SwitchRequest, ...]:
    spec = SWITCHES[switch]
    found: list[SwitchRequest] = []
    for site in find_special_features(rom):
        floor = max(0, site.offset - DITHER_SEARCH_BACK)
        ceiling = min(len(rom) - 4, site.masks["antialias"] + DITHER_SEARCH_FORWARD)
        for setter in range(floor, ceiling, 4):
            if not _sets_bit(struct.unpack_from(">I", rom, setter)[0], spec):
                continue
            resolved = _guard_for(rom, setter, floor)
            if resolved is None or resolved[3] != spec.flag:
                continue
            branch, guard, target, flag = resolved
            found.append(SwitchRequest(switch, setter, branch, guard, target, flag))
    return tuple(found)


def find_dither_requests(rom: bytes) -> tuple[SwitchRequest, ...]:
    return find_switch_requests(rom, "dedither")


def clear_dither_requests(rom: bytes) -> tuple[bytes, tuple[tuple[int, int, int], ...]]:
    """Make every request to switch the filter on do nothing.

    The guard's immediate becomes zero, so the flag register is always zero and
    the branch past the block is always taken. This skips the bit that turns the
    filter on and the AA_MODE override that accompanies it, which is why it is
    preferred over neutering the `lui`: that would suppress the filter and leave
    the override forcing anti-aliasing on behind the caller's back.
    """
    out = bytearray(rom)
    changes: list[tuple[int, int, int]] = []
    for request in find_dither_requests(rom):
        before = struct.unpack_from(">I", out, request.guard)[0]
        after = before & 0xFFFF0000
        struct.pack_into(">I", out, request.guard, after)
        changes.append((request.guard, before, after))
    return bytes(out), tuple(changes)


@dataclass(frozen=True)
class Report:
    mode_count: int
    ctrl_values: tuple[int, ...]
    antialiasing_on: int
    dither_filter_on: int
    divot_on: int
    gamma_dither_on: int
    standards: tuple[str, ...]
    special_features_sites: int

    @property
    def patchable(self) -> bool:
        return self.mode_count > 0

    @property
    def blurs(self) -> bool:
        return self.dither_filter_on > 0 or self.divot_on > 0


def _as_z64(rom: bytes) -> tuple[bytes, ByteOrder]:
    order = detect(rom) or ByteOrder.Z64
    return swap(rom, order), order


def audit(rom: bytes) -> Report:
    big, _ = _as_z64(rom)
    return _audit_z64(big)


def _audit_z64(rom: bytes) -> Report:
    modes = find_mode_tables(rom)
    decoded = [m.decoded for m in modes]
    seen: list[int] = []
    for m in modes:
        if m.ctrl not in seen:
            seen.append(m.ctrl)
    standards: list[str] = []
    for m in modes:
        if m.standard not in standards:
            standards.append(m.standard)
    return Report(
        mode_count=len(modes),
        ctrl_values=tuple(seen),
        antialiasing_on=sum(1 for d in decoded if d.antialiasing),
        dither_filter_on=sum(1 for d in decoded if d.dither_filter),
        divot_on=sum(1 for d in decoded if d.divot),
        gamma_dither_on=sum(1 for d in decoded if d.gamma_dither),
        standards=tuple(standards),
        special_features_sites=len(find_special_features(rom)),
    )


def patch(
    rom: bytes,
    *,
    dither_filter: bool | None = None,
    divot: bool | None = None,
    gamma_dither: bool | None = None,
    gamma: bool | None = None,
    antialiasing: bool | None = None,
) -> tuple[bytes, int]:
    """Rewrite the ctrl field of every mode entry, touching nothing else."""
    out = bytearray(rom)
    changed = 0
    for entry in find_mode_tables(rom):
        new = apply_changes(
            entry.ctrl,
            dither_filter=dither_filter,
            divot=divot,
            gamma_dither=gamma_dither,
            gamma=gamma,
            antialiasing=antialiasing,
        )
        if new != entry.ctrl:
            struct.pack_into(">I", out, entry.ctrl_offset, new)
            changed += 1
    return bytes(out), changed


CHECKSUM_START = 0x1000
CHECKSUM_END = CHECKSUM_START + 0x100000
CRC1_OFFSET = 0x10
CRC2_OFFSET = 0x14

UNKNOWN_BOOT_CHIP = "the boot code names no known chip, so the seal is unknown"


def reseal(rom: bytes, cic: str | None = None) -> bytes:
    """Recompute and write the header checksum.

    Almost every video mode table sits inside the region the checksum covers, so
    editing one invalidates it. A retail cartridge with a bad checksum is
    rejected by the lockout chip, which means an unsealed patch produces a ROM
    that will not boot on real hardware even though it looks fine in an emulator
    that skips the check. Resealing is therefore not optional.
    """
    from . import checksum

    if len(rom) < CHECKSUM_END:
        raise ValueError(
            f"data is too short to checksum, need {CHECKSUM_END} bytes, got {len(rom)}"
        )
    chosen = cic or checksum.boot_chip(rom)
    if chosen is None:
        raise ValueError(UNKNOWN_BOOT_CHIP)
    pair = checksum.compute(rom, chosen)
    if pair is None:
        raise ValueError(f"the checksum could not be computed over {len(rom)} bytes")
    out = bytearray(rom)
    struct.pack_into(">I", out, CRC1_OFFSET, pair[0])
    struct.pack_into(">I", out, CRC2_OFFSET, pair[1])
    return bytes(out)


@dataclass(frozen=True)
class PatchResult:
    applied: bool
    reason: str
    data: bytes = b""
    modes_changed: int = 0
    changes: tuple[tuple[int, int, int], ...] = ()
    cic: str | None = None


NO_CHANGE_NEEDED = "every mode already has the requested settings"
NO_TABLE = "no video mode table could be proven present"
NO_MICROCODE = "no supported microcode was found, so the RDP dither cannot be changed"
RDP_COLOR_DITHER = 0xC0
RDP_ALPHA_DITHER = 0x30
RDP_DITHER_LABELS = {
    RDP_COLOR_DITHER: "colour",
    RDP_ALPHA_DITHER: "alpha",
    RDP_COLOR_DITHER | RDP_ALPHA_DITHER: "colour and alpha",
}


def _describe(
    modes: int,
    entries: int,
    requests: tuple[SwitchRequest, ...],
    *,
    table_missing: bool,
    rdp: tuple[int, int],
) -> str:
    parts = [f"{modes} of {entries} mode entries changed"] if entries else []
    if table_missing:
        parts.append(f"{NO_TABLE}, so anti-aliasing and the table settings are unchanged")
    for switch in SWITCHES:
        count = sum(1 for request in requests if request.switch == switch)
        if count:
            parts.append(f"{count} {switch} request neutralised")
    bits, microcodes = rdp
    if bits:
        plural = "microcode" if microcodes == 1 else "microcodes"
        parts.append(f"{RDP_DITHER_LABELS[bits]} dither forced off in {microcodes} {plural}")
    return ", ".join(parts) or "nothing changed"


def _safe_patch_z64(
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
    """Edit the video configuration, then prove the result before returning it.

    Four of the five settings live in the mode table. The dedither filter does
    not: it is absent from the table in every retail ROM surveyed, because the
    game switches it on at runtime through osViSetSpecialFeatures. Clearing it
    therefore edits that routine rather than the table, which is why it is the
    one setting that can be changed in a ROM carrying no mode table at all.

    Only `False` is accepted for the filter. Switching it back on would mean
    restoring an instruction this code did not write, and a request to turn on a
    filter that a ROM has no way to reach is meaningless.

    Every guard below refuses rather than guesses:

      a mode-table change needs a table this code can prove is one
      a filter change needs a request it can prove belongs to the library routine
      the existing checksum must validate, otherwise the boot chip is unknown
      and the result could not be resealed correctly
      at least one byte must actually change, so a no-op is reported as such
      the recomputed checksum must validate before the data is handed back
    """
    from . import checksum

    requested = {
        "antialiasing": antialiasing,
        "divot": divot,
        "gamma_dither": gamma_dither,
        "gamma": gamma,
    }
    rdp_requested = (rdp_color_dither, rdp_alpha_dither)
    if (
        all(v is None for v in requested.values())
        and dither_filter is None
        and all(v is None for v in rdp_requested)
    ):
        return PatchResult(False, "nothing requested")
    if any(rdp_requested):
        return PatchResult(False, "the RDP dither can be switched off here, never back on")

    if dither_filter:
        return PatchResult(False, "the dedither filter can be switched off here, never back on")

    entries = find_mode_tables(rom)
    wants_table = any(v is not None for v in requested.values())

    switched_off = (
        ("dedither", dither_filter),
        ("divot", divot),
        ("gamma dither", gamma_dither),
        ("gamma", gamma),
    )
    requests = tuple(
        request
        for switch, setting in switched_off
        if setting is False
        for request in find_switch_requests(rom, switch)
    )
    rdp_bits = (RDP_COLOR_DITHER if rdp_color_dither is False else 0) | (
        RDP_ALPHA_DITHER if rdp_alpha_dither is False else 0
    )
    microcodes = rdp.find_dither_sites(rom) if rdp_bits else ()
    if rdp_bits and not microcodes:
        return PatchResult(False, NO_MICROCODE)
    if wants_table and not entries and not requests:
        return PatchResult(False, NO_TABLE)
    if dither_filter is False and not requests and not wants_table and not rdp_bits:
        return PatchResult(
            False, "this ROM has no way to switch the dedither filter on, so it is already off"
        )

    valid, cic = checksum.verify(rom)
    if not valid:
        return PatchResult(
            False,
            "the existing checksum does not validate, so the boot chip is unknown "
            "and the result could not be resealed correctly",
        )

    changes: list[tuple[int, int, int]] = []
    out = bytearray(rom)
    modes_changed = 0
    if wants_table:
        for entry in entries:
            new = apply_changes(
                entry.ctrl,
                divot=divot,
                gamma_dither=gamma_dither,
                gamma=gamma,
                antialiasing=antialiasing,
            )
            if new != entry.ctrl:
                struct.pack_into(">I", out, entry.ctrl_offset, new)
                changes.append((entry.ctrl_offset, entry.ctrl, new))
        modes_changed = len(changes)

    for request in requests:
        before = struct.unpack_from(">I", out, request.guard)[0]
        after = before & 0xFFFF0000
        struct.pack_into(">I", out, request.guard, after)
        changes.append((request.guard, before, after))

    if rdp_bits:
        dithered, rdp_changes = rdp.disable_dither(bytes(out), rdp_bits)
        out = bytearray(dithered)
        changes.extend(rdp_changes)

    if not changes:
        return PatchResult(False, NO_CHANGE_NEEDED)

    sealed = reseal(bytes(out), cic)

    revalidated, after_cic = checksum.verify(sealed)
    if not revalidated or after_cic != cic:
        return PatchResult(
            False, "the recomputed checksum failed to validate, refusing to return the data"
        )

    for offset, _, expected in changes:
        if struct.unpack_from(">I", sealed, offset)[0] != expected:
            return PatchResult(False, f"the edit at 0x{offset:06X} did not take, refusing")

    return PatchResult(
        applied=True,
        reason=_describe(
            modes_changed,
            len(entries),
            requests,
            table_missing=wants_table and not entries,
            rdp=(rdp_bits, len(microcodes)),
        ),
        data=sealed,
        modes_changed=modes_changed,
        changes=tuple(changes),
        cic=cic,
    )


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
    big, order = _as_z64(rom)
    result = _safe_patch_z64(
        big,
        antialiasing=antialiasing,
        divot=divot,
        gamma_dither=gamma_dither,
        gamma=gamma,
        dither_filter=dither_filter,
        rdp_color_dither=rdp_color_dither,
        rdp_alpha_dither=rdp_alpha_dither,
    )
    return replace(result, data=swap(result.data, order)) if result.applied else result
