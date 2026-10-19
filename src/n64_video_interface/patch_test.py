import struct

import pytest

from n64_video_interface import checksum
from n64_video_interface.byteorder import ByteOrder, swap
from n64_video_interface.conftest import SEALABLE_SIZE, make_rom, mode_entry, sealable_rom
from n64_video_interface.patch import (
    ALREADY_OFF,
    CHECKSUM_UNKNOWN,
    DEDITHER_ON,
    NO_CHANGE_NEEDED,
    NO_MICROCODE,
    NO_TABLE,
    NOTHING_REQUESTED,
    RDP_DITHER_ON,
    safe_patch,
)
from n64_video_interface.rarezip import NO_CHECKSUM
from n64_video_interface.rarezip_test import rom_with_group
from n64_video_interface.rdp_test import TEXT_BASE, microcode_rom
from n64_video_interface.seal import reseal

TABLE_AT = 0x2000
CTRL_AT = TABLE_AT + 4
ROUTINE_AT = 0x3000


def word(data, at):
    return struct.unpack_from(">I", data, at)[0]


def with_microcode(rom):
    data = bytearray(rom)
    blob = microcode_rom()
    data[TEXT_BASE : TEXT_BASE + 0x2000] = blob[TEXT_BASE:]
    return reseal(bytes(data), "6102")


class TestRefusals:
    @pytest.mark.parametrize(
        ("settings", "reason"),
        [
            ({}, NOTHING_REQUESTED),
            ({"dither_filter": True}, DEDITHER_ON),
            ({"rdp_color_dither": True}, RDP_DITHER_ON),
            ({"rdp_alpha_dither": True}, RDP_DITHER_ON),
        ],
    )
    def test_a_request_this_cannot_honour_is_refused_before_reading(self, settings, reason):
        # Arrange
        rom, _ = sealable_rom()

        # Act
        result = safe_patch(rom, **settings)

        # Assert
        assert (result.applied, result.reason) == (False, reason)

    @pytest.mark.parametrize(
        ("rom", "settings", "reason"),
        [
            (sealable_rom(table_at=None)[0], {"antialiasing": False}, NO_TABLE),
            (sealable_rom()[0], {"dither_filter": False}, ALREADY_OFF),
            (sealable_rom()[0], {"rdp_color_dither": False}, NO_MICROCODE),
            (
                sealable_rom(entry=mode_entry(ctrl=0x00003202))[0],
                {"antialiasing": False},
                NO_CHANGE_NEEDED,
            ),
        ],
        ids=["no table", "no dedither request", "no microcode", "already as asked"],
    )
    def test_a_rom_that_does_not_need_or_allow_the_edit_is_refused(self, rom, settings, reason):
        # Arrange
        data = rom

        # Act
        result = safe_patch(data, **settings)

        # Assert
        assert (result.applied, result.reason, result.data) == (False, reason, b"")

    def test_a_rom_whose_checksum_does_not_validate_is_refused(self):
        # Arrange
        rom = bytearray(sealable_rom()[0])
        rom[0x10] ^= 0xFF

        # Act
        result = safe_patch(bytes(rom), antialiasing=False)

        # Assert
        assert (result.applied, result.reason) == (False, CHECKSUM_UNKNOWN)


class TestTheTable:
    def test_anti_aliasing_off_rewrites_the_ctrl_word_and_reseals(self):
        # Arrange
        rom, _ = sealable_rom()

        # Act
        result = safe_patch(rom, antialiasing=False)

        # Assert
        assert (
            result.applied,
            result.reason,
            word(result.data, CTRL_AT),
            checksum.verify(result.data),
            result.cic,
        ) == (True, "1 of 1 mode entries changed", 0x0000321E, (True, "6102"), "6102")

    def test_nothing_but_the_ctrl_word_and_the_checksum_moves(self):
        # Arrange
        rom, _ = sealable_rom()

        # Act
        result = safe_patch(rom, antialiasing=False, divot=False)

        # Assert
        moved = {i for i in range(len(rom)) if rom[i] != result.data[i]}
        assert moved <= set(range(0x10, 0x18)) | set(range(CTRL_AT, CTRL_AT + 4))

    def test_every_change_is_reported_with_its_old_and_new_word(self):
        # Arrange
        rom, _ = sealable_rom()

        # Act
        result = safe_patch(rom, antialiasing=False, divot=False)

        # Assert
        assert (result.changes, result.modes_changed) == (((CTRL_AT, 0x0000311E, 0x0000320E),), 1)

    def test_patching_twice_finds_nothing_left_to_do(self):
        # Arrange
        once = safe_patch(sealable_rom()[0], antialiasing=False).data

        # Act
        twice = safe_patch(once, antialiasing=False)

        # Assert
        assert (twice.applied, twice.reason) == (False, NO_CHANGE_NEEDED)


