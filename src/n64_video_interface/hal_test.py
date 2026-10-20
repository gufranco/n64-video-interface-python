import struct

import pytest

from n64_video_interface import hal

LOAD = 0x8C4E0004
STORE_GAMMA_DITHER = 0xAC4F0004
GAMMA_DITHER_ON = 0x35CF0004
DIVOT_ON = 0x35CF0010
LUI_ONE = 0x3C010001
OR_DEDITHER = 0x01C17825
AA_TEST = (0x31CF0001, 0x11E00003, 0x24040100, 0x10000001, 0x00002025)


def words(*values):
    return b"".join(struct.pack(">I", value) for value in values)


def code(*values, at=0x100):
    data = bytearray(0x400)
    data[at : at + 4 * len(values)] = words(*values)
    return bytes(data)


def word(data, at):
    return struct.unpack_from(">I", data, at)[0]


class TestSetters:
    @pytest.mark.parametrize(
        ("setter", "bit"),
        [(GAMMA_DITHER_ON, 0x0004), (DIVOT_ON, 0x0010)],
        ids=["gamma dither", "divot"],
    )
    def test_a_bit_set_in_a_read_modify_write_of_the_control_word_is_found(self, setter, bit):
        # Arrange
        data = code(LOAD, setter, STORE_GAMMA_DITHER)

        # Act
        found = hal.find_setters(data, range(0, 0x400), {bit})

        # Assert
        assert found == ((0x104, setter, setter & 0xFFFF0000),)

    def test_the_dedither_bit_is_found_through_its_lui(self):
        # Arrange
        data = code(LOAD, LUI_ONE, OR_DEDITHER, STORE_GAMMA_DITHER)

        # Act
        found = hal.find_setters(data, range(0, 0x400), {hal.DEDITHER})

        # Assert
        assert found == ((0x104, LUI_ONE, 0x3C010000),)

    @pytest.mark.parametrize(
        "values",
        [
            (LOAD, GAMMA_DITHER_ON, 0xAC4F0008),
            (0x8C4E0008, GAMMA_DITHER_ON, STORE_GAMMA_DITHER),
            (
                LOAD,
                GAMMA_DITHER_ON,
                0x00000000,
                0x00000000,
                0x00000000,
                0x00000000,
                0x00000000,
                STORE_GAMMA_DITHER,
            ),
            (LOAD, 0x35CF0040, STORE_GAMMA_DITHER),
        ],
        ids=["other word stored", "other word loaded", "store too far", "bit not asked for"],
    )
    def test_anything_but_that_shape_is_left_alone(self, values):
        # Arrange
        data = code(*values)

        # Act
        found = hal.find_setters(data, range(0, 0x400), {0x0004})

        # Assert
        assert found == ()

    def test_a_setter_outside_the_window_is_ignored(self):
        # Arrange
        data = code(LOAD, GAMMA_DITHER_ON, STORE_GAMMA_DITHER)

        # Act
        found = hal.find_setters(data, range(0x200, 0x400), {0x0004})

        # Assert
        assert found == ()

    @pytest.mark.parametrize(
        "values",
        [
            (LOAD, LUI_ONE, 0x00000000, 0x00000000, STORE_GAMMA_DITHER),
            (LUI_ONE, OR_DEDITHER, 0xAC4F0008),
        ],
        ids=["no or", "not written back"],
    )
    def test_a_lui_of_one_that_does_not_reach_the_control_word_is_left_alone(self, values):
        # Arrange
        data = code(*values)

        # Act
        found = hal.find_setters(data, range(0, 0x400), {hal.DEDITHER})

        # Assert
        assert found == ()


class TestTheAntiAliasingRebuild:
    def test_both_choices_become_resampling(self):
        # Arrange
        data = code(*AA_TEST)

        # Act
        found = hal.find_aa_rebuild(data, range(0, 0x400))

        # Assert
        assert found == ((0x108, 0x24040100, 0x24040200), (0x110, 0x00002025, 0x24040200))

    def test_a_different_shape_is_not_rewritten(self):
        # Arrange
        data = code(*AA_TEST[:4], 0x00000000)

        # Act
        found = hal.find_aa_rebuild(data, range(0, 0x400))

        # Assert
        assert found == ()
