import random
import struct

import pytest

from n64_video_interface import mioblock
from n64_video_interface.bitlz import Copy, Literals, expand
from n64_video_interface.conftest import mode_entry
from n64_video_interface.edits import NO_TARGETS, Settings

RNG = random.Random(64)
SLOT = mioblock.SLOT
RECORD_AT = 0x6898
CHAIN_AT = 0x200000
SMALL = b"abcabcabc"
SMALL_BLOCK = bytes.fromhex("0372 0009 000c 000e e0000000 3002 616263")
TEXT = b"N64 video interface, every option off. " * 0x20


def entry_block():
    return TEXT + mode_entry() + TEXT


def body_of(data, slots):
    blocks = mioblock.pack(mioblock.optimal(data, 0, len(data)), data, (SLOT,) * slots)
    return b"".join(block + bytes(SLOT - len(block)) for block in blocks[:-1]) + blocks[-1]


def rom_with(data, *, slots=1, body=None):
    packed = body if body is not None else body_of(data, slots)
    rom = bytearray(CHAIN_AT + len(packed) + 0x100)
    rom[CHAIN_AT : CHAIN_AT + len(packed)] = packed
    record = (CHAIN_AT, CHAIN_AT + len(packed), len(data))
    rom[RECORD_AT : RECORD_AT + 12] = struct.pack(">3I", *record)
    return bytes(rom)


