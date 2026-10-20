import random
import struct

import pytest

from n64_video_interface import bitlz, bytelz
from n64_video_interface.bitlz import Copy, Literals
from n64_video_interface.conftest import mode_entry
from n64_video_interface.edits import NO_TARGETS, Settings

HEADER = bytes.fromhex("0100001000")
BITLZ_HEADER = bytes.fromhex("0300000000")
PLAIN = b"N64 video " * 3 + b"interface"
TOKENS = (Literals(PLAIN[:10]), Copy(10, 20), Literals(PLAIN[30:]))
STREAM = HEADER + b"\x8aN64 video \x14\x0a\x00\x89interface\x00"
TABLE_AT = 0x1740
RNG = random.Random(64)


def entry_block():
    return RNG.randbytes(0x80) + mode_entry() + RNG.randbytes(0x80)


def segment(data):
    return bytelz.encode(bytelz.optimal(data, 0, len(data)), HEADER)


def table_of(pairs):
    records, offset = [], 12 * len(pairs)
    for data, body in pairs:
        records.append(struct.pack(">3I", offset, len(data), 0x80000000 | len(body)))
        offset += len(body)
    table = b"".join(records) + b"".join(body for _, body in pairs)
    rom = bytearray(TABLE_AT + len(table) + 0x100)
    rom[TABLE_AT : TABLE_AT + len(table)] = table
    return bytes(rom)


def rom_with_table(*datas):
    return table_of(tuple((data, segment(data)) for data in datas))


class TestTheStream:
    def test_tokens_encode_to_the_known_stream(self):
        # Arrange
        tokens = TOKENS

        # Act
        stream = bytelz.encode(tokens, HEADER)

        # Assert
        assert stream == STREAM

    def test_the_stream_decodes_back_to_its_tokens_and_end(self):
        # Arrange
        data = STREAM + bytes(8)

        # Act
        decoded = bytelz.decode(data, len(PLAIN))

        # Assert
        assert decoded == (TOKENS, len(STREAM))

    @pytest.mark.parametrize("size", [1, 17, 300, 5000])
    def test_any_data_survives_a_round_trip(self, size):
        # Arrange
        data = bytes(RNG.choice(b"abc\x00") for _ in range(size)) + RNG.randbytes(size)

        # Act
        tokens, _ = bytelz.decode(segment(data), len(data))

        # Assert
        assert bitlz.expand(tokens) == data

    def test_a_repeat_beyond_the_window_is_spelled_out(self):
        # Arrange
        head = bytes(range(1, 9))
        data = head + bytes(bytelz.WINDOW) + head

        # Act
        tokens = bytelz.optimal(data, 0, len(data))

        # Assert
        last = tokens[-1]
        assert (bitlz.expand(tokens), type(last), last.data[-len(head) :]) == (data, Literals, head)

    @pytest.mark.parametrize(
        ("stream", "size", "message"),
        [
            (bytelz.encode((Copy(1, 3),), HEADER), 3, "a copy reaches before the start"),
            (STREAM[:12], len(PLAIN), "the stream ends before its end mark"),
            (STREAM[:-1], len(PLAIN), "the stream ends before its end mark"),
            (STREAM, len(PLAIN) + 1, "the stream does not decode to its size"),
            (STREAM, len(PLAIN) - 9, "the stream does not decode to its size"),
        ],
        ids=["copy before the start", "cut literal", "no end mark", "short", "long"],
    )
    def test_a_broken_stream_is_refused(self, stream, size, message):
        # Arrange
        data = stream

        # Act
        with pytest.raises(bytelz.FormatError) as caught:
            bytelz.decode(data, size)

        # Assert
        assert str(caught.value).startswith(message)


