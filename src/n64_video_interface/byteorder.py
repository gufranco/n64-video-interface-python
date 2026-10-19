from __future__ import annotations

from enum import StrEnum

CONFIG_MARK = 0x80
CONFIG_LOW = 0x40
HEAD_BYTES = 4


class ByteOrder(StrEnum):
    Z64 = "z64"
    V64 = "v64"
    N64 = "n64"


_MARK_POSITIONS = {
    (0, 3): ByteOrder.Z64,
    (1, 2): ByteOrder.V64,
    (3, 0): ByteOrder.N64,
}


def detect(data: bytes) -> ByteOrder | None:
    if len(data) < HEAD_BYTES:
        return None
    for (mark, low), order in _MARK_POSITIONS.items():
        if data[mark] == CONFIG_MARK and data[low] == CONFIG_LOW:
            return order
    return None


def swap(data: bytes, order: ByteOrder) -> bytes:
    if order is ByteOrder.V64:
        swapped = bytearray(data)
        swapped[0::2], swapped[1::2] = data[1::2], data[0::2]
        return bytes(swapped)
    if order is ByteOrder.N64:
        swapped = bytearray(data)
        for lane in range(HEAD_BYTES):
            swapped[lane::HEAD_BYTES] = data[HEAD_BYTES - 1 - lane :: HEAD_BYTES]
        return bytes(swapped)
    return data
