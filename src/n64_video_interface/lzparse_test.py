import pytest

from n64_video_interface import lzparse
from n64_video_interface.bitlz import Copy, Literals, expand

SHAPE = lzparse.Shape(
    window=0x10,
    min_copy=3,
    max_copy=5,
    candidates=8,
    literal_bits=9,
    copy_bits=lambda length: 17,
)


class TestTheParse:
    def test_a_repeat_inside_the_window_costs_one_copy_and_seven_literals(self):
        # Arrange
        data = b"abcdef" + b"abcdef"

        # Act
        tokens = lzparse.optimal(data, 0, len(data), SHAPE)

        # Assert
        bits = sum(17 if isinstance(t, Copy) else 9 * len(t.data) for t in tokens)
        assert (expand(tokens), bits) == (data, 7 * 9 + 17)

    def test_a_repeat_beyond_the_window_is_spelled_out(self):
        # Arrange
        data = b"abcdef" + bytes(range(0x40, 0x60)) + b"abcdef"

        # Act
        tokens = lzparse.optimal(data, 0, len(data), SHAPE)

        # Assert
        assert tokens == (Literals(data),)

    def test_a_later_part_can_copy_from_before_it(self):
        # Arrange
        data = b"abcde" * 2

        # Act
        tokens = lzparse.optimal(data, 5, len(data), SHAPE)

        # Assert
        assert tokens == (Copy(5, 5),)

    @pytest.mark.parametrize("data", [b"", b"a", b"aaaaaaaaaaaaaaaaaaaa"])
    def test_any_data_parses_back_to_itself(self, data):
        # Arrange
        given = data

        # Act
        tokens = lzparse.optimal(given, 0, len(given), SHAPE)

        # Assert
        assert expand(tokens) == given
