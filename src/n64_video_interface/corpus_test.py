import hashlib
import json
import os
from pathlib import Path

import pytest

from n64_video_interface import checksum
from n64_video_interface.byteorder import ByteOrder, detect, swap
from n64_video_interface.features import find_special_features
from n64_video_interface.patch import NO_TABLE, safe_patch

ROM_DIRS = "N64VI_ROM_DIRS"
MANIFEST = Path(__file__).resolve().parents[2] / "artifacts.manifest.json"
SETTINGS = {"antialiasing": False, "divot": False, "gamma_dither": False, "dither_filter": False}
CHECKSUM_WORDS = frozenset(range(0x10, 0x18))
IPS_FOOTER = b"EOF"
ROUTINE_BEFORE = 0x100
ROUTINE_AFTER = 0x80
ORACLE_FALSE_POSITIVES = {
    "6f1640aaa1fcbfb1d92694f91e5645bab038a2a3a06ce122567a36598b0967ec": frozenset(
        {0x5183C0, 0x53AC9C}
    ),
}


def artifacts():
    return json.loads(MANIFEST.read_text())["artifacts"]


def located():
    folders = [Path(part) for part in os.environ.get(ROM_DIRS, "").split(os.pathsep) if part]
    wanted = {entry["size"] for entry in artifacts()}
    found = {}
    for folder in folders:
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.stat().st_size in wanted:
                found.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(), path)
    return found


LOCATED = located() if os.environ.get(ROM_DIRS) else {}
PRESENT = [entry for entry in artifacts() if entry["sha256"] in LOCATED]
pytestmark = pytest.mark.artifacts


def big_endian(data):
    order = detect(data) or ByteOrder.Z64
    return swap(data, order)


def ips_records(patch):
    at, records = 5, []
    while patch[at : at + 3] != IPS_FOOTER:
        offset = int.from_bytes(patch[at : at + 3], "big")
        size = int.from_bytes(patch[at + 3 : at + 5], "big")
        at += 5
        if size == 0:
            records.append((offset, int.from_bytes(patch[at : at + 2], "big")))
            at += 3
        else:
            records.append((offset, size))
            at += size
    return records


@pytest.mark.parametrize("entry", PRESENT, ids=lambda entry: entry["name"])
def test_the_header_checksum_verdict_matches_the_manifest(entry):
    # Arrange
    data = big_endian(LOCATED[entry["sha256"]].read_bytes())

    # Act
    valid, _ = checksum.verify(data)

    # Assert
    assert ("valid" if valid else "invalid") == entry["header_checksum"]


KNOWN_REFUSALS = frozenset({NO_TABLE})


def outcome(data, result):
    if not result.applied:
        return ("refused", result.reason in KNOWN_REFUSALS)
    before, after = big_endian(data), big_endian(result.data)
    edited = {offset + i for offset, _, _ in result.changes for i in range(4)}
    moved = {i for i in range(len(before)) if before[i] != after[i]}
    resealed = checksum.verify(after) == (True, result.cic)
    return ("applied", resealed and moved <= edited | CHECKSUM_WORDS)


@pytest.mark.parametrize(
    "entry", [e for e in PRESENT if e["header_checksum"] == "valid"], ids=lambda e: e["name"]
)
def test_a_patch_reseals_under_its_own_chip_and_changes_nothing_else(entry):
    # Arrange
    data = LOCATED[entry["sha256"]].read_bytes()

    # Act
    result = safe_patch(data, **SETTINGS)

    # Assert
    assert outcome(data, result)[1] is True


@pytest.mark.parametrize(
    "entry",
    [
        e
        for e in PRESENT
        if e["set"] == "aa"
        and e["header_checksum"] == "valid"
        and LOCATED[e["sha256"]].with_suffix(".ips").exists()
    ],
    ids=lambda e: e["name"],
)
def test_every_mode_word_the_other_tool_edits_is_edited_here_too(entry):
    # Arrange
    rom = LOCATED[entry["sha256"]]
    data = big_endian(rom.read_bytes())
    routines = [
        range(site.offset - ROUTINE_BEFORE, site.masks["antialias"] + ROUTINE_AFTER)
        for site in find_special_features(data)
    ]
    theirs = {
        offset & ~3
        for offset, _ in ips_records(rom.with_suffix(".ips").read_bytes())
        if offset not in CHECKSUM_WORDS and not any(offset in routine for routine in routines)
    }

    # Act
    result = safe_patch(rom.read_bytes(), antialiasing=False, divot=False)

    # Assert
    assert theirs - ORACLE_FALSE_POSITIVES.get(entry["sha256"], frozenset()) <= {
        offset for offset, _, _ in result.changes
    }
