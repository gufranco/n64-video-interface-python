import struct

import pytest

from n64_video_interface import viconst
from n64_video_interface.edits import Settings

LUI_V0_VI = 0x3C02A440
LUI_V0_OTHER = 0x3C02A450
ADDIU_T1_LAN1 = 0x2409311E
ORI_T1_LAN1 = 0x3409311E
SW_T1_V0 = 0xAC490000
JR_RA = 0x03E00008
LUI_T1_ONE = 0x3C090001
ORI_T1_T1 = 0x35293016
LW_T1 = 0x8C490004
NOP = 0x00000000
BRANCH = 0x10000003
OFF = Settings(antialiasing=False, divot=False, gamma_dither=False, dither_filter=False)


def code(*words, at=0x40):
    data = bytearray(0x400)
    struct.pack_into(f">{len(words)}I", data, at, *words)
    return bytes(data)


class TestFinding:
    @pytest.mark.parametrize(
        ("words", "expected"),
        [
            ((LUI_V0_VI, ADDIU_T1_LAN1, SW_T1_V0), ((0x44, ADDIU_T1_LAN1, 0x2409320A),)),
            ((LUI_V0_VI, ORI_T1_LAN1, SW_T1_V0), ((0x44, ORI_T1_LAN1, 0x3409320A),)),
            ((LUI_V0_VI, ADDIU_T1_LAN1, JR_RA, SW_T1_V0), ((0x44, ADDIU_T1_LAN1, 0x2409320A),)),
            (
                (LUI_V0_VI, LUI_T1_ONE, ORI_T1_T1, SW_T1_V0),
                ((0x44, LUI_T1_ONE, 0x3C090000), (0x48, ORI_T1_T1, 0x35293202)),
            ),
            (
                (LUI_T1_ONE, 0x35290016, 0xAC890004),
                ((0x40, LUI_T1_ONE, 0x3C090000), (0x44, 0x35290016, 0x35290202)),
            ),
        ],
        ids=["addiu", "ori", "store in a delay slot", "lui and ori", "mode built in memory"],
    )
    def test_a_constant_stored_to_the_control_register_is_rewritten(self, words, expected):
        # Arrange
        data = code(*words)

        # Act
        changes = viconst.find_constants(data, OFF)

        # Assert
        assert changes == expected

    @pytest.mark.parametrize(
        "words",
        [
            (LUI_V0_OTHER, ADDIU_T1_LAN1, SW_T1_V0),
            (LUI_V0_VI, 0x24091234, SW_T1_V0),
            (LUI_V0_VI, LW_T1, SW_T1_V0),
            (LUI_V0_VI, BRANCH, *([NOP] * 20), ADDIU_T1_LAN1, SW_T1_V0),
            (LUI_V0_VI, ADDIU_T1_LAN1, 0xAC490004),
            (LUI_V0_VI, 0x3409320A, SW_T1_V0),
            (LUI_V0_VI, SW_T1_V0),
            (0x3C090000, 0x35293202, 0xAC890004),
        ],
        ids=[
            "another register block",
            "not a control value",
            "loaded value",
            "branch between the base and the store",
            "another register",
            "already off",
            "value set elsewhere",
            "mode without built-in features",
        ],
    )
    def test_anything_else_is_left_alone(self, words):
        # Arrange
        data = code(*words)

        # Act
        changes = viconst.find_constants(data, OFF)

        # Assert
        assert changes == ()

    def test_a_bit_beyond_one_immediate_is_not_forced_on(self):
        # Arrange
        data = code(LUI_V0_VI, ADDIU_T1_LAN1, SW_T1_V0)

        # Act
        changes = viconst.find_constants(data, Settings(dither_filter=True))

        # Assert
        assert changes == ()

    def test_a_setting_left_alone_keeps_its_bit(self):
        # Arrange
        data = code(LUI_V0_VI, ADDIU_T1_LAN1, SW_T1_V0)

        # Act
        changes = viconst.find_constants(data, Settings(divot=False))

        # Assert
        assert changes == ((0x44, ADDIU_T1_LAN1, 0x2409310E),)
