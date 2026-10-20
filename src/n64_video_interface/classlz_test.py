import random
import struct

import pytest

from n64_video_interface import classlz
from n64_video_interface.bitlz import Copy, Literals, expand
from n64_video_interface.conftest import mode_entry
from n64_video_interface.edits import NO_TARGETS, Settings

RNG = random.Random(64)
TOKENS = (Literals(b"ab"), Copy(2, 4))
STREAM = bytes.fromhex("1c6162700000")
PROGRAM_AT = 0x1230


def entry_block():
    return RNG.randbytes(0x80) + mode_entry() + RNG.randbytes(0x80)


def segment(data):
    return classlz.encode(classlz.optimal(data, 0, len(data)))


def rom_with(*parts, slack=0, at=PROGRAM_AT):
    bodies = [segment(part) for part in parts]
    sizes = [(len(part), len(body) + slack) for part, body in zip(parts, bodies, strict=True)]
    records = b"".join(struct.pack(">2I", *pair) for pair in sizes)
    packed = b"".join(body + bytes(slack) for body in bodies)
    return bytes(at) + classlz.MAGIC + records + packed + bytes(0x40)


def program(*parts):
    padded = [*parts, *(b"pad!" * 8 for _ in range(classlz.SEGMENTS - len(parts)))]
    return rom_with(*padded)


class TestTheStream:
    def test_tokens_encode_to_the_known_stream(self):
        # Arrange
        tokens = TOKENS

        # Act
        encoded = classlz.encode(tokens)

        # Assert
        assert encoded == STREAM

    def test_the_stream_decodes_back_to_its_tokens_and_length(self):
        # Arrange
        data = bytes(4) + STREAM + bytes(8)

        # Act
        decoded = classlz.decode(data, 4, 6)

        # Assert
        assert decoded == (TOKENS, len(STREAM))

    @pytest.mark.parametrize(
        "tokens",
        [
            (Literals(b"ab"), Copy(2, 2)),
            (Literals(b"abc"), Copy(3, 5)),
            (Literals(b"abc"), Copy(3, 20)),
            (Literals(b"abc"), Copy(3, 21)),
            (Literals(b"a"), Copy(1, 275)),
            (Literals(b"a"), Copy(1, 600)),
            (Literals(bytes(range(256)) * 7), Copy(0x20, 2)),
            (Literals(bytes(range(256)) * 7), Copy(0xA0, 2)),
            (Literals(bytes(range(256)) * 7), Copy(0x2A0, 3)),
            (Literals(bytes(range(256)) * 7), Copy(0x6A0, 9)),
        ],
        ids=[
            "two",
            "longest short",
            "longest middle",
            "shortest long",
            "longest long",
            "copy split in pieces",
            "widest first class",
            "widest second class",
            "widest third class",
            "widest last class",
        ],
    )
    def test_every_length_and_distance_round_trips(self, tokens):
        # Arrange
        size = len(expand(tokens))

        # Act
        decoded, _ = classlz.decode(classlz.encode(tokens), 0, size)

        # Assert
        assert expand(decoded) == expand(tokens)

    @pytest.mark.parametrize("size", [1, 17, 300, 5000])
    def test_any_data_survives_a_round_trip(self, size):
        # Arrange
        data = bytes(RNG.choice(b"abc\x00") for _ in range(size)) + RNG.randbytes(size)

        # Act
        decoded, _ = classlz.decode(segment(data), 0, len(data))

        # Assert
        assert expand(decoded) == data

    def test_a_repeat_beyond_the_window_is_spelled_out(self):
        # Arrange
        head = bytes(range(1, 9))
        data = head + RNG.randbytes(classlz.WINDOW) + head

        # Act
        tokens = classlz.optimal(data, 0, len(data))

        # Assert
        last = tokens[-1]
        assert (expand(tokens), type(last), last.data[-len(head) :]) == (data, Literals, head)

    @pytest.mark.parametrize(
        ("data", "size", "message"),
        [
            (bytes.fromhex("1c61627100000000"), 6, "a copy reaches before the start"),
            (STREAM[:-2], 6, "the segment ends before its end mark"),
            (STREAM, 5, "the segment does not decode to its size"),
            (STREAM, 7, "the segment does not decode to its size"),
        ],
        ids=["copy before the start", "cut off", "too long", "too short"],
    )
    def test_a_broken_segment_is_refused(self, data, size, message):
        # Arrange
        given = data

        # Act
        with pytest.raises(classlz.FormatError) as caught:
            classlz.decode(given, 0, size)

        # Assert
        assert str(caught.value).startswith(message)


