from __future__ import annotations

HEADER = b"PATCH"
FOOTER = b"EOF"
MAX_OFFSET = 0xFFFFFF
MAX_RECORD = 0xFFFF
EOF_OFFSET = int.from_bytes(FOOTER, "big")
BLOCK = 0x1000


class IpsError(ValueError):
    pass


def _differing_blocks(original: bytes, patched: bytes) -> list[int]:
    return [
        start
        for start in range(0, len(original), BLOCK)
        if original[start : start + BLOCK] != patched[start : start + BLOCK]
    ]


def _runs(original: bytes, patched: bytes) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for block in _differing_blocks(original, patched):
        for at in range(block, min(block + BLOCK, len(original))):
            if original[at] == patched[at]:
                continue
            if runs and runs[-1][1] == at and at - runs[-1][0] < MAX_RECORD - 1:
                runs[-1] = (runs[-1][0], at + 1)
            else:
                runs.append((at, at + 1))
    return runs


def _record(patched: bytes, start: int, end: int) -> bytes:
    if end - 1 > MAX_OFFSET:
        raise IpsError(f"offset 0x{end - 1:X} is beyond what IPS can address, 0x{MAX_OFFSET:X}")
    first = start - 1 if start == EOF_OFFSET else start
    payload = patched[first:end]
    return first.to_bytes(3, "big") + len(payload).to_bytes(2, "big") + payload


def make_ips(original: bytes, patched: bytes) -> bytes:
    if len(original) != len(patched):
        raise IpsError("IPS here only expresses edits that keep the ROM the same size")
    records = b"".join(_record(patched, start, end) for start, end in _runs(original, patched))
    return HEADER + records + FOOTER
