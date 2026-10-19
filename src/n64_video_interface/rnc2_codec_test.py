import pytest

from n64_video_interface import rnc2_codec
from n64_video_interface.rnc2_codec import Block, Copy, End, Literal

OPS = (
    *(Literal(byte) for byte in b"N64 "),
    Copy(4, 4),
    Copy(8, 2),
    Copy(1, 3),
    Copy(0x300, 9),
    Block(bytes(range(12))),
    Copy(0x1000, 6),
    End(0),
    Literal(0x41),
    Copy(1, 263),
    End(1),
)
STREAM = bytes.fromhex("024e3634201b03079f000117ff09000102030405060708090a0b7bffe70041beff0000")


def framed(stream):
    return bytes(rnc2_codec.HEADER) + stream


def expanded_size(ops):
    return len(rnc2_codec.expand(ops, bytes(0x1000)))


class TestTheBitstream:
    def test_every_operation_encodes_to_the_known_stream(self):
        # Arrange
        ops = OPS

        # Act
        stream = rnc2_codec.encode(ops)

        # Assert
        assert stream == STREAM

    def test_the_stream_decodes_back_to_its_operations(self):
        # Arrange
        data = framed(STREAM)

        # Act
        ops = rnc2_codec.decode(data, 0, expanded_size(OPS))

        # Assert
        assert ops == OPS

    def test_a_stream_without_a_closing_marker_ends_at_its_last_byte(self):
        # Arrange
        data = framed(rnc2_codec.encode((Literal(0x41),)))

        # Act
        ops = rnc2_codec.decode(data, 0, 1)

        # Assert
        assert ops == (Literal(0x41),)

    @pytest.mark.parametrize(
        ("hi", "width"), [(0, 1), (1, 3), (2, 4), (3, 4), (4, 5), (7, 5), (8, 6), (15, 6)]
    )
    def test_each_distance_high_part_round_trips(self, hi, width):
        # Arrange
        ops = (Copy((hi << 8) + 1, 3), End(1))

        # Act
        decoded = rnc2_codec.decode(framed(rnc2_codec.encode(ops)), 0, 3)

        # Assert
        assert (decoded, rnc2_codec.distance_bits(hi)) == (ops, width)


class TestExpanding:
    def test_operations_expand_against_earlier_output(self):
        # Arrange
        ops = (Literal(0x61), Copy(1, 3), Block(b"wxyz" * 3), Copy(13, 2))

        # Act
        produced = rnc2_codec.expand(ops)

        # Assert
        assert produced == b"aaaa" + b"wxyz" * 3 + b"aw"


class TestCosts:
    @pytest.mark.parametrize(
        ("op", "expected"),
        [
            (Literal(0), 9),
            (Block(bytes(12)), 9 + 96),
            (Copy(5, 2), 11),
            (Copy(5, 3), 13),
            (Copy(5, 5), 13),
            (Copy(5, 8), 14),
            (Copy(5, 9), 21),
            (Copy(0x1000, 9), 26),
            (End(1), 13),
        ],
        ids=["literal", "block", "pair", "three", "five", "eight", "nine", "far nine", "end"],
    )
    def test_each_operation_costs_its_bits(self, op, expected):
        # Arrange
        given = op

        # Act
        bits = rnc2_codec.cost(given)

        # Assert
        assert bits == expected

    @pytest.mark.parametrize(
        ("distance", "length", "expected"),
        [
            (5, 2, True),
            (0x101, 2, False),
            (0x1000, 3, True),
            (0x1001, 3, False),
            (1, 264, False),
            (1, 1, False),
        ],
    )
    def test_only_copies_the_format_can_express_are_allowed(self, distance, length, expected):
        # Arrange
        given = (distance, length)

        # Act
        allowed = rnc2_codec.expressible(*given)

        # Assert
        assert allowed is expected
