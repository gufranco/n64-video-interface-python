import struct

import pytest

from n64_video_interface.conftest import make_rom, mode_entry, routine
from n64_video_interface.edits import NO_TABLE, Settings, describe, edit_region

SWAP_AT = 0x380
NO_TABLE_TEXT = f"{NO_TABLE}, so anti-aliasing and the table settings are unchanged"


class TestEditingARegion:
    def test_the_table_and_the_switches_are_edited_in_place(self):
        # Arrange
        blob, placed = routine(("dedither",))
        region = bytes(0x10) + mode_entry() + blob

        # Act
        edit = edit_region(region, Settings(antialiasing=False, dither_filter=False))

        # Assert
        guard = 0x10 + 80 + placed["dedither"][0]
        assert (
            edit.entries,
            edit.modes_changed,
            [r.switch for r in edit.requests],
            edit.data[0x14:0x18],
            edit.data[guard + 2 : guard + 4],
            len(edit.data),
        ) == (1, 1, ["dedither"], bytes.fromhex("0000321e"), b"\x00\x00", len(region))

    def test_a_region_with_nothing_to_edit_comes_back_unchanged(self):
        # Arrange
        region = bytes(0x400)

        # Act
        edit = edit_region(region, Settings(antialiasing=False))

        # Assert
        assert (edit.data, edit.changes, edit.entries) == (region, (), 0)


class TestDescribing:
    def test_edits_from_several_regions_are_summed(self):
        # Arrange
        blob, _ = routine(("dedither",))
        settings = Settings(antialiasing=False, dither_filter=False)
        edits = [edit_region(bytes(0x10) + mode_entry(), settings), edit_region(blob, settings)]

        # Act
        text = describe(edits, settings)

        # Assert
        assert text == "1 of 1 mode entries changed, 1 dedither request neutralised"

    def test_a_constant_written_to_the_control_register_is_counted(self):
        # Arrange
        region = bytearray(0x100)
        struct.pack_into(">3I", region, 0x40, 0x3C02A440, 0x2409311E, 0xAC490000)
        settings = Settings(antialiasing=False, divot=False, gamma_dither=False)
        edit = edit_region(bytes(region), settings)

        # Act
        text = describe([edit], settings)

        # Assert
        assert (text, edit.data[0x44:0x48]) == (
            f"{NO_TABLE_TEXT}, 1 VI control constant changed",
            bytes.fromhex("2409320a"),
        )


class TestIntegrityCheckedTitles:
    @pytest.mark.parametrize(
        ("cart", "expected"), [("AL", 0x3C010000), ("ZZ", 0x3C010001)], ids=["checked", "other"]
    )
    def test_a_dedither_setter_is_cleared_only_where_the_filter_cannot_go(self, cart, expected):
        # Arrange
        rom = bytearray(make_rom(size=0x2000, cart=cart))
        blob, _ = routine(("dedither",))
        rom[0x1000 : 0x1000 + len(blob)] = blob
        struct.pack_into(">4I", rom, 0x1200, 0x8C4E0004, 0x3C010001, 0x01C17825, 0xAC4F0004)

        # Act
        edit = edit_region(bytes(rom), Settings(dither_filter=False))

        # Assert
        assert struct.unpack_from(">I", edit.data, 0x1204)[0] == expected


class TestTheControlFilter:
    def test_a_free_dedither_block_carries_the_control_filter(self):
        # Arrange
        blob, _ = routine(("dedither",), skip=9)
        region = bytearray(blob)
        swap = (0x3C0DA440, 0x8E28000C, 0xADA80000, 0x8DEF5A20)
        for index, value in enumerate(swap):
            struct.pack_into(">I", region, SWAP_AT + index * 4, value)
        settings = Settings(antialiasing=False, dither_filter=False)

        # Act
        edit = edit_region(bytes(region), settings)

        # Assert
        assert (edit.filters, describe([edit], settings)) == (
            1,
            "no video mode table could be proven present, so anti-aliasing and the table "
            "settings are unchanged, 1 dedither request neutralised, 1 VI control store filtered",
        )
