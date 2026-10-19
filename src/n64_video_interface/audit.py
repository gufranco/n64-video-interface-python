from __future__ import annotations

from dataclasses import dataclass

from .byteorder import to_big_endian
from .features import find_special_features, find_switch_requests
from .modes import find_mode_tables


@dataclass(frozen=True, slots=True)
class Report:
    mode_count: int
    ctrl_values: tuple[int, ...]
    antialiasing_on: int
    dither_filter_on: int
    divot_on: int
    gamma_dither_on: int
    standards: tuple[str, ...]
    special_features_sites: int
    dedither_requests: int


def audit(rom: bytes) -> Report:
    big, _ = to_big_endian(rom)
    entries = find_mode_tables(big)
    decoded = [entry.decoded for entry in entries]
    return Report(
        mode_count=len(entries),
        ctrl_values=tuple(dict.fromkeys(entry.ctrl for entry in entries)),
        antialiasing_on=sum(d.antialiasing for d in decoded),
        dither_filter_on=sum(d.dither_filter for d in decoded),
        divot_on=sum(d.divot for d in decoded),
        gamma_dither_on=sum(d.gamma_dither for d in decoded),
        standards=tuple(dict.fromkeys(entry.standard for entry in entries)),
        special_features_sites=len(find_special_features(big)),
        dedither_requests=len(find_switch_requests(big, "dedither")),
    )