class TestCosts:
    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            (Literals(b"ab"), 18),
            (Copy(1, 2), 9),
            (Copy(0x21, 5), 13),
            (Copy(0xA1, 20), 19),
            (Copy(0x2A1, 21), 28),
            (Copy(1, 600), 3 * 23),
        ],
        ids=["two literals", "two", "short", "middle", "long", "three pieces"],
    )
    def test_each_token_costs_its_bits(self, token, expected):
        # Arrange
        given = token

        # Act
        bits = classlz.cost(given)

        # Assert
        assert bits == expected


class TestFinding:
    def test_a_program_is_found_with_its_four_segments(self):
        # Arrange
        parts = [entry_block(), b"two " * 9, b"three" * 7, b"four" * 11]
        rom = rom_with(*parts, slack=3)

        # Act
        found = classlz.find_programs(rom)

        # Assert
        lengths = [len(segment(part)) + 3 for part in parts]
        starts = [PROGRAM_AT + classlz.HEADER + sum(lengths[:i]) for i in range(4)]
        assert found == (
            classlz.Program(
                PROGRAM_AT,
                tuple(
                    classlz.Segment(start, len(part), packed)
                    for start, part, packed in zip(starts, parts, lengths, strict=True)
                ),
            ),
        )

    @pytest.mark.parametrize(
        "rom",
        [
            bytes(PROGRAM_AT + 1) + program(b"one " * 8)[PROGRAM_AT:],
            program(b"one " * 8)[: PROGRAM_AT + 0x10],
            bytes(PROGRAM_AT) + classlz.MAGIC + bytes(0x40),
            bytes(PROGRAM_AT) + classlz.MAGIC + struct.pack(">8I", *[0x40, 0x10] * 4) + bytes(0x80),
            bytes(PROGRAM_AT) + classlz.MAGIC + struct.pack(">8I", *[6, 4] * 4) + STREAM * 4,
        ],
        ids=[
            "unaligned",
            "cut off by the end",
            "empty sizes",
            "does not decode",
            "packed too small",
        ],
    )
    def test_anything_else_is_not_a_program(self, rom):
        # Arrange
        data = rom

        # Act
        found = classlz.find_programs(data)

        # Assert
        assert found == ()


class TestPatching:
    def test_a_target_inside_a_program_is_patched_in_place(self):
        # Arrange
        rom = program(RNG.randbytes(0x40), entry_block())

        # Act
        packed = classlz.edit_programs(rom, Settings(antialiasing=False))

        # Assert
        second = classlz.find_programs(packed.data)[0].segments[1]
        tokens, _ = classlz.decode(packed.data, second.at, second.size)
        assert (packed.where, packed.spans, len(packed.data), expand(tokens)[0x84:0x88]) == (
            "in 1 packed program",
            ((PROGRAM_AT, len(rom) - 0x40),),
            len(rom),
            bytes.fromhex("0000321e"),
        )

    def test_a_segment_that_no_longer_fits_is_left_alone(self):
        # Arrange
        near = entry_block()
        far = near[:0x90] + bytes(4) + near[0x94:]
        rom = program(b"".join(RNG.randbytes(0x20) + far + near for _ in range(8)))

        # Act
        packed = classlz.edit_programs(rom, Settings(antialiasing=False))

        # Assert
        assert packed == classlz.NO_ROOM

    def test_a_program_without_targets_is_left_alone(self):
        # Arrange
        rom = program(b"nothing to see " * 4)

        # Act
        packed = classlz.edit_programs(rom, Settings(antialiasing=False))

        # Assert
        assert packed == NO_TARGETS

    def test_a_rom_without_programs_has_no_targets(self):
        # Arrange
        rom = bytes(0x4000)

        # Act
        packed = classlz.edit_programs(rom, Settings(antialiasing=False))

        # Assert
        assert packed == NO_TARGETS
