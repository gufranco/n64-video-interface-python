import random
import struct

import pytest

from n64_video_interface import rnc, rnc_codec, rnc_parse
from n64_video_interface.conftest import mode_entry
from n64_video_interface.edits import NO_TARGETS, Settings
from n64_video_interface.rnc_codec import Chunk, Sub

UNIFORM = ((4,) * 16, (4,) * 16, (4,) * 16)
STREAM_AT = 0x40
RNG = random.Random(64)
BLOCK = RNG.randbytes(0x100)
VSYNC_AT = 0x90


def packed_stream(chunks, size):
    body = rnc_codec.encode(chunks)
    crc = rnc.crc16(rnc_codec.expand(chunks))
    leeway = rnc.leeway(rnc.header(size, crc, body, 0, len(chunks)) + body, 0, size)
    return rnc.header(size, crc, body, leeway, len(chunks)) + body


def optimal_stream(data):
    subs = rnc_parse.optimal(data, 0, len(data), UNIFORM)
    for _ in range(rnc_parse.ROUNDS):
        subs = rnc_parse.optimal(data, 0, len(data), rnc_codec.tables_for(subs))
    return packed_stream((Chunk(rnc_codec.tables_for(subs), subs),), len(data))


def rom_with(stream):
    return bytes(STREAM_AT) + stream + bytes(0x40)


def pair_differing_in_vsync(seed):
    block = random.Random(seed).randbytes(0x100)
    near = block[:0x80] + mode_entry() + block[0x80:]
    return near[:VSYNC_AT] + bytes(4) + near[VSYNC_AT + 4 :] + near


def entry_block():
    return bytes(0x80) + mode_entry() + bytes(0x80)


class TestTheContainer:
    def test_a_stream_is_found_with_its_header_fields(self):
        # Arrange
        data = entry_block()
        rom = rom_with(optimal_stream(data))

        # Act
        streams = rnc.find_streams(rom)

        # Assert
        assert [(s.at, s.size, s.crc) for s in streams] == [(STREAM_AT, len(data), rnc.crc16(data))]

    @pytest.mark.parametrize(
        "damage",
        [
            lambda s: s[:3] + b"\x02" + s[4:],
            lambda s: s[:4] + struct.pack(">I", 0) + s[8:],
            lambda s: s[:8] + struct.pack(">I", 0x7FFFFFF) + s[12:],
            lambda s: s[:-1] + bytes([s[-1] ^ 0xFF]),
        ],
        ids=["another method", "empty", "longer than the data", "packed bytes damaged"],
    )
    def test_a_header_that_does_not_hold_up_is_skipped(self, damage):
        # Arrange
        rom = rom_with(damage(optimal_stream(entry_block())))

        # Act
        streams = rnc.find_streams(rom)

        # Assert
        assert streams == ()

    def test_a_header_cut_off_by_the_end_of_the_data_is_skipped(self):
        # Arrange
        rom = bytes(0x40) + rnc.MAGIC + b"\x01"

        # Act
        streams = rnc.find_streams(rom)

        # Assert
        assert streams == ()

    @pytest.mark.parametrize(
        ("data", "expected"),
        [(b"\x00", 0x0000), (b"123456789", 0xBB3D)],
        ids=["one zero", "check string"],
    )
    def test_the_checksum_is_crc16_arc(self, data, expected):
        # Arrange
        given = data

        # Act
        value = rnc.crc16(given)

        # Assert
        assert value == expected

    def test_the_leeway_follows_the_input_falling_behind_the_output(self):
        # Arrange
        tables = ((15, 8, 1), (0, 1) + (0,) * 8 + (15,), (15,) + (0,) * 9 + (1,))
        tail = tuple(Sub(bytes([index]), 0x300, 2) for index in range(64))
        chunks = (Chunk(tables, (Sub(b"ab", 2, 0x400), *tail, Sub(b""))),)
        stream = packed_stream(chunks, 0x4C2)

        # Act
        margin = rnc.leeway(stream, 0, 0x4C2)

        # Assert
        assert margin == 245


class TestEditing:
    def test_a_target_inside_a_stream_is_patched_and_the_header_follows(self):
        # Arrange
        rom = rom_with(optimal_stream(entry_block()))

        # Act
        packed = rnc.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        stream = rnc.find_streams(packed.data)[0]
        data = rnc_codec.expand(rnc_codec.decode(packed.data, stream.at, stream.size))
        assert (packed.where, data[0x84:0x88], stream.crc == rnc.crc16(data)) == (
            "in 1 RNC stream",
            bytes.fromhex("0000321e"),
            True,
        )

    def test_a_stream_with_nothing_to_change_is_left_as_it_was(self):
        # Arrange
        plain = optimal_stream(BLOCK)
        rom = rom_with(plain + optimal_stream(entry_block()))

        # Act
        packed = rnc.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        assert (packed.count, packed.data[STREAM_AT : STREAM_AT + len(plain)]) == (1, plain)

    def test_a_stream_that_no_longer_fits_is_left_alone(self):
        # Arrange
        data = b"".join(pair_differing_in_vsync(seed) for seed in range(8))
        rom = rom_with(optimal_stream(data))

        # Act
        packed = rnc.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        assert packed == rnc.NO_ROOM

    def test_a_rom_without_streams_has_no_targets(self):
        # Arrange
        rom = bytes(0x400)

        # Act
        packed = rnc.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        assert packed == NO_TARGETS
