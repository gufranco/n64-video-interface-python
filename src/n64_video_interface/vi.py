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

import struct
from dataclasses import dataclass

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

VSYNC_BY_STANDARD = {525: "NTSC", 625: "PAL"}
PLAUSIBLE_WIDTHS = (256, 320, 384, 512, 576, 640)

ADDIU_ZERO = 0x24000000
ADDIU_OPCODE_MASK = 0xFC000000
ADDIU_OPCODE = 0x24000000

MASK_CONSTANTS = (
    ("gamma", 0xFFF7),
    ("gamma_dither", 0xFFFB),
    ("divot", 0xFFEF),
    ("antialias", 0xFCFF),
)
MASK_SEARCH_WINDOW = 256

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
        out = (out & ~CTRL_AA_MASK) | (AA_RESAMPLE << AA_SHIFT)
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


def find_mode_tables(rom: bytes) -> tuple[ModeEntry, ...]:
    """Locate OSViMode entries by their ctrl, width and vSync fields together.

    A ctrl value alone is far too common to key on. Requiring a plausible width
    and a real video standard immediately after it is what makes the match safe.

    The search is anchored on vSync rather than on ctrl. vSync takes one of only
    two values in the whole address space, so scanning for it in C through
    bytes.find and then checking backwards is far cheaper than decoding every
    word in a 64 MiB ROM. The acceptance test is identical either way.
    """
    found: list[ModeEntry] = []
    for vsync, standard in VSYNC_BY_STANDARD.items():
        needle = struct.pack(">I", vsync)
        at = rom.find(needle)
        while at >= 0:
            ctrl_offset = at - VSYNC_OFFSET_FROM_CTRL
            if ctrl_offset >= 0 and ctrl_offset % 4 == 0 and ctrl_offset + 16 <= len(rom):
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


def find_special_features(rom: bytes) -> tuple[SpecialFeaturesSite, ...]:
    """Locate osViSetSpecialFeatures by the four AND masks it applies in order.

    The routine clears GAMMA, GAMMA_DITHER, DIVOT and AA_MODE using immediate
    constants 0xFFF7, 0xFFFB, 0xFFEF and 0xFCFF. Those four appearing in order
    and close together is a strong signature for the library routine, and it is
    how the established tooling finds it too.
    """
    first_name, first_imm = MASK_CONSTANTS[0]
    sites: list[SpecialFeaturesSite] = []
    for offset in range(0, max(0, len(rom) - 4), 4):
        word = struct.unpack_from(">I", rom, offset)[0]
        if word & ADDIU_OPCODE_MASK != ADDIU_OPCODE or word & 0xFFFF != first_imm:
            continue
        masks = {first_name: offset}
        cursor = offset
        for name, imm in MASK_CONSTANTS[1:]:
            hit = None
            for probe in range(cursor + 4, min(len(rom) - 4, cursor + MASK_SEARCH_WINDOW), 4):
                candidate = struct.unpack_from(">I", rom, probe)[0]
                if candidate & ADDIU_OPCODE_MASK == ADDIU_OPCODE and candidate & 0xFFFF == imm:
                    hit = probe
                    break
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
LUI_AT_ONE = 0x3C010001
DITHER_FILTER_ON_FLAG = 0x0040
DITHER_SEARCH_BACK = 0x100
DITHER_SEARCH_FORWARD = 0x80


@dataclass(frozen=True)
class DitherRequest:
    """Where a ROM asks the library to switch the dedither filter on.

    `setter` is the `lui at, 0x0001` that puts bit 16 in place for the `or` that
    follows. `guard` is the `andi` computing the OS_VI_DITHER_FILTER_ON flag, and
    `branch` is the `beq`/`beql` that skips the whole block when the flag is
    clear. Zeroing the guard's immediate makes the branch always taken, so the
    request becomes a no-op.
    """

    setter: int
    branch: int
    guard: int
    target: int
    flag: int


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


