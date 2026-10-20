import random

from n64_video_interface import bitlz, bytelz, rewindow

RNG = random.Random(64)
PLAIN = b"N64 video " * 3 + b"interface"


class TestImproving:
    def test_windows_that_did_not_change_are_kept_once_the_stream_fits(self):
        # Arrange
        data = b"".join(RNG.choice((PLAIN, RNG.randbytes(8))) for _ in range(0x900))
        original = bytelz.optimal(data, 0, len(data))
        desired = b"Z" + data[1:]
        edited = bitlz.split(original, desired)
        budget = sum(map(bytelz.cost, original)) + 64

        # Act
        improved = rewindow.improve(
            edited, desired, data, budget, optimal=bytelz.optimal, cost=bytelz.cost
        )

        # Assert
        assert (bitlz.expand(improved), improved[-3:]) == (desired, original[-3:])

    def test_every_window_is_reparsed_while_the_stream_is_over_budget(self):
        # Arrange
        data = PLAIN * 0x400
        literal = bitlz.split((bitlz.Literals(data),), data)

        # Act
        improved = rewindow.improve(
            literal, data, data, 0, optimal=bytelz.optimal, cost=bytelz.cost
        )

        # Assert
        assert (bitlz.expand(improved), sum(map(bytelz.cost, improved)) < len(data)) == (
            data,
            True,
        )
