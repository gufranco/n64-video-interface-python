from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

HEADER = 18
BYTE_BITS = 8
LOOKAHEAD = 4
PAIR = 2
TRIPLE = 3
SHORT_REACH = 0x100
FAR_REACH = 0x1000
LONG_BASE = 8
LONGEST = LONG_BASE + 0xFF
BLOCK_UNIT = 4
BLOCK_BASE = 3
BLOCK_COUNT_BITS = 4
BLOCK_CODE = 9
MID_LENGTHS = range(4, 9)
DISTANCE_BITS = (1, 3, 4, 4, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6, 6, 6)


@dataclass(frozen=True, slots=True)
class Literal:
    value: int


@dataclass(frozen=True, slots=True)
class Block:
    data: bytes


@dataclass(frozen=True, slots=True)
class Copy:
    distance: int
    length: int


@dataclass(frozen=True, slots=True)
class End:
    more: int


Op = Literal | Block | Copy | End


def expressible(distance: int, length: int) -> bool:
    if length == PAIR:
        return 0 < distance <= SHORT_REACH
    return TRIPLE <= length <= LONGEST and 0 < distance <= FAR_REACH


def distance_bits(high: int) -> int:
    return DISTANCE_BITS[high]


def cost(op: Op) -> int:
    if isinstance(op, Literal):
        return 1 + BYTE_BITS
    if isinstance(op, Block):
        return BLOCK_CODE + BYTE_BITS * len(op.data)
    if isinstance(op, End):
        return 4 + BYTE_BITS + 1
    if op.length == PAIR:
        return 3 + BYTE_BITS
    head = 4 if op.length == TRIPLE else 4 + BYTE_BITS
    head = (4 if op.length < 6 else 5) if op.length in MID_LENGTHS else head
    return head + distance_bits((op.distance - 1) >> BYTE_BITS) + BYTE_BITS


class Reader:
    def __init__(self, data: bytes, at: int) -> None:
        self.data = data + bytes(LOOKAHEAD)
        self.pos = at
        self.start = at
        self.byte = 0
        self.left = 0
        self.produced = 0
        self.margin = -len(data)

    def bit(self) -> int:
        if not self.left:
            self.byte, self.left = self.data[self.pos], BYTE_BITS
            self.pos += 1
        self.left -= 1
        return (self.byte >> self.left) & 1

    def bits(self, width: int) -> int:
        value = 0
        for _ in range(width):
            value = (value << 1) | self.bit()
        return value

    def raw(self) -> int:
        self.margin = max(self.margin, self.produced - (self.pos - self.start) - LOOKAHEAD)
        value = self.data[self.pos]
        self.pos += 1
        return value

    def high(self) -> int:
        if not self.bit():
            return 0
        value = self.bit()
        if self.bit():
            value = (value << 1) | self.bit() | 4
            return value if self.bit() else (value << 1) | self.bit()
        return value if value else 2 | self.bit()

    def copy(self, length: int) -> Copy:
        distance = (self.high() << BYTE_BITS) + self.raw() + 1
        self.margin = max(
            self.margin, self.produced + length - 1 - (self.pos - self.start) - LOOKAHEAD
        )
        self.produced += length
        return Copy(distance, length)

    def literal(self) -> Literal:
        value = Literal(self.raw())
        self.produced += 1
        return value

    def block(self) -> Block:
        count = (self.bits(BLOCK_COUNT_BITS) + BLOCK_BASE) * BLOCK_UNIT
        data = bytes(self.literal().value for _ in range(count))
        return Block(data)

    def middle(self) -> Op:
        length = 4 | self.bit()
        if self.bit():
            length = ((length - 1) << 1) | self.bit()
        return self.block() if length == BLOCK_CODE else self.copy(length)

    def long(self) -> Op:
        if not self.bit():
            return self.copy(TRIPLE)
        length = self.raw() + LONG_BASE
        return End(self.bit()) if length == LONG_BASE else self.copy(length)

    def op(self) -> Op:
        if not self.bit():
            return self.literal()
        if not self.bit():
            return self.middle()
        if not self.bit():
            return self.copy_pair()
        return self.long()

    def copy_pair(self) -> Copy:
        distance = self.raw() + 1
        self.margin = max(self.margin, self.produced + 1 - (self.pos - self.start) - LOOKAHEAD)
        self.produced += PAIR
        return Copy(distance, PAIR)

    def closing(self) -> tuple[End, ...]:
        if self.bits(4) != 0b1111 or self.raw():
            return ()
        return (End(self.bit()),)


def read_all(data: bytes, at: int, size: int) -> tuple[tuple[Op, ...], Reader]:
    reader = Reader(data, at + HEADER)
    reader.bits(2)
    ops: list[Op] = []
    while reader.produced < size:
        ops.append(reader.op())
    return (*ops, *reader.closing()), reader


def decode(data: bytes, at: int, size: int) -> tuple[Op, ...]:
    return read_all(data, at, size)[0]


class Writer:
    def __init__(self) -> None:
        self.out = bytearray()
        self.slot = 0
        self.used = BYTE_BITS

    def bit(self, value: int) -> None:
        if self.used == BYTE_BITS:
            self.slot, self.used = len(self.out), 0
            self.out.append(0)
        self.out[self.slot] |= (value & 1) << (BYTE_BITS - 1 - self.used)
        self.used += 1

    def bits(self, value: int, width: int) -> None:
        for index in range(width - 1, -1, -1):
            self.bit(value >> index)

    def high(self, value: int) -> None:
        if value < 4:
            self.bits((0, 0b110, 0b1000, 0b1001)[value], distance_bits(value))
            return
        top = value >> 1 if value >= LONG_BASE else value
        self.bits(0b1010 | ((top >> 1) & 1) << 2 | (top & 1), 4)
        self.bits(value & 1 if value >= LONG_BASE else 1, 2 if value >= LONG_BASE else 1)

    def copy(self, op: Copy) -> None:
        low = (op.distance - 1) & 0xFF
        if op.length == PAIR:
            self.bits(0b110, 3)
            self.out.append(low)
            return
        if op.length in MID_LENGTHS:
            self.bits(0b10, 2)
            pick = (op.length & 1, 0) if op.length < 6 else ((op.length >> 1) - 3, 1, op.length & 1)
            for value in pick:
                self.bit(value)
        elif op.length == TRIPLE:
            self.bits(0b1110, 4)
        else:
            self.bits(0b1111, 4)
            self.out.append(op.length - LONG_BASE)
        self.high((op.distance - 1) >> BYTE_BITS)
        self.out.append(low)

    def op(self, op: Op) -> None:
        if isinstance(op, Literal):
            self.bit(0)
            self.out.append(op.value)
        elif isinstance(op, Block):
            self.bits(0b10111, 5)
            self.bits(len(op.data) // BLOCK_UNIT - BLOCK_BASE, BLOCK_COUNT_BITS)
            self.out += op.data
        elif isinstance(op, End):
            self.bits(0b1111, 4)
            self.out.append(0)
            self.bit(op.more)
        else:
            self.copy(op)


def encode(ops: Sequence[Op]) -> bytes:
    writer = Writer()
    writer.bits(0, 2)
    for op in ops:
        writer.op(op)
    return bytes(writer.out)


def expand(ops: Sequence[Op], history: bytes = b"") -> bytes:
    out = bytearray(history)
    for op in ops:
        if isinstance(op, Literal):
            out.append(op.value)
        elif isinstance(op, Block):
            out += op.data
        elif isinstance(op, Copy):
            for _ in range(op.length):
                out.append(out[-op.distance])
    return bytes(out[len(history) :])
