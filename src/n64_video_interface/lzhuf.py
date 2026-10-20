from __future__ import annotations

import math
import struct
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from . import lzparse, rewindow
from .bitlz import Copy, Literals, Token, expand, span, split
from .edits import NO_TARGETS, Packed, RegionEdit, Settings, edit_region

RING = 0x1000
LONGEST = 60
SHORTEST = 3
THRESHOLD = SHORTEST - 1
SYMBOLS = 256 - THRESHOLD + LONGEST
NODES = SYMBOLS * 2 - 1
ROOT = NODES - 1
MAX_FREQ = 0x8000
SENTINEL = 0xFFFF
LITERALS = 256
WINDOW = RING - LONGEST
LOW_BITS = 6
LOW_MASK = (1 << LOW_BITS) - 1
BYTE_BITS = 8
BYTE = 0xFF
WORD_MASK = 0xFFFF
TOP_BIT = 0x8000
TOP_SHIFT = 15
CODE_SHIFT = 8
LOW_SHIFT = 10
SIZE = struct.Struct(">I")
HEADER = struct.Struct(">4s2H2I")
MAGIC = b"Ogre"
BOOT = 0x1000
BOOT_END = 0x101000
MAX_SIZE = 0x800000
SCALE = 16
SYMBOL_BITS = 9
CANDIDATES = 64
ATTEMPTS = 6
MARGIN = 16
NOUN = "LZHUF stream"
NO_ROOM = "a changed LZHUF stream no longer fits where it was"
P_LEN = bytes([3] + [4] * 3 + [5] * 8 + [6] * 12 + [7] * 24 + [8] * 16)
P_CODE = bytes(
    (
        *(0x00, 0x20, 0x30, 0x40, 0x50, 0x58, 0x60, 0x68, 0x70, 0x78, 0x80, 0x88),
        *range(0x90, 0xC0, 4),
        *range(0xC0, 0xF0, 2),
        *range(0xF0, 0x100),
    )
)
D_CODE = bytes(
    [0] * 32
    + [c for c in range(1, 4) for _ in range(16)]
    + [c for c in range(4, 12) for _ in range(8)]
    + [c for c in range(12, 24) for _ in range(4)]
    + [c for c in range(24, 48) for _ in range(2)]
    + list(range(48, 64))
)
D_LEN = bytes([3] * 32 + [4] * 48 + [5] * 64 + [6] * 48 + [7] * 48 + [8] * 16)


class FormatError(ValueError):
    pass


