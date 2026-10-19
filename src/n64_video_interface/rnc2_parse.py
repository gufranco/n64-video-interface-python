from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .rnc2_codec import (
    BLOCK_BASE,
    BLOCK_UNIT,
    FAR_REACH,
    LONGEST,
    PAIR,
    Block,
    Copy,
    End,
    Literal,
    Op,
    cost,
    expressible,
)

CANDIDATES = 128
SHORT_LENGTHS = range(PAIR, 10)
BLOCK_SIZES = tuple(range((BLOCK_BASE) * BLOCK_UNIT, (BLOCK_BASE + 16) * BLOCK_UNIT, BLOCK_UNIT))
LITERAL_BITS = 9


def _run(distance: int, run: bytes) -> list[Op]:
    if expressible(distance, len(run)):
        return [Copy(distance, len(run))]
    return [Literal(byte) for byte in run]


def _split_copy(op: Copy, desired: bytes, out: bytearray) -> list[Op]:
    ops: list[Op] = []
    start = len(out)
    for _ in range(op.length):
        at = len(out)
        if out[at - op.distance] != desired[at]:
            ops.extend(_run(op.distance, bytes(out[start:at])) if at > start else [])
            ops.append(Literal(desired[at]))
            start = at + 1
        out.append(desired[at])
    ops.extend(_run(op.distance, bytes(out[start:])) if len(out) > start else [])
    return ops


def _split_op(op: Op, desired: bytes, out: bytearray) -> list[Op]:
    at = len(out)
    if isinstance(op, Literal):
        out.append(desired[at])
        return [Literal(desired[at])]
    if isinstance(op, Block):
        out += desired[at : at + len(op.data)]
        return [Block(desired[at : at + len(op.data)])]
    if isinstance(op, End):
        return [op]
    return _split_copy(op, desired, out)


def split(ops: Sequence[Op], desired: bytes) -> tuple[Op, ...]:
    out = bytearray()
    return tuple(new for op in ops for new in _split_op(op, desired, out))


def _index(data: bytes, start: int) -> dict[bytes, list[int]]:
    index: dict[bytes, list[int]] = {}
    for at in range(max(0, start - FAR_REACH), start):
        index.setdefault(data[at : at + PAIR], []).append(at)
    return index


def _candidates(
    data: bytes, at: int, end: int, index: dict[bytes, list[int]]
) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    limit = min(end - at, LONGEST)
    longest = 1
    for source in reversed(index.get(data[at : at + PAIR], [])[-CANDIDATES:]):
        if at - source > FAR_REACH or longest >= limit:
            break
        length = PAIR
        while length < limit and data[source + length] == data[at + length]:
            length += 1
        if length > longest:
            found.append((length, at - source))
            longest = length
    return found


@dataclass(slots=True)
class _Path:
    cost: list[float]
    back: list[Op]


def _offer(path: _Path, offset: int, op: Op, size: int) -> None:
    total = path.cost[offset] + cost(op)
    if total < path.cost[offset + size]:
        path.cost[offset + size] = total
        path.back[offset + size] = op


def _step(
    path: _Path, data: bytes, at: int, offset: int, options: Sequence[tuple[int, int]]
) -> None:
    _offer(path, offset, Literal(data[at]), 1)
    for size in BLOCK_SIZES:
        if offset + size < len(path.cost):
            _offer(path, offset, Block(data[at : at + size]), size)
    for longest, gap in options:
        for size in {*SHORT_LENGTHS, longest}:
            if size <= longest and expressible(gap, size):
                _offer(path, offset, Copy(gap, size), size)


def _size(op: Op) -> int:
    if isinstance(op, Literal):
        return 1
    if isinstance(op, Block):
        return len(op.data)
    return op.length if isinstance(op, Copy) else 0


def optimal(data: bytes, start: int, end: int) -> tuple[Op, ...]:
    index = _index(data, start)
    path = _Path([0.0] + [float("inf")] * (end - start), [End(0)] * (end - start + 1))
    for offset in range(end - start):
        at = start + offset
        _step(path, data, at, offset, _candidates(data, at, end, index))
        index.setdefault(data[at : at + PAIR], []).append(at)
    ops: list[Op] = []
    offset = end - start
    while offset:
        op = path.back[offset]
        ops.append(op)
        offset -= _size(op)
    return tuple(reversed(ops))


def _segments(ops: Sequence[Op]) -> list[tuple[Op, ...]]:
    segments: list[tuple[Op, ...]] = [()]
    for op in ops:
        segments[-1] = (*segments[-1], op)
        segments.extend([()] if isinstance(op, End) else [])
    return [segment for segment in segments if segment]


def _reparsed(segment: tuple[Op, ...], desired: bytes, start: int) -> tuple[Op, ...]:
    ends = tuple(op for op in segment if isinstance(op, End))
    size = sum(map(_size, segment))
    fresh = (*optimal(desired, start, start + size), *ends)
    return min(segment, fresh, key=lambda ops: sum(map(cost, ops)))


def improve(ops: Sequence[Op], original: Sequence[Op], desired: bytes) -> tuple[Op, ...]:
    out: list[Op] = []
    start = 0
    for segment, before in zip(_segments(ops), _segments(original), strict=True):
        out.extend(segment if segment == before else _reparsed(segment, desired, start))
        start += sum(map(_size, segment))
    return tuple(out)
