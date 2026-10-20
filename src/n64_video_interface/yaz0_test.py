import random

import pytest

from n64_video_interface import yaz0
from n64_video_interface.bitlz import Copy, Literals, expand
from n64_video_interface.conftest import mode_entry
from n64_video_interface.edits import NO_TARGETS, Settings

RNG = random.Random(64)
PLAIN = b"N64 video " * 3 + b"interface"
TOKENS = (Literals(PLAIN[:10]), Copy(10, 20), Literals(PLAIN[30:]))
HEADER = b"Yaz0" + bytes.fromhex("00000027") + bytes(8)
STREAM = HEADER + b"\xffN64 vide" + b"\xdfo \x00\x09\x02inter" + b"\xf0face"
FILE_AT = 0x10000
TEXT = b"N64 video interface, every option off. " * 0x20


def entry_block():
    return TEXT + mode_entry() + TEXT


def stream(data):
    return yaz0.encode(yaz0.optimal(data, 0, len(data)), len(data), bytes(8))


def rom_with(*bodies):
    rom = bytearray(FILE_AT)
    for body in bodies:
        rom += body + bytes(-len(body) % 16)
    return bytes(rom + bytes(0x40))


class TestTheStream:
    def test_tokens_encode_to_the_known_stream(self):
        # Arrange
        tokens = TOKENS

        # Act
        encoded = yaz0.encode(tokens, len(PLAIN), bytes(8))

        # Assert
        assert encoded == STREAM

    def test_the_stream_decodes_back_to_its_tokens_and_length(self):
        # Arrange
        data = bytes(16) + STREAM + bytes(8)

        # Act
        decoded = yaz0.decode(data, 16)

        # Assert
        assert decoded == (TOKENS, len(STREAM))

    @pytest.mark.parametrize(
        "tokens",
        [
            (Literals(b"ab"), Copy(2, 3)),
            (Literals(b"abc"), Copy(3, 17)),
            (Literals(b"a"), Copy(1, 0x111)),
            (Literals(b"a"), Copy(1, 700)),
        ],
        ids=["shortest copy", "longest short copy", "longest copy", "copy cut in pieces"],
    )
    def test_every_copy_length_round_trips(self, tokens):
        # Arrange
        size = len(expand(tokens))

        # Act
        decoded, _ = yaz0.decode(yaz0.encode(tokens, size, bytes(8)), 0)

        # Assert
        assert expand(decoded) == expand(tokens)

    @pytest.mark.parametrize("size", [1, 17, 300, 5000])
    def test_any_data_survives_a_round_trip(self, size):
        # Arrange
        data = bytes(RNG.choice(b"abc\x00") for _ in range(size)) + RNG.randbytes(size)

        # Act
        decoded, _ = yaz0.decode(stream(data), 0)

        # Assert
        assert expand(decoded) == data

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            (b"Yaz0" + bytes.fromhex("00000004") + bytes(8) + b"\x00\x10\x05", "a copy reaches"),
            (STREAM[:-2], "the stream ends before its data"),
            (STREAM[:7] + b"\x14" + STREAM[8:], "the stream does not decode to its size"),
        ],
        ids=["copy before the start", "cut off", "overshoots"],
    )
    def test_a_broken_stream_is_refused(self, data, message):
        # Arrange
        given = data

        # Act
        with pytest.raises(yaz0.FormatError) as caught:
            yaz0.decode(given, 0)

        # Assert
        assert str(caught.value).startswith(message)


class TestCosts:
    @pytest.mark.parametrize(
        ("token", "expected"),
        [(Literals(b"ab"), 18), (Copy(1, 17), 17), (Copy(1, 18), 25), (Copy(1, 700), 75)],
        ids=["two literals", "short copy", "long copy", "copy in three pieces"],
    )
    def test_each_token_costs_its_bits(self, token, expected):
        # Arrange
        given = token

        # Act
        bits = yaz0.cost(given)

        # Assert
        assert bits == expected


class TestFinding:
    def test_files_are_found_with_their_packed_length(self):
        # Arrange
        first, second = stream(entry_block()), stream(TEXT)
        rom = rom_with(first, second)

        # Act
        found = yaz0.find_files(rom)

        # Assert
        assert [(f.at, f.packed) for f in found] == [
            (FILE_AT, len(first)),
            (FILE_AT + len(first) + (-len(first) % 16), len(second)),
        ]

    @pytest.mark.parametrize(
        "body",
        [b"Yaz0" + bytes.fromhex("00000004") + bytes(8) + b"\x00\x10\x05", b"Yaz0" + bytes(12)],
        ids=["does not decode", "empty"],
    )
    def test_a_magic_without_a_file_is_ignored(self, body):
        # Arrange
        rom = rom_with(body)

        # Act
        found = yaz0.find_files(rom)

        # Assert
        assert found == ()


class TestPatching:
    def test_a_target_inside_a_file_is_patched_in_place(self):
        # Arrange
        rom = rom_with(stream(TEXT), stream(entry_block()))

        # Act
        packed = yaz0.edit_files(rom, Settings(antialiasing=False))

        # Assert
        second = yaz0.find_files(packed.data)[1]
        tokens, _ = yaz0.decode(packed.data, second.at)
        at = len(TEXT) + 4
        assert (packed.where, len(packed.data), expand(tokens)[at : at + 4]) == (
            "in 1 Yaz0 file",
            len(rom),
            bytes.fromhex("0000321e"),
        )

    def test_a_file_that_no_longer_fits_is_left_alone(self):
        # Arrange
        near = RNG.randbytes(0x80) + mode_entry() + RNG.randbytes(0x80)
        far = near[:0x90] + bytes(4) + near[0x94:]
        rom = rom_with(stream(b"".join(RNG.randbytes(0x20) + far + near for _ in range(8))))

        # Act
        packed = yaz0.edit_files(rom, Settings(antialiasing=False))

        # Assert
        assert packed == yaz0.NO_ROOM

    def test_a_rom_without_files_has_no_targets(self):
        # Arrange
        rom = bytes(0x4000)

        # Act
        packed = yaz0.edit_files(rom, Settings(antialiasing=False))

        # Assert
        assert packed == NO_TARGETS