class Tree:
    def __init__(self) -> None:
        self.freq = [1] * SYMBOLS + [0] * (NODES - SYMBOLS) + [SENTINEL]
        self.son = [0] * NODES
        self.prnt = [0] * (NODES + SYMBOLS)
        for leaf in range(SYMBOLS):
            self.son[leaf], self.prnt[leaf + NODES] = leaf + NODES, leaf
        for node in range(SYMBOLS, NODES):
            low = (node - SYMBOLS) * 2
            self.freq[node] = self.freq[low] + self.freq[low + 1]
            self.son[node], self.prnt[low], self.prnt[low + 1] = low, node, node

    def _rebuild(self) -> None:
        pairs = zip(self.freq[:NODES], self.son, strict=True)
        leaves = [(f, s) for f, s in pairs if s >= NODES]
        self.freq[: len(leaves)] = [(f + 1) // 2 for f, _ in leaves]
        self.son[: len(leaves)] = [s for _, s in leaves]
        for node in range(SYMBOLS, NODES):
            low = (node - SYMBOLS) * 2
            total = self.freq[low] + self.freq[low + 1]
            spot = node - 1
            while total < self.freq[spot]:
                spot -= 1
            spot += 1
            self.freq[spot + 1 : node + 1] = self.freq[spot:node]
            self.son[spot + 1 : node + 1] = self.son[spot:node]
            self.freq[spot], self.son[spot] = total, low
        for node in range(NODES):
            self._link(self.son[node], node)

    def _link(self, child: int, parent: int) -> None:
        self.prnt[child] = parent
        if child < NODES:
            self.prnt[child + 1] = parent

    def _exchange(self, node: int, count: int) -> int:
        other = node + 1
        while count > self.freq[other + 1]:
            other += 1
        self.freq[node], self.freq[other] = self.freq[other], count
        first, second = self.son[node], self.son[other]
        self._link(first, other)
        self._link(second, node)
        self.son[node], self.son[other] = second, first
        return other

    def update(self, symbol: int) -> None:
        if self.freq[ROOT] == MAX_FREQ:
            self._rebuild()
        node, leaf = self.prnt[symbol + NODES], True
        while leaf or node:
            self.freq[node] += 1
            if self.freq[node] > self.freq[node + 1]:
                node = self._exchange(node, self.freq[node])
            node, leaf = self.prnt[node], False

    def code(self, symbol: int) -> tuple[int, int]:
        code, length, node = 0, 0, self.prnt[symbol + NODES]
        while node != ROOT or not length:
            code = (code >> 1) | (TOP_BIT if node & 1 else 0)
            length, node = length + 1, self.prnt[node]
        return code, length


class Decoder:
    def __init__(self, data: bytes, at: int) -> None:
        self.data, self.at, self.buffer, self.held = data, at, 0, 0
        self.tree = Tree()

    def _bits(self, count: int) -> int:
        value = 0
        for _ in range(count):
            while self.held <= BYTE_BITS:
                byte = self.data[self.at] if self.at < len(self.data) else 0
                self.buffer |= byte << (BYTE_BITS - self.held)
                self.at, self.held = self.at + 1, self.held + BYTE_BITS
            value = (value << 1) | (self.buffer >> TOP_SHIFT & 1)
            self.buffer, self.held = (self.buffer << 1) & WORD_MASK, self.held - 1
        return value

    def symbol(self) -> int:
        node = self.tree.son[ROOT]
        while node < NODES:
            node = self.tree.son[node + self._bits(1)]
        self.tree.update(node - NODES)
        return node - NODES

    def position(self) -> int:
        first = self._bits(BYTE_BITS)
        extra = D_LEN[first] - 2
        value = (first << extra) | self._bits(extra)
        return (D_CODE[first] << LOW_BITS) | (value & LOW_MASK)


class Encoder:
    def __init__(self) -> None:
        self.out, self.buffer, self.held = bytearray(), 0, 0
        self.tree = Tree()

    def _put(self, length: int, code: int) -> None:
        self.buffer |= code >> self.held
        self.held += length
        if self.held < BYTE_BITS:
            return
        self.out.append(self.buffer >> BYTE_BITS)
        self.held -= BYTE_BITS
        if self.held < BYTE_BITS:
            self.buffer = (self.buffer << BYTE_BITS) & WORD_MASK
            return
        self.out.append(self.buffer & BYTE)
        self.held -= BYTE_BITS
        self.buffer = (code << (length - self.held)) & WORD_MASK

    def emit(self, symbol: int, position: int | None) -> None:
        self._put(*reversed(self.tree.code(symbol)))
        self.tree.update(symbol)
        if position is not None:
            top = position >> LOW_BITS
            self._put(P_LEN[top], P_CODE[top] << CODE_SHIFT)
            self._put(LOW_BITS, (position & LOW_MASK) << LOW_SHIFT)

    def finish(self) -> bytes:
        return bytes(self.out) + (bytes((self.buffer >> BYTE_BITS,)) if self.held else b"")


def decode(data: bytes, at: int) -> tuple[tuple[Token, ...], int]:
    size = SIZE.unpack_from(data, at)[0]
    decoder = Decoder(data, at + SIZE.size)
    tokens: list[Token] = []
    produced = 0
    while produced < size:
        symbol = decoder.symbol()
        token: Token = (
            Literals(bytes((symbol,)))
            if symbol < LITERALS
            else Copy(decoder.position() + 1, symbol - LITERALS + SHORTEST)
        )
        if isinstance(token, Copy) and token.distance > produced:
            raise FormatError("a copy reaches before the start of the stream")
        tokens.append(token)
        produced += span(token)
    if produced != size:
        raise FormatError("the stream does not decode to its size")
    return lzparse.merged(tokens), decoder.at - decoder.held // BYTE_BITS - at


def _pieces(length: int) -> list[int]:
    pieces: list[int] = []
    left = length
    while left > LONGEST:
        pieces.append(min(LONGEST, left - SHORTEST))
        left -= pieces[-1]
    return [*pieces, left]


def _symbols(token: Token) -> list[tuple[int, int | None]]:
    if isinstance(token, Literals):
        return [(byte, None) for byte in token.data]
    return [(LITERALS - SHORTEST + p, token.distance - 1) for p in _pieces(token.length)]


def encode(tokens: Sequence[Token], size: int) -> bytes:
    encoder = Encoder()
    for token in tokens:
        for symbol, position in _symbols(token):
            encoder.emit(symbol, position)
    return SIZE.pack(size) + encoder.finish()


@dataclass(frozen=True, slots=True)
class Model:
    prices: tuple[int, ...]

    def literal(self) -> int:
        return round(sum(self.prices[:LITERALS]) / LITERALS)

    def copy(self, distance: int, length: int) -> int:
        far = (P_LEN[(distance - 1) >> LOW_BITS] + LOW_BITS) * SCALE
        return sum(self.prices[LITERALS - SHORTEST + p] + far for p in _pieces(length))

    def cost(self, token: Token) -> int:
        if isinstance(token, Copy):
            return self.copy(token.distance, token.length)
        return sum(self.prices[byte] for byte in token.data)

    def shape(self) -> lzparse.Shape:
        return lzparse.Shape(WINDOW, SHORTEST, LONGEST, CANDIDATES, self.literal(), self.copy)


FLAT = Model((SYMBOL_BITS * SCALE,) * SYMBOLS)


def model_of(tokens: Sequence[Token]) -> Model:
    counts = Counter(symbol for token in tokens for symbol, _ in _symbols(token))
    total = sum(counts.values())
    prices = (math.log2(total / counts.get(s, 0.5)) for s in range(SYMBOLS))
    return Model(tuple(round(price * SCALE) for price in prices))


def cost(token: Token) -> int:
    return FLAT.cost(token)


def optimal(data: bytes, start: int, end: int) -> tuple[Token, ...]:
    return lzparse.optimal(data, start, end, FLAT.shape())


@dataclass(frozen=True, slots=True)
class Stream:
    at: int
    size: int
    slot: int


def _stream_at(rom: bytes, header: int) -> Stream | None:
    _, _, flag, start, end = HEADER.unpack_from(rom, header)
    if not flag or not BOOT <= start < end - SIZE.size or end > len(rom):
        return None
    size = SIZE.unpack_from(rom, start)[0]
    if not 0 < size <= MAX_SIZE:
        return None
    try:
        _, used = decode(rom, start)
    except FormatError:
        return None
    return Stream(start, size, end - start) if used <= end - start else None


def find_streams(rom: bytes) -> tuple[Stream, ...]:
    found: list[Stream] = []
    at = rom.find(MAGIC, BOOT, BOOT_END)
    while 0 <= at <= len(rom) - HEADER.size:
        stream = _stream_at(rom, at)
        found.extend([stream] if stream else [])
        at = rom.find(MAGIC, at + 1, BOOT_END)
    return tuple(found)


def _repacked(
    tokens: Sequence[Token], edit: RegionEdit, before: bytes, stream: Stream
) -> bytes | None:
    model = model_of(tokens)
    shape = model.shape()
    budget = stream.slot * BYTE_BITS * SCALE
    for _ in range(ATTEMPTS):
        improved = rewindow.improve(
            split(tokens, edit.data),
            edit.data,
            before,
            budget,
            optimal=lambda data, start, end: lzparse.optimal(data, start, end, shape),
            cost=model.cost,
        )
        body = encode(improved, stream.size)
        if len(body) <= stream.slot:
            return body
        budget -= (len(body) - stream.slot + MARGIN) * BYTE_BITS * SCALE
    return None


def _edited(
    rom: bytes, stream: Stream, settings: Settings
) -> tuple[bytes, RegionEdit] | str | None:
    tokens, _ = decode(rom, stream.at)
    before = expand(tokens)
    edit = edit_region(before, settings)
    if not edit.changes:
        return None
    body = _repacked(tokens, edit, before, stream)
    if body is None:
        return NO_ROOM
    return body + bytes(stream.slot - len(body)), edit


def edit_streams(rom: bytes, settings: Settings) -> Packed | str:
    done: tuple[RegionEdit, ...] = ()
    spans: tuple[tuple[int, int], ...] = ()
    out, refusal = rom, NO_TARGETS
    for stream in find_streams(rom):
        result = _edited(rom, stream, settings)
        if isinstance(result, tuple):
            out = out[: stream.at] + result[0] + out[stream.at + stream.slot :]
            spans = (*spans, (stream.at, stream.at + stream.slot))
            done = (*done, result[1])
        refusal = result if isinstance(result, str) else refusal
    return Packed(out, done, len(done), NOUN, spans) if done else refusal
