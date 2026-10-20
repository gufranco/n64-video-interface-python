import random
import struct

import pytest

from n64_video_interface import bitlz
from n64_video_interface.bitlz import Copy, Literals
from n64_video_interface.conftest import mode_entry
from n64_video_interface.edits import NO_TARGETS, Settings

HEADER = bytes.fromhex("0300000000")
PLAIN = b"N64 video " * 3 + b"interface"
TOKENS = (Literals(PLAIN[:10]), Copy(10, 20), Literals(PLAIN[30:]))
STREAM = bytes.fromhex("03000000008a4e363420766964656f2004be25a5b9d195c999858d94")
TABLE_AT = 0x1740
RNG = random.Random(64)


def entry_block():
    return RNG.randbytes(0x80) + mode_entry() + RNG.randbytes(0x80)


def segment(data):
    return bitlz.encode(bitlz.optimal(data, 0, len(data)), HEADER)


def rom_with_table(*datas):
    return table_of(tuple((data, segment(data)) for data in datas))


def table_of(pairs):
    records, offset = [], 12 * len(pairs)
    for data, body in pairs:
        records.append(struct.pack(">3I", offset, len(data), 0x80000000 | len(body)))
        offset += len(body)
    table = b"".join(records) + b"".join(body for _, body in pairs)
    rom = bytearray(TABLE_AT + len(table) + 0x100)
    rom[TABLE_AT : TABLE_AT + len(table)] = table
    return bytes(rom)


class TestTheBitstream:
    def test_tokens_encode_to_the_known_stream(self):
        # Arrange
        tokens = TOKENS

        # Act
        stream = bitlz.encode(tokens, HEADER)

        # Assert
        assert stream == STREAM

    def test_the_stream_decodes_back_to_its_tokens_and_bytes(self):
        # Arrange
        data = STREAM

        # Act
        tokens = bitlz.decode(data, len(PLAIN))

        # Assert
        assert (tokens, bitlz.expand(tokens)) == (TOKENS, PLAIN)

    def test_a_stream_without_the_format_byte_is_refused(self):
        # Arrange
        data = b"\x04" + STREAM[1:]

        # Act
        with pytest.raises(bitlz.FormatError) as caught:
            bitlz.decode(data, len(PLAIN))

        # Assert
        assert str(caught.value) == "the segment does not start with the format byte 3"

    def test_a_token_that_would_cross_a_block_is_moved_to_the_next(self):
        # Arrange
        data = RNG.randbytes(0x1100)
        tokens = (Literals(data[i : i + 0x7F]) for i in range(0, len(data), 0x7F))

        # Act
        stream = bitlz.encode(tuple(tokens), HEADER)

        # Assert
        assert (bitlz.expand(bitlz.decode(stream, len(data))), len(stream) > bitlz.BLOCK) == (
            data,
            True,
        )

    def test_a_copy_beyond_the_full_window_round_trips(self):
        # Arrange
        data = RNG.randbytes(bitlz.WINDOW)
        tokens = (*(Literals(data[i : i + 0x7F]) for i in range(0, len(data), 0x7F)), Copy(5, 4))

        # Act
        decoded = bitlz.decode(bitlz.encode(tokens, HEADER), len(data) + 4)

        # Assert
        assert decoded[-1] == Copy(5, 4)

    @pytest.mark.parametrize(
        ("stream", "size", "message"),
        [
            (
                bitlz.encode((Copy(1, 3),), HEADER),
                3,
                "a copy reaches before the start of the segment",
            ),
            (STREAM, len(PLAIN) + 50, "the segment ends before its data"),
        ],
        ids=["copy before the start", "truncated"],
    )
    def test_a_broken_segment_is_refused(self, stream, size, message):
        # Arrange
        data = stream

        # Act
        with pytest.raises(bitlz.FormatError) as caught:
            bitlz.decode(data, size)

        # Assert
        assert str(caught.value) == message

    @pytest.mark.parametrize(
        ("position", "expected"), [(1, 1), (2, 2), (4, 3), (0x7FFF, 15), (0x20000, 16)]
    )
    def test_the_distance_width_follows_the_position(self, position, expected):
        # Arrange
        given = position

        # Act
        width = bitlz.distance_width(given)

        # Assert
        assert width == expected


class TestCosts:
    @pytest.mark.parametrize(
        ("token", "position", "expected"),
        [
            (Literals(b"ab"), 0, 24),
            (Copy(1, 3), 1, 1 + 1 + 1),
            (Copy(4, 255), 4, 1 + 15 + 3),
            (Copy(5, 4), 0x10000, 1 + 3 + 16),
        ],
        ids=["two literals", "shortest copy", "longest copy", "full window"],
    )
    def test_each_token_costs_its_bits(self, token, position, expected):
        # Arrange
        given = (token, position)

        # Act
        bits = bitlz.cost(*given)

        # Assert
        assert bits == expected


