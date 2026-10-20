from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from .bitlz import Copy, Literals, Token

UNREACHED = 1 << 62


@dataclass(frozen=True, slots=True)
class Shape:
    window: int
    min_copy: int
    max_copy: int
    candidates: int
    literal_bits: int
    copy_bits: Callable[[int], int]


def merged(tokens: Sequence[Token]) -> tuple[Token, ...]:
    out: list[Token] = []
    for token in tokens:
        if isinstance(token, Literals) and out and isinstance(out[-1], Literals):
            out[-1] = Literals(out[-1].data + token.data)
        else:
            out.append(token)
    return tuple(out)


def _matches(
    data: bytes, at: int, limit: int, index: Mapping[bytes, Sequence[int]], shape: Shape
) -> Iterator[Copy]:
    longest = shape.min_copy - 1
    for source in reversed(index.get(data[at : at + shape.min_copy], [])[-shape.candidates :]):
        if at - source > shape.window or longest >= limit:
            return
        length = shape.min_copy
        while length < limit and data[source + length] == data[at + length]:
            length += 1
        yield from (Copy(at - source, size) for size in range(longest + 1, length + 1))
        longest = max(longest, length)


def _trace(back: Sequence[Copy | None], data: bytes, start: int) -> tuple[Token, ...]:
    tokens: list[Token] = []
    offset = len(back) - 1
    while offset:
        copy = back[offset]
        tokens.append(copy if copy else Literals(data[start + offset - 1 : start + offset]))
        offset -= copy.length if copy else 1
    return merged(tokens[::-1])


def optimal(data: bytes, start: int, end: int, shape: Shape) -> tuple[Token, ...]:
    index: dict[bytes, list[int]] = {}
    for at in range(max(0, start - shape.window), start):
        index.setdefault(data[at : at + shape.min_copy], []).append(at)
    size = end - start
    best = [0] + [UNREACHED] * size
    back: list[Copy | None] = [None] * (size + 1)
    for offset in range(size):
        at = start + offset
        if best[offset] + shape.literal_bits < best[offset + 1]:
            best[offset + 1], back[offset + 1] = best[offset] + shape.literal_bits, None
        limit = min(size - offset, shape.max_copy)
        for copy in _matches(data, at, limit, index, shape):
            total = best[offset] + shape.copy_bits(copy.length)
            if total < best[offset + copy.length]:
                best[offset + copy.length], back[offset + copy.length] = total, copy
        index.setdefault(data[at : at + shape.min_copy], []).append(at)
    return _trace(back, data, start)
