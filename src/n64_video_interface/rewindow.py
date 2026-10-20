from __future__ import annotations

from collections.abc import Callable, Sequence

from .bitlz import Token, span

IMPROVE_WINDOW = 0x4000
Parser = Callable[[bytes, int, int], tuple[Token, ...]]
Pricer = Callable[[Token], int]


def _windows(tokens: Sequence[Token]) -> list[tuple[int, int, int, int]]:
    windows: list[tuple[int, int, int, int]] = []
    index, position = 0, 0
    while index < len(tokens):
        end, stop = index, position
        while end < len(tokens) and stop - position < IMPROVE_WINDOW:
            stop += span(tokens[end])
            end += 1
        windows.append((index, end, position, stop))
        index, position = end, stop
    return windows


def improve(
    tokens: Sequence[Token],
    desired: bytes,
    before: bytes,
    target: int,
    *,
    optimal: Parser,
    cost: Pricer,
) -> tuple[Token, ...]:
    windows = _windows(tokens)
    parts = [tuple(tokens[first:end]) for first, end, _, _ in windows]
    total = sum(map(cost, tokens))
    changed = [i for i, (_, _, a, b) in enumerate(windows) if desired[a:b] != before[a:b]]
    for i in [*changed, *(i for i in range(len(windows)) if i not in changed)]:
        if i not in changed and total <= target:
            break
        fresh = optimal(desired, windows[i][2], windows[i][3])
        saving = sum(map(cost, parts[i])) - sum(map(cost, fresh))
        parts[i], total = (fresh, total - saving) if saving > 0 else (parts[i], total)
    return tuple(token for part in parts for token in part)