class TestParsing:
    def test_a_later_part_can_copy_from_before_it(self):
        # Arrange
        data = PLAIN + PLAIN

        # Act
        tokens = bitlz.optimal(data, len(PLAIN), len(data))

        # Assert
        assert tokens == (Copy(len(PLAIN), len(PLAIN)),)

    @pytest.mark.parametrize(
        ("desired", "expected"),
        [
            (PLAIN[:11] + b"X" + PLAIN[12:], (Literals(PLAIN[:11] + b"X"), Copy(10, 9))),
            (PLAIN[:28] + b"X" + PLAIN[29:], (Literals(PLAIN[:10]), Copy(10, 18))),
        ],
        ids=["short run before", "short run after"],
    )
    def test_runs_too_short_to_copy_become_literals(self, desired, expected):
        # Arrange
        tokens = TOKENS

        # Act
        split = bitlz.split(tokens, desired)

        # Assert
        assert (bitlz.expand(split), split[: len(expected)]) == (desired, expected)


class TestImproving:
    def test_windows_that_did_not_change_are_kept_once_the_stream_fits(self):
        # Arrange
        data = b"".join(RNG.choice((PLAIN, RNG.randbytes(8))) for _ in range(0x900))
        original = bitlz.optimal(data, 0, len(data))
        desired = b"Z" + data[1:]
        edited = bitlz.split(original, desired)

        # Act
        improved = bitlz.improve(edited, desired, data, bitlz._bits(original, 0))

        # Assert
        assert (bitlz.expand(improved), improved[-3:]) == (desired, original[-3:])


class TestPatching:
    def test_a_target_inside_a_segment_is_patched(self):
        # Arrange
        rom = rom_with_table(RNG.randbytes(0x40), entry_block())

        # Act
        packed = bitlz.edit_segments(rom, Settings(antialiasing=False))

        # Assert
        segments = bitlz.find_segments(packed.data)
        data = bitlz.expand(bitlz.decode(packed.data[segments[1].body], segments[1].size))
        assert (packed.where, data[0x84:0x88]) == (
            "in 1 compressed segment",
            bytes.fromhex("0000321e"),
        )

    def test_a_record_whose_size_does_not_hold_does_not_stop_the_search(self):
        # Arrange
        rom = bytearray(rom_with_table(entry_block()))
        decoy = TABLE_AT - 0x100
        rom[decoy : decoy + 12] = struct.pack(">3I", 0x10, 0, 0x80000001)
        rom[decoy + 0x10] = bitlz.FORMAT_BYTE

        # Act
        segments = bitlz.find_segments(bytes(rom))

        # Assert
        assert [segment.at for segment in segments] == [TABLE_AT + 12]

    def test_a_segment_that_does_not_decode_is_skipped(self):
        # Arrange
        rom = bytearray(rom_with_table(entry_block(), entry_block()))
        second = bitlz.find_segments(bytes(rom))[1]
        rom[second.at + 5 : second.body.stop] = bytes(second.packed - 5)

        # Act
        packed = bitlz.edit_segments(bytes(rom), Settings(antialiasing=False))

        # Assert
        assert packed.count == 1

    def test_a_segment_this_encoder_would_not_write_is_left_alone(self):
        # Arrange
        data = entry_block()
        writer = bitlz.Writer(HEADER)
        writer.bits(bitlz.NO_OP, 8)
        position = 0
        for token in bitlz.optimal(data, 0, len(data)):
            writer.token(token, position)
            position += bitlz.span(token)
        rom = table_of(((data, writer.finish()),))

        # Act
        packed = bitlz.edit_segments(rom, Settings(antialiasing=False))

        # Assert
        assert packed == NO_TARGETS

    def test_a_segment_that_no_longer_fits_is_left_alone(self):
        # Arrange
        near = entry_block()
        far = near[:0x90] + bytes(4) + near[0x94:]
        data = b"".join(RNG.randbytes(0x20) + far + near for _ in range(8))
        rom = rom_with_table(data)

        # Act
        packed = bitlz.edit_segments(rom, Settings(antialiasing=False))

        # Assert
        assert packed == bitlz.NO_ROOM

    def test_a_rom_without_a_segment_table_has_no_targets(self):
        # Arrange
        rom = bytes(0x4000)

        # Act
        packed = bitlz.edit_segments(rom, Settings(antialiasing=False))

        # Assert
        assert packed == NO_TARGETS
