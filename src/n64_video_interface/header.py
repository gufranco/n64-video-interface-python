from __future__ import annotations

import struct
from dataclasses import dataclass

from .byteorder import ByteOrder, detect, swap

HEADER_SIZE = 0x40
OFF_CRC = 0x10
OFF_TITLE = 0x20
LEN_TITLE = 20
OFF_MEDIA = 0x3B
OFF_CART = 0x3C
OFF_REGION = 0x3E
OFF_VERSION = 0x3F
PRINTABLE = range(32, 127)
UNPRINTABLE = "?"

ORDER_NAMES = {
    ByteOrder.Z64: "big endian",
    ByteOrder.V64: "byteswapped",
    ByteOrder.N64: "little endian",
}

REGIONS = {
    "7": "Beta",
    "A": "JPN/USA",
    "B": "BRA",
    "C": "CHN",
    "D": "GER",
    "E": "USA",
    "F": "FRA",
    "G": "USA",
    "H": "NLD",
    "I": "ITA",
    "J": "JPN",
    "K": "KOR",
    "L": "GTW",
    "N": "CAN",
    "P": "EUR",
    "S": "SPA",
    "U": "AUS",
    "W": "SWE",
    "X": "EUR",
    "Y": "EUR",
}


@dataclass(frozen=True, slots=True)
class RomHeader:
    byte_order: str
    true_extension: str
    internal_name: str
    media: str
    cart_id: str
    region_code: str
    region: str
    version: int
    crc1: str
    crc2: str

    @property
    def game_code(self) -> str:
        return f"{self.media}{self.cart_id}{self.region_code}"


def _character(value: int) -> str:
    return chr(value) if value in PRINTABLE else UNPRINTABLE


def parse(data: bytes) -> RomHeader | None:
    order = detect(data[:HEADER_SIZE]) if len(data) >= HEADER_SIZE else None
    if order is None:
        return None
    head = swap(data[:HEADER_SIZE], order)
    region_code = _character(head[OFF_REGION])
    crc1, crc2 = struct.unpack_from(">2I", head, OFF_CRC)
    title = head[OFF_TITLE : OFF_TITLE + LEN_TITLE].decode("ascii", "replace")
    return RomHeader(
        byte_order=ORDER_NAMES[order],
        true_extension=order.value,
        internal_name=title.rstrip(" \x00").strip(),
        media=_character(head[OFF_MEDIA]),
        cart_id=head[OFF_CART : OFF_CART + 2].decode("ascii", "replace"),
        region_code=region_code,
        region=REGIONS.get(region_code, "unknown"),
        version=head[OFF_VERSION],
        crc1=f"{crc1:08X}",
        crc2=f"{crc2:08X}",
    )
