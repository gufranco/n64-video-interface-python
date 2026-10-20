import random

import pytest

from n64_video_interface import flaglz
from n64_video_interface.bitlz import Copy, Literals, expand
from n64_video_interface.conftest import mode_entry
from n64_video_interface.edits import NO_TARGETS, Settings

RNG = random.Random(64)
PLAIN = b"N64 video " * 3 + b"interface"
TOKENS = (Literals(PLAIN[:10]), Copy(10, 20), Literals(PLAIN[30:]))
STREAM = (
    bytes.fromhex("01000027") + b"\x00N64 vide" + b"\x20o \x00\x0a\x03inter" + b"\x08face\x00\x00"
)
STREAM_AT = 0x1350


def entry_block():
    return RNG.randbytes(0x80) + mode_entry() + RNG.randbytes(0x80)


def stream(data):
    return flaglz.encode(flaglz.optimal(data, 0, len(data)), len(data))


def rom_with(*streams, at=STREAM_AT):
    rom = bytearray(at)
    for body in streams:
        rom += body + bytes(-len(body) % 16)
    return bytes(rom + bytes(0x40))


class TestTheStream:
    def test_tokens_encode_to_the_known_stream(self):
        # Arrange
        tokens = TOKENS

        # Act
        encoded = flaglz.encode(tokens, len(PLAIN))

        # Assert
        assert encoded == STREAM

    def test_the_stream_decodes_back_to_its_tokens_and_length(self):
        # Arrange
        data = bytes(4) + STREAM + bytes(8)

        # Act
        decoded = flaglz.decode(data, 4)

        # Assert
        assert decoded == (TOKENS, len(STREAM))

    @pytest.mark.parametrize(
        "tokens",
        [
            (Literals(b"ab"), Copy(2, 2)),
            (Literals(b"abc"), Copy(3, 16)),
            (Literals(b"a"), Copy(1, 600)),
            (Literals(PLAIN[:7]), Copy(7, 3)),
        ],
        ids=["two-byte copy", "longest short copy", "copy split in pieces", "seven literals"],
    )
    def test_every_copy_length_round_trips(self, tokens):
        # Arrange
        size = len(expand(tokens))

        # Act
        decoded, _ = flaglz.decode(flaglz.encode(tokens, size), 0)

        # Assert
        assert expand(decoded) == expand(tokens)

    @pytest.mark.parametrize("size", [1, 17, 300, 5000])
    def test_any_data_survives_a_round_trip(self, size):
        # Arrange
        data = bytes(RNG.choice(b"abc\x00") for _ in range(size)) + RNG.randbytes(size)

        # Act
        decoded, _ = flaglz.decode(stream(data), 0)

        # Assert
        assert expand(decoded) == data

    def test_a_repeat_beyond_the_window_is_spelled_out(self):
        # Arrange
        head = bytes(range(1, 9))
        data = head + bytes(flaglz.WINDOW) + head

        # Act
        tokens = flaglz.optimal(data, 0, len(data))

        # Assert
        last = tokens[-1]
        assert (expand(tokens), type(last), last.data[-len(head) :]) == (data, Literals, head)

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            (bytes.fromhex("010000048000050000"), "a copy reaches before the start"),
            (STREAM[:-3], "the stream ends before its end mark"),
            (STREAM[:3] + b"\x28" + STREAM[4:], "the stream does not decode to its size"),
            (STREAM[:3] + b"\x26" + STREAM[4:], "the stream does not decode to its size"),
        ],
        ids=["copy before the start", "cut off", "too short", "too long"],
    )
    def test_a_broken_stream_is_refused(self, data, message):
        # Arrange
        given = data

        # Act
        with pytest.raises(flaglz.FormatError) as caught:
            flaglz.decode(given, 0)

        # Assert
        assert str(caught.value).startswith(message)


class TestCosts:
    @pytest.mark.parametrize(
        ("token", "expected"),
        [(Literals(b"ab"), 18), (Copy(1, 16), 17), (Copy(1, 17), 25), (Copy(1, 545), 67)],
        ids=["two literals", "short copy", "long copy", "copy in three pieces"],
    )
    def test_each_token_costs_its_bits(self, token, expected):
        # Arrange
        given = token

        # Act
        bits = flaglz.cost(given)

        # Assert
        assert bits == expected


class TestParsing:
    def test_a_later_part_can_copy_from_before_it(self):
        # Arrange
        data = PLAIN + PLAIN

        # Act
        tokens = flaglz.optimal(data, len(PLAIN), len(data))

        # Assert
        assert tokens == (Copy(len(PLAIN), len(PLAIN)),)


class TestFinding:
    def test_streams_are_found_where_they_start(self):
        # Arrange
        first, second = stream(entry_block()), stream(PLAIN * 4)
        rom = rom_with(first, second)

        # Act
        found = flaglz.find_streams(rom)

        # Assert
        assert [(s.at, s.packed) for s in found] == [
            (STREAM_AT, len(first)),
            (STREAM_AT + len(first) + (-len(first) % 16), len(second)),
        ]

    @pytest.mark.parametrize(
        "rom",
        [
            bytes(0x1350) + bytes.fromhex("01000040") + bytes(0x80),
            bytes(0x1350) + bytes.fromhex("01ffffff") + bytes(0x40),
            bytes(0x1351) + STREAM + bytes(0x40),
            bytes(0x1350) + STREAM[:-4],
        ],
        ids=["does not decode", "too large", "unaligned", "cut off by the end"],
    )
    def test_anything_else_is_not_a_stream(self, rom):
        # Arrange
        data = rom

        # Act
        found = flaglz.find_streams(data)

        # Assert
        assert found == ()


class TestPatching:
    def test_a_target_inside_a_stream_is_patched_in_place(self):
        # Arrange
        rom = rom_with(stream(RNG.randbytes(0x40)), stream(entry_block()))

        # Act
        packed = flaglz.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        second = flaglz.find_streams(packed.data)[1]
        tokens, _ = flaglz.decode(packed.data, second.at)
        assert (packed.where, len(packed.data), expand(tokens)[0x84:0x88]) == (
            "in 1 LZ stream",
            len(rom),
            bytes.fromhex("0000321e"),
        )

    def test_a_stream_that_no_longer_fits_is_left_alone(self):
        # Arrange
        near = entry_block()
        far = near[:0x90] + bytes(4) + near[0x94:]
        rom = rom_with(stream(b"".join(RNG.randbytes(0x20) + far + near for _ in range(8))))

        # Act
        packed = flaglz.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        assert packed == flaglz.NO_ROOM

    def test_a_rom_without_streams_has_no_targets(self):
        # Arrange
        rom = bytes(0x4000)

        # Act
        packed = flaglz.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        assert packed == NO_TARGETS