def find_dither_requests(rom: bytes) -> tuple[DitherRequest, ...]:
    """Every runtime request to switch the dedither filter on.

    The filter never appears in a mode table, so this is the only place a ROM can
    turn it on. A block whose guard tests a flag other than OS_VI_DITHER_FILTER_ON
    belongs to some other routine that happens to carry the same mask constants,
    and is left alone.
    """
    found: list[DitherRequest] = []
    for site in find_special_features(rom):
        floor = max(0, site.offset - DITHER_SEARCH_BACK)
        ceiling = min(len(rom) - 4, site.masks["antialias"] + DITHER_SEARCH_FORWARD)
        for setter in range(floor, ceiling, 4):
            if struct.unpack_from(">I", rom, setter)[0] != LUI_AT_ONE:
                continue
            resolved = _guard_for(rom, setter, floor)
            if resolved is None:
                continue
            branch, guard, target, flag = resolved
            if flag != DITHER_FILTER_ON_FLAG:
                continue
            found.append(
                DitherRequest(setter=setter, branch=branch, guard=guard, target=target, flag=flag)
            )
    return tuple(found)


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
        if after != before:
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


def audit(rom: bytes) -> Report:
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

DEFAULT_RESEAL_CIC = "6102"


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
    chosen = cic or checksum.verify(rom)[1] or DEFAULT_RESEAL_CIC
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


def _describe(modes: int, entries: int, requests: int) -> str:
    parts = []
    if entries:
        parts.append(f"{modes} of {entries} mode entries changed")
    if requests:
        parts.append(f"{requests} dedither request neutralised")
    return ", ".join(parts) or "nothing changed"


def safe_patch(
    rom: bytes,
    *,
    antialiasing: bool | None = None,
    divot: bool | None = None,
    gamma_dither: bool | None = None,
    gamma: bool | None = None,
    dither_filter: bool | None = None,
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
    if all(v is None for v in requested.values()) and dither_filter is None:
        return PatchResult(False, "nothing requested")

    if dither_filter:
        return PatchResult(False, "the dedither filter can be switched off here, never back on")

    entries = find_mode_tables(rom)
    wants_table = any(v is not None for v in requested.values())
    if wants_table and not entries:
        return PatchResult(False, "no video mode table could be proven present")

    requests = find_dither_requests(rom) if dither_filter is False else ()
    if dither_filter is False and not requests and not wants_table:
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
        if after != before:
            struct.pack_into(">I", out, request.guard, after)
            changes.append((request.guard, before, after))

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
        reason=_describe(modes_changed, len(entries), len(requests)),
        data=sealed,
        modes_changed=modes_changed,
        changes=tuple(changes),
        cic=cic,
    )


IPS_MAX_OFFSET = 0xFFFFFF


def make_ips(
    changes: list[tuple[int, int, int]] | tuple[tuple[int, int, int], ...],
    checksum_words: bytes | None = None,
) -> bytes:
    """Express the same edits as an IPS patch, leaving the ROM file untouched.

    Useful when the ROM must stay byte-identical on disk and the change is
    applied by some other tool. A device that matches one external patch per ROM
    cannot take this alongside a patch the game already needs, because the two
    would compete for the same slot.

    When `checksum_words` is given, the two header checksum words are included as
    a record, since editing a mode table almost always invalidates them.
    """
    out = bytearray(b"PATCH")
    records = [(offset, struct.pack(">I", new)) for offset, _, new in changes]
    if checksum_words is not None:
        records.append((CRC1_OFFSET, bytes(checksum_words[CRC1_OFFSET : CRC1_OFFSET + 8])))
    for offset, payload in records:
        if offset > IPS_MAX_OFFSET:
            raise ValueError(
                f"offset 0x{offset:X} is beyond what IPS can address, 0x{IPS_MAX_OFFSET:X}"
            )
        out += offset.to_bytes(3, "big")
        out += len(payload).to_bytes(2, "big")
        out += payload
    return bytes(out + b"EOF")