class TestCosts:
    @pytest.mark.parametrize(
        ("token", "expected"),
        [(Literals(b"ab"), 3), (Copy(1, 3), 3), (Copy(0xFFFF, 0x7F), 3)],
        ids=["two literals", "shortest copy", "longest copy"],
    )
    def test_each_token_costs_its_bytes(self, token, expected):
        # Arrange
        given = token

        # Act
        size = bytelz.cost(given)

        # Assert
        assert size == expected


class TestParsing:
    def test_a_later_part_can_copy_from_before_it(self):
        # Arrange
        data = PLAIN + PLAIN

        # Act
        tokens = bytelz.optimal(data, len(PLAIN), len(data))

        # Assert
        assert tokens == (Copy(len(PLAIN), len(PLAIN)),)

    def test_a_long_repeat_costs_one_literal_and_three_copies(self):
        # Arrange
        data = b"\x00" * 300

        # Act
        tokens = bytelz.optimal(data, 0, len(data))

        # Assert
        assert (bitlz.expand(tokens), sum(map(bytelz.cost, tokens))) == (data, 2 + 3 * 3)


class TestImproving:
    def test_windows_that_did_not_change_are_kept_once_the_stream_fits(self):
        # Arrange
        data = b"".join(RNG.choice((PLAIN, RNG.randbytes(8))) for _ in range(0x900))
        original = bytelz.optimal(data, 0, len(data))
        desired = b"Z" + data[1:]
        edited = bitlz.split(original, desired)
        budget = sum(map(bytelz.cost, original)) + 64

        # Act
        improved = bytelz.improve(edited, desired, data, budget)

        # Assert
        assert (bitlz.expand(improved), improved[-3:]) == (desired, original[-3:])


class TestPatching:
    def test_a_target_inside_a_segment_is_patched(self):
        # Arrange
        rom = rom_with_table(RNG.randbytes(0x40), entry_block())

        # Act
        packed = bytelz.edit_segments(rom, Settings(antialiasing=False))

        # Assert
        second = bytelz.find_segments(packed.data)[1]
        tokens, _ = bytelz.decode(packed.data[second.body], second.size)
        assert (packed.where, bitlz.expand(tokens)[0x84:0x88]) == (
            "in 1 byte-coded segment",
            bytes.fromhex("0000321e"),
        )

    def test_each_format_finds_only_its_own_segments_in_a_shared_table(self):
        # Arrange
        first, second = entry_block(), entry_block()
        bits = bitlz.encode(bitlz.optimal(first, 0, len(first)), BITLZ_HEADER)
        rom = table_of(((first, bits), (second, segment(second))))

        # Act
        found = (bitlz.find_segments(rom), bytelz.find_segments(rom))

        # Assert
        assert ([s.at for s in found[0]], [s.at for s in found[1]]) == (
            [TABLE_AT + 24],
            [TABLE_AT + 24 + len(bits)],
        )

    def test_a_segment_that_does_not_decode_is_skipped(self):
        # Arrange
        rom = bytearray(rom_with_table(entry_block(), entry_block()))
        second = bytelz.find_segments(bytes(rom))[1]
        rom[second.at + bytelz.HEADER : second.body.stop] = b"\x7f" * (
            second.packed - bytelz.HEADER
        )

        # Act
        packed = bytelz.edit_segments(bytes(rom), Settings(antialiasing=False))

        # Assert
        assert packed.count == 1

    def test_a_segment_that_no_longer_fits_is_left_alone(self):
        # Arrange
        near = entry_block()
        far = near[:0x90] + bytes(4) + near[0x94:]
        data = b"".join(RNG.randbytes(0x20) + far + near for _ in range(8))
        rom = rom_with_table(data)

        # Act
        packed = bytelz.edit_segments(rom, Settings(antialiasing=False))

        # Assert
        assert packed == bytelz.NO_ROOM

    def test_a_rom_without_a_segment_table_has_no_targets(self):
        # Arrange
        rom = bytes(0x4000)

        # Act
        packed = bytelz.edit_segments(rom, Settings(antialiasing=False))

        # Assert
        assert packed == NO_TARGETS
