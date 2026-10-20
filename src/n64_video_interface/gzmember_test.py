import random
import struct
import zlib

import pytest

from n64_video_interface import gzmember
from n64_video_interface.conftest import mode_entry
from n64_video_interface.edits import NO_TARGETS, Settings

RNG = random.Random(64)
NAME = b"hd_code_data.raw"


def entry_block():
    return RNG.randbytes(0x80) + mode_entry() + RNG.randbytes(0x80)


def member(data, *, name=NAME, level=9):
    packer = zlib.compressobj(level, zlib.DEFLATED, -15)
    body = packer.compress(data) + packer.flush()
    head = b"\x1f\x8b\x08" + bytes([0x08 if name else 0]) + bytes(4) + b"\x02\x03"
    return (
        head
        + (name + b"\x00" if name else b"")
        + body
        + struct.pack("<2I", zlib.crc32(data), len(data))
    )


def rom_with(*members):
    return bytes(0x40) + b"".join(members) + bytes(0x40)


class TestFinding:
    @pytest.mark.parametrize("name", [NAME, b""], ids=["named", "unnamed"])
    def test_a_member_is_found_with_its_data(self, name):
        # Arrange
        data = entry_block()
        rom = rom_with(member(data, name=name))

        # Act
        members = gzmember.find_members(rom)

        # Assert
        assert [(m.at, m.data, m.length) for m in members] == [
            (0x40, data, len(member(data, name=name)))
        ]

    @pytest.mark.parametrize(
        "damage",
        [
            lambda m: m[:3] + b"\x1f" + m[4:],
            lambda m: m[:-8] + struct.pack("<I", 1) + m[-4:],
            lambda m: m[:-4] + struct.pack("<I", 1),
            lambda m: m[:27] + b"\x07" + m[28:],
        ],
        ids=["reserved flags", "checksum", "size", "broken deflate"],
    )
    def test_a_member_that_does_not_hold_up_is_skipped(self, damage):
        # Arrange
        rom = rom_with(damage(member(entry_block())))

        # Act
        members = gzmember.find_members(rom)

        # Assert
        assert members == ()

    def test_a_member_cut_off_by_the_end_of_the_data_is_skipped(self):
        # Arrange
        rom = member(entry_block())[:-20]

        # Act
        members = gzmember.find_members(rom)

        # Assert
        assert members == ()


class TestPatching:
    def test_a_target_inside_a_member_is_patched_and_its_trailer_follows(self):
        # Arrange
        rom = rom_with(member(RNG.randbytes(0x40)), member(entry_block()))

        # Act
        packed = gzmember.edit_members(rom, Settings(antialiasing=False))

        # Assert
        found = gzmember.find_members(packed.data)
        assert (packed.where, found[1].data[0x84:0x88], found[1].name) == (
            "in 1 gzip member",
            bytes.fromhex("0000321e"),
            NAME,
        )

    def test_a_member_that_no_longer_fits_is_left_alone(self):
        # Arrange
        pairs = []
        for _ in range(8):
            near = entry_block()
            pairs.append(near[:0x90] + bytes(4) + near[0x94:] + near)
        rom = rom_with(member(b"".join(pairs)))

        # Act
        packed = gzmember.edit_members(rom, Settings(antialiasing=False))

        # Assert
        assert packed == gzmember.NO_ROOM

    def test_a_rom_without_members_has_no_targets(self):
        # Arrange
        rom = bytes(0x400)

        # Act
        packed = gzmember.edit_members(rom, Settings(antialiasing=False))

        # Assert
        assert packed == NO_TARGETS
