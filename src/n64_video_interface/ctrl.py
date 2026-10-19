from __future__ import annotations

from dataclasses import dataclass

CTRL_TYPE_MASK = 0x00003
CTRL_GAMMA_DITHER = 0x00004
CTRL_GAMMA = 0x00008
CTRL_DIVOT = 0x00010
CTRL_VBUS_CLOCK = 0x00020
CTRL_TEST_MODE = 0x00080
CTRL_AA_MASK = 0x00300
CTRL_KILL_WE = 0x00800
CTRL_DITHER_FILTER = 0x10000
CTRL_BITS = 17
AA_SHIFT = 8
AA_ALWAYS, AA_NEEDED, AA_RESAMPLE, AA_REPLICATE = 0, 1, 2, 3
PIXEL_16_BIT, PIXEL_32_BIT = 2, 3
WORD = 0xFFFFFFFF
FORBIDDEN = CTRL_VBUS_CLOCK | CTRL_TEST_MODE | CTRL_KILL_WE
SETTING_BITS = (
    ("dither_filter", CTRL_DITHER_FILTER),
    ("divot", CTRL_DIVOT),
    ("gamma_dither", CTRL_GAMMA_DITHER),
    ("gamma", CTRL_GAMMA),
)


@dataclass(frozen=True, slots=True)
class ViCtrl:
    raw: int

    @property
    def antialiasing(self) -> bool:
        return (self.raw & CTRL_AA_MASK) >> AA_SHIFT in (AA_ALWAYS, AA_NEEDED)

    @property
    def divot(self) -> bool:
        return bool(self.raw & CTRL_DIVOT)

    @property
    def gamma_dither(self) -> bool:
        return bool(self.raw & CTRL_GAMMA_DITHER)

    @property
    def gamma(self) -> bool:
        return bool(self.raw & CTRL_GAMMA)

    @property
    def dither_filter(self) -> bool:
        return bool(self.raw & CTRL_DITHER_FILTER)


def plausible(value: int) -> bool:
    return (
        value >> CTRL_BITS == 0
        and value & CTRL_TYPE_MASK in (PIXEL_16_BIT, PIXEL_32_BIT)
        and not value & FORBIDDEN
    )


def _with_bit(value: int, bit: int, setting: bool | None) -> int:
    if setting is None:
        return value
    return value | bit if setting else value & ~bit


def _with_aa(value: int, setting: bool | None) -> int:
    if setting is None:
        return value
    point_sampled = AA_REPLICATE if value & CTRL_TYPE_MASK == PIXEL_32_BIT else AA_RESAMPLE
    mode = AA_NEEDED if setting else point_sampled
    return (value & ~CTRL_AA_MASK) | (mode << AA_SHIFT)


def apply_changes(
    value: int,
    *,
    dither_filter: bool | None = None,
    divot: bool | None = None,
    gamma_dither: bool | None = None,
    gamma: bool | None = None,
    antialiasing: bool | None = None,
) -> int:
    settings = {
        "dither_filter": dither_filter,
        "divot": divot,
        "gamma_dither": gamma_dither,
        "gamma": gamma,
    }
    out = value
    for name, bit in SETTING_BITS:
        out = _with_bit(out, bit, settings[name])
    return _with_aa(out, antialiasing) & WORD
