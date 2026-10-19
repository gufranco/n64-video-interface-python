import random

from n64_video_interface import rnc2_codec, rnc2_parse
from n64_video_interface.rnc2_codec import Block, Copy, End, Literal

PLAIN = b"N64 video " * 3 + b"interface!!!"
OPS = (*(Literal(byte) for byte in PLAIN[:10]), Copy(10, 20), Block(PLAIN[30:]), End(0), End(1))


class TestSplitting:
    def test_a_copied_byte_that_must_change_becomes_a_literal(self):
        # Arrange
        desired = PLAIN[:12] + b"X" + PLAIN[13:]

        # Act
        ops = rnc2_parse.split(OPS, desired)

        # Assert
        assert (rnc2_codec.expand(ops), ops[10:15]) == (
            desired,
            (Copy(10, 2), Literal(ord("X")), Copy(10, 9), Literal(ord("4")), Copy(10, 7)),
        )

    def test_literals_and_blocks_take_the_new_bytes_in_place(self):
        # Arrange
        desired = b"M" + PLAIN[1:10] + b"M" + PLAIN[11:20] + b"M" + PLAIN[21:31] + b"N" + PLAIN[32:]

        # Act
        ops = rnc2_parse.split(OPS, desired)

        # Assert
        assert (rnc2_codec.expand(ops), len(ops)) == (desired, len(OPS))

    def test_a_run_the_format_cannot_express_turns_into_literals(self):
        # Arrange
        far = random.Random(3).randbytes(0x200)
        ops = (Block(far[:0x48]), *(Literal(byte) for byte in far[0x48:]), Copy(0x200, 4))
        desired = far + far[:1] + b"Z" + far[2:4]

        # Act
        split = rnc2_parse.split(ops, desired)

        # Assert
        assert (rnc2_codec.expand(split), split[-4:]) == (
            desired,
            (Literal(far[0]), Literal(ord("Z")), Literal(far[2]), Literal(far[3])),
        )


class TestOptimalParsing:
    def test_the_parse_expands_back_to_the_data(self):
        # Arrange
        rng = random.Random(64)
        data = bytes(rng.choice(b"ab") for _ in range(0x300)) + rng.randbytes(0x50)

        # Act
        ops = rnc2_parse.optimal(data, 0, len(data))

        # Assert
        assert rnc2_codec.expand(ops) == data

    def test_a_long_unmatched_run_is_sent_as_blocks(self):
        # Arrange
        data = random.Random(5).randbytes(0x40)

        # Act
        ops = rnc2_parse.optimal(data, 0, len(data))

        # Assert
        assert any(isinstance(op, Block) for op in ops)

    def test_a_later_part_can_copy_from_before_it(self):
        # Arrange
        data = PLAIN + PLAIN

        # Act
        ops = rnc2_parse.optimal(data, len(PLAIN), len(data))

        # Assert
        assert ops == (Copy(len(PLAIN), len(PLAIN)),)

    def test_copies_stay_within_the_reach_of_the_format(self):
        # Arrange
        rng = random.Random(9)
        block = rng.randbytes(0x40)
        data = block + rng.randbytes(rnc2_codec.FAR_REACH) + block

        # Act
        ops = rnc2_parse.optimal(data, len(data) - 0x40, len(data))

        # Assert
        assert all(op.distance <= rnc2_codec.FAR_REACH for op in ops if isinstance(op, Copy))


class TestImproving:
    def test_only_segments_that_changed_are_parsed_again(self):
        # Arrange
        original = (*OPS[:-1], *OPS[:-2], End(1))
        desired = PLAIN + PLAIN[:12] + b"X" + PLAIN[13:]
        edited = rnc2_parse.split(original, desired)

        # Act
        improved = rnc2_parse.improve(edited, original, desired)

        # Assert
        first = len(OPS) - 1
        assert (improved[:first], rnc2_codec.expand(improved)) == (original[:first], desired)

    def test_the_cheaper_parse_of_a_segment_is_kept(self):
        # Arrange
        desired = PLAIN[:12] + b"X" + PLAIN[13:]
        edited = rnc2_parse.split(OPS, desired)

        # Act
        improved = rnc2_parse.improve(edited, OPS, desired)

        # Assert
        assert sum(map(rnc2_codec.cost, improved)) <= sum(map(rnc2_codec.cost, edited))
