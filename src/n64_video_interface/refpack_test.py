import random

import pytest

from n64_video_interface import refpack
from n64_video_interface.conftest import mode_entry
from n64_video_interface.edits import NO_TARGETS, Settings
from n64_video_interface.refpack import Copy, Literals

PLAIN = b"N64 video " * 3 + b"interface"
TOKENS = (Literals(PLAIN[:10]), Copy(10, 20), Literals(PLAIN[30:]))
STREAM = bytes.fromhex("10fb000027e14e363420766964659080096f20e1696e746572666163fd65")
RNG = random.Random(64)


def entry_block():
    return RNG.randbytes(0x80) + mode_entry() + RNG.randbytes(0x80)


def rom_with(*streams):
    return bytes(0x40) + b"".join(streams) + bytes(0x40)


def packed(data):
    return refpack.encode(refpack.optimal(data, 0, len(data)), len(data))


class TestTheStream:
    def test_tokens_encode_to_the_known_stream(self):
        # Arrange
        tokens = TOKENS

        # Act
        stream = refpack.encode(tokens, len(PLAIN))

        # Assert
        assert stream == STREAM

    def test_the_stream_decodes_back_to_its_tokens(self):
        # Arrange
        data = STREAM

        # Act
        stream = refpack.decode(data, 0)

        # Assert
        assert (stream.tokens, refpack.expand(stream.tokens), stream.length) == (
            TOKENS,
            PLAIN,
            len(STREAM),
        )

    @pytest.mark.parametrize(
        ("distance", "length", "expected"),
        [(1024, 10, 2), (1025, 10, 3), (16384, 67, 3), (16385, 5, 4), (5, 1028, 4)],
        ids=["short", "medium by reach", "medium", "long by reach", "long by length"],
    )
    def test_each_copy_takes_the_smallest_form_that_reaches(self, distance, length, expected):
        # Arrange
        copy = Copy(distance, length)

        # Act
        size = refpack.copy_size(copy)

        # Assert
        assert size == expected

    @pytest.mark.parametrize("count", [0, 3, 4, 7, 112, 113, 230])
    def test_any_run_of_literals_round_trips(self, count):
        # Arrange
        data = RNG.randbytes(count) + b"abcabcabc"

        # Act
        stream = refpack.decode(packed(data), 0)

        # Assert
        assert refpack.expand(stream.tokens) == data

    @pytest.mark.parametrize(
        "stream",
        [
            b"\x10\xfa\x00\x00\x04",
            b"\x10\xfb\x00\x00\x00\xfc",
            b"\x10\xfb\x00\x00\x04\x00\x05\xfc",
            b"\x10\xfb\x00\x00\x04\xfd\x61",
        ],
        ids=["no magic", "empty", "copy before the start", "short of its size"],
    )
    def test_a_stream_that_does_not_hold_up_is_not_one(self, stream):
        # Arrange
        data = stream + bytes(8)

        # Act
        found = refpack.decode(data, 0)

        # Assert
        assert found is None

    def test_a_stream_cut_off_before_its_end_code_is_not_one(self):
        # Arrange
        data = b"\x10\xfb\x00\x00\x08\xe0abcd"

        # Act
        found = refpack.decode(data, 0)

        # Assert
        assert found is None


class TestParsing:
    def test_a_later_part_can_copy_from_before_it(self):
        # Arrange
        data = PLAIN + PLAIN

        # Act
        tokens = refpack.optimal(data, len(PLAIN), len(data))

        # Assert
        assert tokens == (Copy(len(PLAIN), len(PLAIN)),)

    def test_a_copied_byte_that_must_change_becomes_a_literal(self):
        # Arrange
        desired = PLAIN[:12] + b"X" + PLAIN[13:]

        # Act
        tokens = refpack.split(TOKENS, desired)

        # Assert
        assert (refpack.expand(tokens), tokens[:2]) == (
            desired,
            (Literals(PLAIN[:12] + b"X"), Copy(10, 9)),
        )


class TestImproving:
    def test_windows_that_did_not_change_are_kept_once_the_stream_fits(self):
        # Arrange
        data = b"".join(RNG.choice((PLAIN, RNG.randbytes(8))) for _ in range(0x900))
        original = refpack.optimal(data, 0, len(data))
        desired = b"Z" + data[1:]
        edited = refpack.split(original, desired)

        # Act
        improved = refpack.improve(edited, desired, data, sum(map(refpack.cost, original)) + 16)

        # Assert
        assert (refpack.expand(improved), improved[-3:]) == (desired, original[-3:])


class TestPatching:
    def test_a_target_inside_a_stream_is_patched(self):
        # Arrange
        rom = rom_with(packed(RNG.randbytes(0x40)), packed(entry_block()))

        # Act
        result = refpack.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        streams = refpack.find_streams(result.data)
        data = refpack.expand(streams[1].tokens)
        assert (result.where, data[0x84:0x88]) == ("in 1 RefPack stream", bytes.fromhex("0000321e"))

    def test_a_stream_that_no_longer_fits_is_left_alone(self):
        # Arrange
        near = entry_block()
        far = near[:0x90] + bytes(4) + near[0x94:]
        rom = rom_with(packed(b"".join(RNG.randbytes(0x20) + far + near for _ in range(8))))

        # Act
        result = refpack.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        assert result == refpack.NO_ROOM

    def test_a_rom_without_streams_has_no_targets(self):
        # Arrange
        rom = bytes(0x400)

        # Act
        result = refpack.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        assert result == NO_TARGETS