class TestBlocks:
    def test_tokens_pack_into_the_known_block(self):
        # Arrange
        tokens = (Literals(b"abc"), Copy(3, 6))

        # Act
        blocks = mioblock.pack(tokens, SMALL, (SLOT,))

        # Assert
        assert blocks == (SMALL_BLOCK,)

    def test_a_chain_decodes_to_its_tokens(self):
        # Arrange
        rom = bytes(0x10) + SMALL_BLOCK

        # Act
        tokens = mioblock.decode_chain(rom, 0x10, 0x10 + len(SMALL_BLOCK), len(SMALL))

        # Assert
        assert tokens == (Literals(b"abc"), Copy(3, 6))

    @pytest.mark.parametrize(("size", "slots"), [(1, 1), (300, 1), (0x3000, 4)])
    def test_any_data_survives_a_round_trip_over_its_slots(self, size, slots):
        # Arrange
        data = bytes(RNG.choice(b"abc\x00") for _ in range(size)) + RNG.randbytes(size // 4)
        body = body_of(data, slots)

        # Act
        tokens = mioblock.decode_chain(body, 0, len(body), len(data))

        # Assert
        assert expand(tokens) == data

    def test_a_copy_longer_than_a_link_is_cut_into_links(self):
        # Arrange
        tokens = (Literals(b"a"), Copy(1, 40))
        data = expand(tokens)

        # Act
        blocks = mioblock.pack(tokens, data, (SLOT,))

        # Assert
        assert expand(mioblock.decode_chain(blocks[0], 0, len(blocks[0]), len(data))) == data

    @pytest.mark.parametrize(
        ("rom", "size", "message"),
        [
            (bytes.fromhex("0000 0003 000c 000e 00000000 0000"), 3, "a copy reaches before"),
            (bytes.fromhex("0001 0001 000c 000c 80000000 61"), 1, "a block's sum does not"),
            (SMALL_BLOCK[:-1], 9, "the chain ends before"),
            (SMALL_BLOCK, 8, "the chain does not decode to its size"),
        ],
        ids=["copy before the start", "wrong sum", "cut off", "wrong size"],
    )
    def test_a_broken_chain_is_refused(self, rom, size, message):
        # Arrange
        data = rom

        # Act
        with pytest.raises(mioblock.FormatError) as caught:
            mioblock.decode_chain(data, 0, len(data), size)

        # Assert
        assert str(caught.value).startswith(message)

    def test_tokens_that_do_not_fit_their_slots_are_refused(self):
        # Arrange
        data = RNG.randbytes(0x100)

        # Act
        blocks = mioblock.pack((Literals(data),), data, (0x40,))

        # Assert
        assert blocks is None

    def test_a_slot_too_small_for_a_single_unit_is_refused(self):
        # Arrange
        tokens = (Literals(b"a"),)

        # Act
        blocks = mioblock.pack(tokens, b"a", (mioblock.BLOCK.size,))

        # Assert
        assert blocks is None

    def test_every_slot_gets_a_block_even_when_fewer_would_do(self):
        # Arrange
        data = TEXT

        # Act
        blocks = mioblock.pack(mioblock.optimal(data, 0, len(data)), data, (SLOT,) * 3)

        # Assert
        joined = b"".join(block + bytes(SLOT - len(block)) for block in blocks)
        assert (len(blocks), expand(mioblock.decode_chain(joined, 0, len(joined), len(data)))) == (
            3,
            data,
        )


class TestFinding:
    def test_a_chain_is_found_through_its_record(self):
        # Arrange
        rom = rom_with(entry_block(), slots=2)

        # Act
        chains = mioblock.find_chains(rom)

        # Assert
        assert [(c.start, c.size) for c in chains] == [(CHAIN_AT, len(entry_block()))]

    @pytest.mark.parametrize(
        "record",
        [(0x10, 0x20, 0x10), (CHAIN_AT, CHAIN_AT, 0x10), (CHAIN_AT, 0x40000000, 0x10)],
        ids=["inside the boot code", "empty", "past the end"],
    )
    def test_a_record_that_does_not_name_a_chain_finds_nothing(self, record):
        # Arrange
        rom = bytearray(rom_with(entry_block()))
        rom[RECORD_AT : RECORD_AT + 12] = struct.pack(">3I", *record)

        # Act
        chains = mioblock.find_chains(bytes(rom))

        # Assert
        assert chains == ()

    def test_a_record_over_bytes_that_are_not_blocks_finds_nothing(self):
        # Arrange
        rom = rom_with(SMALL, body=bytes.fromhex("0000 0003 000c 000e 00000000 0000"))

        # Act
        chains = mioblock.find_chains(rom)

        # Assert
        assert chains == ()


class TestPatching:
    def test_a_target_inside_a_chain_is_patched_in_its_slots(self):
        # Arrange
        rom = rom_with(entry_block(), slots=2)

        # Act
        packed = mioblock.edit_chains(rom, Settings(antialiasing=False))

        # Assert
        chain = mioblock.find_chains(packed.data)[0]
        data = expand(mioblock.decode_chain(packed.data, chain.start, chain.end, chain.size))
        at = len(TEXT) + 4
        assert (packed.where, len(packed.data), data[at : at + 4]) == (
            "in 1 MIO0 block chain",
            len(rom),
            bytes.fromhex("0000321e"),
        )

    def test_a_chain_that_no_longer_fits_is_left_alone(self):
        # Arrange
        near = RNG.randbytes(0x80) + mode_entry() + RNG.randbytes(0x80)
        far = near[:0x90] + bytes(4) + near[0x94:]
        data = b"".join(RNG.randbytes(0x20) + far + near for _ in range(8))
        rom = rom_with(data, slots=1)

        # Act
        packed = mioblock.edit_chains(rom, Settings(antialiasing=False))

        # Assert
        assert packed == mioblock.NO_ROOM

    def test_a_chain_without_targets_is_left_alone(self):
        # Arrange
        rom = rom_with(TEXT)

        # Act
        packed = mioblock.edit_chains(rom, Settings(antialiasing=False))

        # Assert
        assert packed == NO_TARGETS

    def test_a_rom_without_chains_has_no_targets(self):
        # Arrange
        rom = bytes(0x8000)

        # Act
        packed = mioblock.edit_chains(rom, Settings(antialiasing=False))

        # Assert
        assert packed == NO_TARGETS
