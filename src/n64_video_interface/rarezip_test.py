import random
import struct
import zlib

import pytest

from n64_video_interface import rarezip
from n64_video_interface.conftest import SEALABLE_SIZE, make_rom, mode_entry, routine
from n64_video_interface.edits import CHECKSUM_UNKNOWN, NO_TARGETS, Settings
from n64_video_interface.seal import reseal

GROUP_AT = 0x200000
CRC_BIN_AT = 0x5E78
NEXT_AT = GROUP_AT + 0x8000
ALIGN = 16
NOISE = random.Random(64).randbytes(0x1000)


def deflate(data):
    packer = zlib.compressobj(6, zlib.DEFLATED, -15)
    return packer.compress(data) + packer.flush()


def stream(data):
    return rarezip.MAGIC + struct.pack(">I", len(data)) + deflate(data)


def code_block():
    blob, _ = routine(("dedither", "divot"))
    return bytes(0x100) + blob + bytes(0x100)


def data_block():
    return bytes(0x80) + mode_entry() + bytes(0x80)


def tight_rom():
    data = bytearray(make_rom(size=SEALABLE_SIZE + GROUP_AT))
    base = len(stream(NOISE)) + len(stream(data_block()))
    group = stream(NOISE + bytes(-base % ALIGN - 1)) + stream(data_block())
    data[GROUP_AT : GROUP_AT + len(group) + 1] = group + b"\xff"
    return reseal(bytes(data), "6102")


def rom_with_group(*, crc_bin=True):
    data = bytearray(make_rom(size=SEALABLE_SIZE + GROUP_AT))
    group = stream(code_block()) + stream(data_block())
    data[GROUP_AT : GROUP_AT + len(group)] = group
    tail = stream(bytes(0x40))
    data[NEXT_AT : NEXT_AT + len(tail)] = tail
    if crc_bin:
        pairs = (*rarezip.bk_crc(code_block()), *rarezip.bk_crc(data_block()))
        struct.pack_into(">4I", data, CRC_BIN_AT, *pairs)
    return reseal(bytes(data), "6102")


class TestTheChecksum:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [(b"", (0, 0xFFFFFFFF)), (b"\x01", (1, 0xFFFFFFFD)), (b"\x01\x02", (3, 0xFFFFFFED))],
    )
    def test_the_sum_and_the_shifted_xor_follow_the_game(self, data, expected):
        # Arrange
        content = data

        # Act
        pair = rarezip.bk_crc(content)

        # Assert
        assert pair == expected


class TestFindingStreams:
    def test_a_group_of_back_to_back_streams_is_found_with_its_span(self):
        # Arrange
        rom = rom_with_group()

        # Act
        groups = rarezip.find_groups(rom)

        # Assert
        assert [(g.start, g.end, [s.data for s in g.streams]) for g in groups[:1]] == [
            (GROUP_AT, NEXT_AT, [code_block(), data_block()])
        ]

    @pytest.mark.parametrize(
        "tail",
        [
            struct.pack(">I", 0x400) + bytes(0x40),
            struct.pack(">I", 0x10) + deflate(bytes(0x10)),
            struct.pack(">I", 0x80) + deflate(bytes(0x40)),
            b"\x00",
        ],
    )
    def test_a_magic_without_a_valid_stream_is_ignored(self, tail):
        # Arrange
        rom = bytes(0x100) + rarezip.MAGIC + tail

        # Act
        groups = rarezip.find_groups(rom)

        # Assert
        assert groups == ()


class TestRepacking:
    def test_edited_streams_are_packed_back_to_back_and_padded_to_the_span(self):
        # Arrange
        rom = rom_with_group()
        group = rarezip.find_groups(rom)[0]
        edited = (bytes(len(code_block())), data_block())

        # Act
        packed = rarezip.repack(group, edited)

        # Assert
        assert [s.data for s in rarezip.find_groups(packed + bytes(6))[0].streams] == list(edited)
        assert len(packed) == group.end - group.start

    def test_streams_that_no_longer_fit_are_refused(self):
        # Arrange
        rom = rom_with_group()
        group = rarezip.find_groups(rom)[0]
        noise = random.Random(64).randbytes(0x10000)

        # Act
        packed = rarezip.repack(group, (noise, data_block()))

        # Assert
        assert packed is None


class TestThePatch:
    def test_the_compressed_targets_are_edited_and_the_checksums_follow(self):
        # Arrange
        rom = rom_with_group()

        # Act
        result = rarezip.patch(rom, Settings(antialiasing=False, dither_filter=False))

        # Assert
        groups = rarezip.find_groups(result.data)
        code, data = (s.data for s in groups[0].streams)
        stored = struct.unpack_from(">4I", result.data, CRC_BIN_AT)
        assert (
            result.reason,
            stored,
            data[0x84:0x88],
            code != code_block(),
        ) == (
            "1 of 1 mode entries changed, 1 dedither request neutralised, in 1 compressed group",
            (*rarezip.bk_crc(code), *rarezip.bk_crc(data)),
            bytes.fromhex("0000321e"),
            True,
        )

    def test_a_group_whose_checksum_cannot_be_found_is_refused(self):
        # Arrange
        rom = rom_with_group(crc_bin=False)

        # Act
        result = rarezip.patch(rom, Settings(antialiasing=False))

        # Assert
        assert (result.applied, result.reason) == (False, rarezip.NO_CHECKSUM)

    def test_a_rom_without_compressed_targets_is_refused(self):
        # Arrange
        rom = reseal(make_rom(size=SEALABLE_SIZE), "6102")

        # Act
        result = rarezip.patch(rom, Settings(antialiasing=False))

        # Assert
        assert (result.applied, result.reason) == (False, NO_TARGETS)

    def test_a_group_that_no_longer_fits_is_refused(self):
        # Arrange
        rom = tight_rom()

        # Act
        result = rarezip.patch(rom, Settings(antialiasing=False))

        # Assert
        assert (result.applied, result.reason) == (False, rarezip.NO_ROOM)

    def test_a_rom_whose_checksum_does_not_validate_is_refused(self):
        # Arrange
        rom = bytearray(rom_with_group())
        rom[0x2000] ^= 0xFF

        # Act
        result = rarezip.patch(bytes(rom), Settings(antialiasing=False))

        # Assert
        assert (result.applied, result.reason) == (False, CHECKSUM_UNKNOWN)
