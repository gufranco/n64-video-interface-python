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


BARE_AT = 0x158D0
RECORD_AT = 0x13B0
TEXT = b"N64 video interface " * 64
TEXT_CTRL = len(TEXT) + 4


def text_block():
    return TEXT + mode_entry() + TEXT


def member(data, *, name=NAME, level=1):
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


def bare(data):
    packer = zlib.compressobj(1, zlib.DEFLATED, -15)
    body = packer.compress(data) + packer.flush()
    return body + struct.pack("<2I", zlib.crc32(data), len(data))


def rom_with_bare(data, *, record=None):
    member = bare(data)
    rom = bytearray(BARE_AT + len(member) + 0x40)
    rom[BARE_AT : BARE_AT + len(member)] = member
    rom[RECORD_AT : RECORD_AT + 8] = struct.pack(">2I", *(record or (BARE_AT, len(member))))
    return bytes(rom)


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


class TestFindingBareMembers:
    def test_a_member_without_a_header_is_found_through_its_record(self):
        # Arrange
        data = entry_block()
        rom = rom_with_bare(data)

        # Act
        members = gzmember.find_members(rom)

        # Assert
        assert [(m.at, m.header, m.data, m.length) for m in members] == [
            (BARE_AT, b"", data, len(bare(data)))
        ]

    @pytest.mark.parametrize(
        "record",
        [
            (BARE_AT, 4),
            (BARE_AT, 0x40000000),
            (0x10, 0x100),
            (BARE_AT + 1, 0x100),
        ],
        ids=["shorter than a trailer", "past the end", "inside the boot code", "not a stream"],
    )
    def test_a_record_that_does_not_point_at_a_member_finds_nothing(self, record):
        # Arrange
        rom = rom_with_bare(entry_block(), record=record)

        # Act
        members = gzmember.find_members(rom)

        # Assert
        assert members == ()

    def test_a_record_whose_length_differs_from_the_stream_finds_nothing(self):
        # Arrange
        data = entry_block()
        rom = rom_with_bare(data, record=(BARE_AT, len(bare(data)) + 4))

        # Act
        members = gzmember.find_members(rom)

        # Assert
        assert members == ()


class TestDeflating:
    @pytest.mark.parametrize("slack", range(2, 14))
    def test_the_stream_fills_its_slot_exactly_and_inflates_to_the_data(self, slack):
        # Arrange
        data = entry_block()
        packer = zlib.compressobj(9, zlib.DEFLATED, -15)
        floor = len(packer.compress(data) + packer.flush(zlib.Z_SYNC_FLUSH))
        slot = floor + slack + 20

        # Act
        stream = gzmember.deflate(data, slot)

        # Assert
        inflater = zlib.decompressobj(-15)
        assert (len(stream), inflater.decompress(stream), inflater.eof) == (slot, data, True)

    def test_a_slot_smaller_than_the_stream_has_no_fill(self):
        # Arrange
        data = entry_block()

        # Act
        stream = gzmember.deflate(data, 4)

        # Assert
        assert stream is None


class TestPatching:
    def test_a_target_inside_a_member_is_patched_and_its_trailer_follows(self):
        # Arrange
        rom = rom_with(member(RNG.randbytes(0x40)), member(text_block()))

        # Act
        packed = gzmember.edit_members(rom, Settings(antialiasing=False))

        # Assert
        found = gzmember.find_members(packed.data)
        before = gzmember.find_members(rom)
        assert (
            packed.where,
            found[1].data[TEXT_CTRL : TEXT_CTRL + 4],
            found[1].name,
            found[1].length,
        ) == (
            "in 1 gzip member",
            bytes.fromhex("0000321e"),
            NAME,
            before[1].length,
        )

    def test_a_bare_member_is_patched_in_place_with_its_trailer(self):
        # Arrange
        rom = rom_with_bare(text_block())

        # Act
        packed = gzmember.edit_members(rom, Settings(antialiasing=False))

        # Assert
        found = gzmember.find_members(packed.data)
        assert (
            packed.where,
            [(m.at, m.length) for m in found],
            found[0].data[TEXT_CTRL : TEXT_CTRL + 4],
        ) == (
            "in 1 gzip member",
            [(BARE_AT, len(bare(text_block())))],
            bytes.fromhex("0000321e"),
        )

    def test_a_member_that_no_longer_fits_is_left_alone(self):
        # Arrange
        pairs = []
        for _ in range(8):
            near = entry_block()
            pairs.append(near[:0x90] + bytes(4) + near[0x94:] + near)
        rom = rom_with(member(b"".join(pairs), level=9))

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
