"""N64 cartridge header parsing.

The header is 64 bytes and carries everything needed to identify a dump exactly:
the internal title, the cartridge code, the region, the revision and both
checksums. Those 64 bytes are also the key most patch formats bind to, which is
why `identity_key` exists as a named concept rather than a slice at the call
site.

The byte order is read from the magic word and never inferred from the file
extension, because a byteswapped dump carrying a big endian extension is a real
and silent failure mode on the hardware.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .byteorder import ByteOrder, detect, swap

HEADER_SIZE = 0x40

OFF_CRC1 = 0x10
OFF_CRC2 = 0x14
OFF_TITLE = 0x20
LEN_TITLE = 20
OFF_MEDIA = 0x3B
OFF_CART = 0x3C
OFF_REGION = 0x3E
OFF_VERSION = 0x3F

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


@dataclass(frozen=True)
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


def _normalise(head: bytes) -> tuple[bytes, str, str] | None:
    order = detect(head)
    if order is None:
        return None
    return swap(head, order), ORDER_NAMES[order], order.value


def identity_key(data: bytes) -> bytes | None:
    """The 64 bytes a patch binds to, normalised to big endian."""
    if len(data) < HEADER_SIZE:
        return None
    result = _normalise(data[:HEADER_SIZE])
    return None if result is None else result[0]


def parse(data: bytes) -> RomHeader | None:
    if len(data) < HEADER_SIZE:
        return None
    result = _normalise(data[:HEADER_SIZE])
    if result is None:
        return None
    head, order, ext = result

    media = chr(head[OFF_MEDIA]) if 32 <= head[OFF_MEDIA] < 127 else "?"
    region_code = chr(head[OFF_REGION]) if 32 <= head[OFF_REGION] < 127 else "?"
    title = head[OFF_TITLE : OFF_TITLE + LEN_TITLE].decode("ascii", "replace")

    return RomHeader(
        byte_order=order,
        true_extension=ext,
        internal_name=title.rstrip(" \x00").strip(),
        media=media,
        cart_id=head[OFF_CART : OFF_CART + 2].decode("ascii", "replace"),
        region_code=region_code,
        region=REGIONS.get(region_code, "unknown"),
        version=head[OFF_VERSION],
        crc1=f"{struct.unpack_from('>I', head, OFF_CRC1)[0]:08X}",
        crc2=f"{struct.unpack_from('>I', head, OFF_CRC2)[0]:08X}",
    )
