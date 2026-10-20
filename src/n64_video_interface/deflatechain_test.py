import random
import struct
import zlib

import pytest

from n64_video_interface import deflatechain
from n64_video_interface.conftest import mode_entry
from n64_video_interface.edits import NO_TARGETS, Settings

RNG = random.Random(64)
STUB_AT = 0x1050
CHAIN_AT = 0x20000
TEXT = b"N64 video interface, every option off. " * 0x20
LUI_V0, LUI_T8 = 0x3C020000, 0x3C180000
ADDIU_A1_V0, ADDIU_T8_T8 = 0x24450000, 0x27180000


def padded(data):
    return data + bytes(-len(data) % 16)


def entry_block():
    return padded(TEXT + mode_entry() + TEXT)


def piece(data, level=6):
    packer = zlib.compressobj(level, zlib.DEFLATED, -15)
    return struct.pack(">H", len(data) >> 4) + packer.compress(data) + packer.flush()


def stub(start, end):
    def split(value):
        return (value + 0x8000) >> 16, value & 0xFFFF

    (start_high, start_low), (end_high, end_low) = split(start), split(end)
    return (
        LUI_V0 | start_high,
        LUI_T8 | end_high,
        ADDIU_A1_V0 | start_low,
        ADDIU_T8_T8 | end_low,
    )


def rom_with(*datas, slack=1, level=6, body=None):
    chain = body if body is not None else b"".join(piece(data, level) for data in datas)
    rom = bytearray(CHAIN_AT + len(chain) + slack + 0x100)
    rom[CHAIN_AT : CHAIN_AT + len(chain)] = chain
    struct.pack_into(">4I", rom, STUB_AT, *stub(CHAIN_AT, CHAIN_AT + len(chain) + slack))
    return bytes(rom)


class TestFinding:
    def test_a_chain_named_by_the_stub_is_found(self):
        # Arrange
        first, second = padded(RNG.randbytes(0x40)), entry_block()
        rom = rom_with(first, second)

        # Act
        chains = deflatechain.find_chains(rom)

        # Assert
        assert [[(s.at, s.data) for s in c.streams] for c in chains] == [
            [(CHAIN_AT, first), (CHAIN_AT + len(piece(first)), second)]
        ]

    @pytest.mark.parametrize(
        ("datas", "slack"),
        [((TEXT[:0x100],), 0x40), ((b"",), 1)],
        ids=["bytes left over", "empty stream"],
    )
    def test_a_range_that_is_not_a_chain_finds_nothing(self, datas, slack):
        # Arrange
        rom = rom_with(*datas, slack=slack)

        # Act
        chains = deflatechain.find_chains(rom)

        # Assert
        assert chains == ()

    @pytest.mark.parametrize(
        "body",
        [
            struct.pack(">H", 0x10) + bytes(0x20),
            struct.pack(">H", 0x20) + piece(TEXT[:0x100])[2:],
        ],
        ids=["not deflate", "wrong size"],
    )
    def test_a_range_over_other_bytes_finds_nothing(self, body):
        # Arrange
        rom = rom_with(body=body)

        # Act
        chains = deflatechain.find_chains(rom)

        # Assert
        assert chains == ()

    def test_a_rom_without_the_stub_finds_nothing(self):
        # Arrange
        rom = bytearray(rom_with(entry_block()))
        rom[STUB_AT : STUB_AT + 16] = bytes(16)

        # Act
        chains = deflatechain.find_chains(bytes(rom))

        # Assert
        assert chains == ()


class TestPatching:
    def test_a_target_inside_a_chain_is_patched_in_its_range(self):
        # Arrange
        rom = rom_with(padded(TEXT), entry_block(), level=1)

        # Act
        packed = deflatechain.edit_chains(rom, Settings(antialiasing=False))

        # Assert
        second = deflatechain.find_chains(packed.data)[0].streams[1]
        at = len(TEXT) + 4
        assert (packed.where, len(packed.data), second.data[at : at + 4]) == (
            "in 1 deflate chain",
            len(rom),
            bytes.fromhex("0000321e"),
        )

    def test_a_chain_that_no_longer_fits_is_left_alone(self):
        # Arrange
        near = RNG.randbytes(0x80) + mode_entry() + RNG.randbytes(0x7C)
        far = near[:0x90] + bytes(4) + near[0x94:]
        data = b"".join(RNG.randbytes(0x20) + far + near for _ in range(8))
        rom = rom_with(data, level=9)

        # Act
        packed = deflatechain.edit_chains(rom, Settings(antialiasing=False))

        # Assert
        assert packed == deflatechain.NO_ROOM

    def test_a_chain_without_targets_is_left_alone(self):
        # Arrange
        rom = rom_with(padded(TEXT))

        # Act
        packed = deflatechain.edit_chains(rom, Settings(antialiasing=False))

        # Assert
        assert packed == NO_TARGETS
