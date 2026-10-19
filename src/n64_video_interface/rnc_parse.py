from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .rnc_codec import MIN_MATCH, Chunk, Sub, Tables, bits, cost, symbol, tables_for

WINDOW = 0x8000
LONGEST = 0xFFFF
CANDIDATES = 256
EXACT_LENGTHS = 34
ROUNDS = 2
BYTE_BITS = 8


@dataclass(frozen=True, slots=True)
class Copy:
    distance: int
    length: int
    data: bytes


Item = int | Copy


def _copy_items(sub: Sub, desired: bytes, out: bytearray) -> list[Item]:
    items: list[Item] = []
    run = 0
    for _ in range(sub.length):
        at = len(out)
        if out[at - sub.distance] == desired[at]:
            out.append(desired[at])
            run += 1
            continue
        items.extend([Copy(sub.distance, run, bytes(out[at - run : at]))] if run else [])
        items.append(desired[at])
        out.append(desired[at])
        run = 0
    items.extend([Copy(sub.distance, run, bytes(out[len(out) - run :]))] if run else [])
    return items


def _items(chunk: Chunk, desired: bytes, out: bytearray) -> list[Item]:
    items: list[Item] = []
    for sub in chunk.subs:
        start = len(out)
        out += desired[start : start + len(sub.literals)]
        items.extend(desired[start : start + len(sub.literals)])
        items.extend(_copy_items(sub, desired, out))
    return items


def regroup(items: Sequence[Item]) -> tuple[Sub, ...]:
    subs: list[Sub] = []
    literals = bytearray()
    for item in items:
        if isinstance(item, int):
            literals.append(item)
        elif item.length < MIN_MATCH:
            literals += item.data
        else:
            subs.append(Sub(bytes(literals), item.distance, item.length))
            literals = bytearray()
    return (*subs, Sub(bytes(literals)))


def _fits(tables: Tables, subs: Sequence[Sub]) -> bool:
    known = [{index for index, width in enumerate(table) if width} for table in tables]
    return all(
        symbol(len(sub.literals)) in known[0]
        and (not sub.length or symbol(sub.distance - 1) in known[1])
        and (not sub.length or symbol(sub.length - MIN_MATCH) in known[2])
        for sub in subs
    )


def _rebuilt(chunk: Chunk, subs: tuple[Sub, ...]) -> Chunk:
    if subs == chunk.subs:
        return chunk
    return Chunk(chunk.tables if _fits(chunk.tables, subs) else tables_for(subs), subs)


def split(chunks: Sequence[Chunk], desired: bytes) -> tuple[Chunk, ...]:
    out = bytearray()
    return tuple(_rebuilt(chunk, regroup(_items(chunk, desired, out))) for chunk in chunks)


def _index(data: bytes, start: int) -> dict[bytes, list[int]]:
    index: dict[bytes, list[int]] = {}
    for at in range(max(0, start - WINDOW), start):
        index.setdefault(data[at : at + MIN_MATCH], []).append(at)
    return index


def _candidates(
    data: bytes, at: int, end: int, index: dict[bytes, list[int]]
) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    limit = min(end - at, LONGEST)
    longest = 1
    for source in reversed(index.get(data[at : at + MIN_MATCH], [])[-CANDIDATES:]):
        if at - source > WINDOW or longest >= limit:
            break
        length = MIN_MATCH
        while length < limit and data[source + length] == data[at + length]:
            length += 1
        if length > longest:
            found.append((length, at - source))
            longest = length
    return found


def _lengths(longest: int) -> set[int]:
    exact = set(range(MIN_MATCH, min(longest, EXACT_LENGTHS) + 1))
    steps = {1 << power for power in range(6, longest.bit_length()) if 1 << power < longest}
    return exact | steps | {step + 1 for step in steps} | {longest}


@dataclass(slots=True)
class _Path:
    cost: list[float]
    back: list[tuple[int, int]]
    run: list[int]


def _literal(path: _Path, offset: int) -> None:
    total = path.cost[offset] + BYTE_BITS
    if total < path.cost[offset + 1]:
        path.cost[offset + 1] = total
        path.back[offset + 1] = (0, 1)
        path.run[offset + 1] = path.run[offset] + 1


def _copies(
    path: _Path, offset: int, options: Sequence[tuple[int, int]], price: Sequence[Sequence[int]]
) -> None:
    raw, distance, length = price
    for longest, gap in options:
        base = path.cost[offset] + raw[symbol(path.run[offset])] + distance[symbol(gap - 1)]
        for size in _lengths(longest):
            total = base + length[symbol(size - MIN_MATCH)]
            if total < path.cost[offset + size]:
                path.cost[offset + size] = total
                path.back[offset + size] = (gap, size)
                path.run[offset + size] = 0


def _trace(path: _Path, data: bytes, start: int) -> list[Item]:
    items: list[Item] = []
    offset = len(path.back) - 1
    while offset > 0:
        gap, size = path.back[offset]
        at = start + offset
        items.append(Copy(gap, size, data[at - size : at]) if gap else data[at - 1])
        offset -= size
    return items[::-1]


def optimal(data: bytes, start: int, end: int, tables: Tables) -> tuple[Sub, ...]:
    price = tuple(cost(table) for table in tables)
    index = _index(data, start)
    size = end - start
    path = _Path([0.0] + [float("inf")] * size, [(0, 1)] * (size + 1), [0] * (size + 1))
    for offset in range(size):
        at = start + offset
        _literal(path, offset)
        _copies(path, offset, _candidates(data, at, end, index), price)
        index.setdefault(data[at : at + MIN_MATCH], []).append(at)
    return regroup(_trace(path, data, start))


def _optimized(chunk: Chunk, desired: bytes, start: int) -> Chunk:
    tables = chunk.tables
    candidate = chunk
    for _ in range(ROUNDS):
        subs = optimal(desired, start, start + chunk.size, tables)
        tables = tables_for(subs)
        candidate = min(candidate, Chunk(tables, subs), key=bits)
    return candidate


def improve(
    chunks: Sequence[Chunk], original: Sequence[Chunk], desired: bytes
) -> tuple[Chunk, ...]:
    starts = [sum(chunk.size for chunk in chunks[:index]) for index in range(len(chunks))]
    return tuple(
        chunk if chunk == before else _optimized(chunk, desired, start)
        for chunk, before, start in zip(chunks, original, starts, strict=True)
    )
