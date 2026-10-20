import random
import struct

import pytest

from n64_video_interface import lzhuf
from n64_video_interface.bitlz import Copy, Literals, expand
from n64_video_interface.conftest import mode_entry
from n64_video_interface.edits import NO_TARGETS, Settings

RNG = random.Random(64)
PLAIN = b"N64 video " * 3 + b"interface"
TOKENS = (Literals(PLAIN[:10]), Copy(10, 20), Literals(PLAIN[30:]))
KNOWN_TOKENS = (Literals(b"\x00\x20\x00"), Copy(2, 5), Literals(b"\xff"), Copy(7, 3))
KNOWN_STREAM = bytes.fromhex("0000000c c66b3163 80317180 60")
TEXT = b"N64 video interface, every option off. " * 0x20
HEADER_AT = 0x1F30
STREAM_AT = 0x2AC0


def entry_block():
    return TEXT + mode_entry() + TEXT


def stream(data):
    return lzhuf.encode(lzhuf.optimal(data, 0, len(data)), len(data))


def rom_with(body, *, slack=0x10, flag=1):
    rom = bytearray(STREAM_AT + len(body) + slack + 0x100)
    end = STREAM_AT + len(body) + slack
    rom[HEADER_AT : HEADER_AT + 16] = b"Ogre" + struct.pack(">2H2I", 0x1000, flag, STREAM_AT, end)
    rom[STREAM_AT : STREAM_AT + len(body)] = body
    return bytes(rom)


class TestTheStream:
    def test_tokens_encode_to_the_known_stream(self):
        # Arrange
        tokens = KNOWN_TOKENS

        # Act
        encoded = lzhuf.encode(tokens, 12)

        # Assert
        assert encoded == KNOWN_STREAM

    def test_the_known_stream_decodes_to_its_tokens(self):
        # Arrange
        data = KNOWN_STREAM

        # Act
        decoded = lzhuf.decode(data, 0)

        # Assert
        assert decoded == (KNOWN_TOKENS, len(KNOWN_STREAM))

    def test_tokens_survive_a_round_trip(self):
        # Arrange
        encoded = lzhuf.encode(TOKENS, len(PLAIN))

        # Act
        decoded, used = lzhuf.decode(encoded, 0)

        # Assert
        assert (decoded, used) == (TOKENS, len(encoded))

    def test_a_stream_starts_with_its_size_and_holds_no_more(self):
        # Arrange
        tokens = ()

        # Act
        encoded = lzhuf.encode(tokens, 0)

        # Assert
        assert encoded == bytes(4)

    @pytest.mark.parametrize("size", [1, 17, 300, 5000, 0x9000])
    def test_any_data_survives_a_round_trip(self, size):
        # Arrange
        data = bytes(RNG.choice(b"abc\x00") for _ in range(size)) + RNG.randbytes(size // 8)

        # Act
        decoded, _ = lzhuf.decode(stream(data), 0)

        # Assert
        assert expand(decoded) == data

    def test_a_copy_longer_than_the_longest_symbol_is_cut_into_pieces(self):
        # Arrange
        tokens = (Literals(b"a"), Copy(1, 200))

        # Act
        decoded, _ = lzhuf.decode(lzhuf.encode(tokens, 201), 0)

        # Assert
        assert expand(decoded) == b"a" * 201

    def test_a_long_run_of_one_symbol_rebuilds_the_tree(self):
        # Arrange
        tokens = tuple(Literals(b"a") for _ in range(lzhuf.MAX_FREQ + 10))

        # Act
        decoded, _ = lzhuf.decode(lzhuf.encode(tokens, len(tokens)), 0)

        # Assert
        assert expand(decoded) == b"a" * len(tokens)

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            (lzhuf.encode((Literals(b"a"), Copy(2, 3)), 4), "a copy reaches before the start"),
            (lzhuf.encode(TOKENS, len(PLAIN) - 14), "the stream does not decode to its size"),
        ],
        ids=["copy before the start", "overshoots"],
    )
    def test_a_broken_stream_is_refused(self, data, message):
        # Arrange
        given = data

        # Act
        with pytest.raises(lzhuf.FormatError) as caught:
            lzhuf.decode(given, 0)

        # Assert
        assert str(caught.value).startswith(message)


class TestCosts:
    def test_a_far_copy_costs_more_than_a_near_one(self):
        # Arrange
        near, far = Copy(1, 20), Copy(4000, 20)

        # Act
        prices = (lzhuf.cost(near), lzhuf.cost(far))

        # Assert
        assert prices[1] - prices[0] == (8 - 3) * lzhuf.SCALE


class TestFinding:
    def test_the_stream_its_header_names_is_found(self):
        # Arrange
        body = stream(entry_block())
        rom = rom_with(body)

        # Act
        found = lzhuf.find_streams(rom)

        # Assert
        assert [(s.at, s.size, s.slot) for s in found] == [
            (STREAM_AT, len(entry_block()), len(body) + 0x10)
        ]

    @pytest.mark.parametrize(
        ("body", "flag"),
        [
            (lzhuf.encode((Literals(b"a"), Copy(2, 3)), 4), 1),
            (stream(TEXT), 0),
            (lzhuf.encode((), 0) + bytes(8), 1),
        ],
        ids=["does not decode", "stored", "empty"],
    )
    def test_a_header_over_anything_else_finds_nothing(self, body, flag):
        # Arrange
        rom = rom_with(body, flag=flag)

        # Act
        found = lzhuf.find_streams(rom)

        # Assert
        assert found == ()

    def test_a_header_past_the_end_finds_nothing(self):
        # Arrange
        rom = bytearray(rom_with(stream(TEXT)))
        struct.pack_into(">I", rom, HEADER_AT + 12, 0x40000000)

        # Act
        found = lzhuf.find_streams(bytes(rom))

        # Assert
        assert found == ()


class TestPatching:
    def test_a_target_inside_the_stream_is_patched_in_its_slot(self):
        # Arrange
        rom = rom_with(stream(entry_block()))

        # Act
        packed = lzhuf.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        tokens, _ = lzhuf.decode(packed.data, STREAM_AT)
        at = len(TEXT) + 4
        assert (packed.where, len(packed.data), expand(tokens)[at : at + 4]) == (
            "in 1 LZHUF stream",
            len(rom),
            bytes.fromhex("0000321e"),
        )

    def test_a_stream_that_no_longer_fits_is_left_alone(self):
        # Arrange
        near = RNG.randbytes(0x80) + mode_entry() + RNG.randbytes(0x80)
        far = near[:0x90] + bytes(4) + near[0x94:]
        data = b"".join(RNG.randbytes(0x20) + far + near for _ in range(8))
        rom = rom_with(stream(data), slack=0)

        # Act
        packed = lzhuf.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        assert packed == lzhuf.NO_ROOM

    def test_a_stream_without_targets_is_left_alone(self):
        # Arrange
        rom = rom_with(stream(TEXT))

        # Act
        packed = lzhuf.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        assert packed == NO_TARGETS

    def test_a_rom_without_headers_has_no_targets(self):
        # Arrange
        rom = bytes(0x4000)

        # Act
        packed = lzhuf.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        assert packed == NO_TARGETS
