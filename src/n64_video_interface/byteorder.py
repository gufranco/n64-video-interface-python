from __future__ import annotations

from enum import StrEnum

CONFIG_MARK = 0x80
HEAD_BYTES = 4


class ByteOrder(StrEnum):
    Z64 = "z64"
    V64 = "v64"
    N64 = "n64"


_MARK_POSITIONS = (
    (0, ByteOrder.Z64),
    (1, ByteOrder.V64),
    (3, ByteOrder.N64),
)


def detect(data: bytes) -> ByteOrder | None:
    if len(data) < HEAD_BYTES:
        return None
    for mark, order in _MARK_POSITIONS:
        if data[mark] == CONFIG_MARK:
            return order
    return None


UNIT = {ByteOrder.Z64: 1, ByteOrder.V64: 2, ByteOrder.N64: HEAD_BYTES}


def swap(data: bytes, order: ByteOrder) -> bytes:
    whole = len(data) - len(data) % UNIT[order]
    body, tail = data[:whole], data[whole:]
    if order is ByteOrder.V64:
        swapped = bytearray(body)
        swapped[0::2], swapped[1::2] = body[1::2], body[0::2]
        return bytes(swapped) + tail
    if order is ByteOrder.N64:
        swapped = bytearray(body)
        for lane in range(HEAD_BYTES):
            swapped[lane::HEAD_BYTES] = body[HEAD_BYTES - 1 - lane :: HEAD_BYTES]
        return bytes(swapped) + tail
    return data
