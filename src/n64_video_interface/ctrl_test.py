import pytest

from n64_video_interface.ctrl import ViCtrl, apply_changes, plausible


class TestDecoding:
    def test_every_blur_source_is_read_from_its_bit(self):
        # Arrange
        value = 0x0001311E

        # Act
        decoded = ViCtrl(value)

        # Assert
        assert (
            decoded.antialiasing,
            decoded.divot,
            decoded.gamma_dither,
            decoded.gamma,
            decoded.dither_filter,
        ) == (True, True, True, True, True)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(0x0000301E, True), (0x0000311E, True), (0x0000321E, False), (0x0000331E, False)],
    )
    def test_anti_aliasing_is_on_only_for_the_two_aa_modes(self, value, expected):
        # Arrange
        decoded = ViCtrl(value)

        # Act
        antialiasing = decoded.antialiasing

        # Assert
        assert antialiasing is expected

    def test_a_clean_value_has_no_blur_source(self):
        # Arrange
        decoded = ViCtrl(0x00003202)

        # Act
        sources = (decoded.divot, decoded.gamma_dither, decoded.gamma, decoded.dither_filter)

        # Assert
        assert sources == (False, False, False, False)


class TestPlausibility:
    @pytest.mark.parametrize("value", [0x0000311E, 0x0000301F, 0x0001320E])
    def test_a_16_or_32_bit_mode_without_test_bits_is_plausible(self, value):
        # Arrange
        candidate = value

        # Act
        accepted = plausible(candidate)

        # Assert
        assert accepted is True

    @pytest.mark.parametrize(
        "value", [0x00020000, 0x0000311C, 0x0000311D, 0x0000313E, 0x0000319E, 0x0000391E]
    )
    def test_high_bits_blank_types_and_test_bits_are_rejected(self, value):
        # Arrange
        candidate = value

        # Act
        accepted = plausible(candidate)

        # Assert
        assert accepted is False


class TestEditing:
    @pytest.mark.parametrize(
        ("settings", "expected"),
        [
            ({"dither_filter": False}, 0x0000311E),
            ({"divot": False}, 0x0001310E),
            ({"gamma_dither": False}, 0x0001311A),
            ({"gamma": False}, 0x00013116),
        ],
    )
    def test_each_setting_clears_only_its_own_bit(self, settings, expected):
        # Arrange
        value = 0x0001311E

        # Act
        out = apply_changes(value, **settings)

        # Assert
        assert out == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(0x0000311E, 0x0000321E), (0x0000301F, 0x0000331F), (0x0000305F, 0x0000335F)],
    )
    def test_anti_aliasing_off_matches_the_library_point_sampled_modes(self, value, expected):
        # Arrange
        ctrl = value

        # Act
        out = apply_changes(ctrl, antialiasing=False)

        # Assert
        assert out == expected

    def test_a_setting_can_be_turned_back_on(self):
        # Arrange
        value = 0x00003202

        # Act
        out = apply_changes(value, divot=True, antialiasing=True)

        # Assert
        assert out == 0x00003112

    def test_no_setting_changes_nothing(self):
        # Arrange
        value = 0x0000311E

        # Act
        out = apply_changes(value)

        # Assert
        assert out == value