class TestRuntimeSwitches:
    @pytest.mark.parametrize(
        ("setting", "switch"),
        [("divot", "divot"), ("gamma_dither", "gamma dither"), ("gamma", "gamma")],
    )
    def test_turning_a_setting_off_also_stops_the_game_turning_it_on(self, setting, switch):
        # Arrange
        rom, guards = sealable_rom(
            routine_at=ROUTINE_AT, names=("dedither", "divot", "gamma dither", "gamma")
        )

        # Act
        result = safe_patch(rom, **{setting: False})

        # Assert
        assert (word(result.data, guards[switch]) & 0xFFFF, result.reason) == (
            0,
            f"1 of 1 mode entries changed, 1 {switch} request neutralised",
        )

    def test_the_dedither_request_is_neutralised_without_a_table(self):
        # Arrange
        rom, guards = sealable_rom(table_at=None, routine_at=ROUTINE_AT)

        # Act
        result = safe_patch(rom, dither_filter=False)

        # Assert
        assert (result.applied, result.reason, word(result.data, guards["dedither"]) & 0xFFFF) == (
            True,
            "1 dedither request neutralised",
            0,
        )

    def test_table_settings_without_a_table_still_neutralise_the_switches(self):
        # Arrange
        rom, _ = sealable_rom(table_at=None, routine_at=ROUTINE_AT, names=("dedither", "divot"))

        # Act
        result = safe_patch(rom, antialiasing=False, divot=False, dither_filter=False)

        # Assert
        assert (result.applied, result.reason) == (
            True,
            f"{NO_TABLE}, so anti-aliasing and the table settings are unchanged, "
            "1 dedither request neutralised, 1 divot request neutralised",
        )

    def test_anti_aliasing_alone_leaves_every_switch_in_place(self):
        # Arrange
        rom, guards = sealable_rom(routine_at=ROUTINE_AT, names=("dedither", "divot"))

        # Act
        result = safe_patch(rom, antialiasing=False)

        # Assert
        assert [word(result.data, at) & 0xFFFF for at in guards.values()] == [0x0040, 0x0010]


class TestTheRdpDither:
    @pytest.mark.parametrize(
        ("settings", "label"),
        [
            ({"rdp_color_dither": False}, "colour"),
            ({"rdp_alpha_dither": False}, "alpha"),
            ({"rdp_color_dither": False, "rdp_alpha_dither": False}, "colour and alpha"),
        ],
    )
    def test_each_dither_is_forced_off_in_the_microcode(self, settings, label):
        # Arrange
        rom = with_microcode(make_rom(size=SEALABLE_SIZE))

        # Act
        result = safe_patch(rom, **settings)

        # Assert
        assert (result.applied, result.reason) == (
            True,
            f"{label} dither forced off in 1 microcode",
        )


class TestCompressedCode:
    def test_targets_inside_a_compressed_group_are_patched(self):
        # Arrange
        rom = rom_with_group()

        # Act
        result = safe_patch(rom, antialiasing=False)

        # Assert
        assert (result.applied, result.reason, checksum.verify(result.data)) == (
            True,
            "1 of 1 mode entries changed, in 1 compressed group",
            (True, "6102"),
        )

    def test_direct_targets_are_patched_when_the_compressed_group_cannot_be(self):
        # Arrange
        rom = bytearray(rom_with_group(crc_bin=False))
        rom[TABLE_AT : TABLE_AT + 80] = mode_entry()
        sealed = reseal(bytes(rom), "6102")

        # Act
        result = safe_patch(sealed, antialiasing=False)

        # Assert
        assert (result.applied, result.reason) == (
            True,
            f"1 of 1 mode entries changed, compressed code left alone: {NO_CHECKSUM}",
        )


class TestEveryByteOrder:
    @pytest.mark.parametrize("order", [ByteOrder.V64, ByteOrder.N64])
    def test_the_edited_copy_keeps_the_order_of_the_dump(self, order):
        # Arrange
        rom, _ = sealable_rom()
        expected = swap(safe_patch(rom, antialiasing=False).data, order)

        # Act
        result = safe_patch(swap(rom, order), antialiasing=False)

        # Assert
        assert (result.applied, result.data == expected) == (True, True)
