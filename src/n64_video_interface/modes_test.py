import struct

import pytest

from n64_video_interface import ctrl, modes
from n64_video_interface.conftest import NTSC_LAN1_CTRL, mode_entry


def reference_scan(rom):
    found = []
    for at in range(0, max(0, len(rom) - 16), 4):
        if not ctrl.plausible(struct.unpack_from(">I", rom, at)[0]):
            continue
        if struct.unpack_from(">I", rom, at + 4)[0] not in modes.PLAUSIBLE_WIDTHS:
            continue
        if struct.unpack_from(">I", rom, at + 12)[0] not in modes.VSYNC_BY_STANDARD:
            continue
        if modes.has_osvimode_shape(rom, at):
            found.append(at)
    return found


def offsets(rom):
    return [entry.ctrl_offset for entry in modes.find_mode_tables(rom)]


class TestFindingModeEntries:
    def test_a_planted_entry_is_found_at_its_ctrl_word(self):
        # Arrange
        rom = bytes(0x400) + mode_entry() + bytes(0x400)

        # Act
        found = modes.find_mode_tables(rom)

        # Assert
        assert [(e.ctrl_offset, e.ctrl, e.width, e.vsync, e.standard) for e in found] == [
            (0x404, NTSC_LAN1_CTRL, 320, 525, "NTSC")
        ]

    @pytest.mark.parametrize(
        ("vsync", "standard"), [(525, "NTSC"), (524, "NTSC"), (625, "PAL"), (624, "PAL")]
    )
    def test_progressive_and_interlaced_entries_of_both_standards_are_found(self, vsync, standard):
        # Arrange
        rom = bytes(16) + mode_entry(vsync=vsync)

        # Act
        found = modes.find_mode_tables(rom)

        # Assert
        assert [entry.standard for entry in found] == [standard]

    @pytest.mark.parametrize("width", [320, 640, 1280])
    def test_every_width_seen_in_retail_dumps_is_found(self, width):
        # Arrange
        rom = bytes(16) + mode_entry(width=width)

        # Act
        found = modes.find_mode_tables(rom)

        # Assert
        assert [entry.width for entry in found] == [width]

    @pytest.mark.parametrize(
        "entry",
        [
            mode_entry(width=12345),
            mode_entry(vsync=999),
            mode_entry(ctrl=0xDEADBEEF),
            mode_entry(burst=0, hsync=0),
            mode_entry(kind=56 << 24),
            mode_entry(kind=0x02000001),
            mode_entry()[:40],
        ],
        ids=[
            "width",
            "vsync",
            "ctrl",
            "zeroed registers",
            "type",
            "padding",
            "cut short",
        ],
    )
    def test_three_matching_words_outside_a_whole_osvimode_are_ignored(self, entry):
        # Arrange
        rom = bytes(16) + entry

        # Act
        found = modes.find_mode_tables(rom)

        # Assert
        assert found == ()

    def test_entries_are_returned_in_rom_order(self):
        # Arrange
        rom = bytes(16) + mode_entry(vsync=625) + mode_entry(vsync=525)

        # Act
        found = offsets(rom)

        # Assert
        assert found == [0x14, 0x64]


class TestTheFastScanAgreesWithAnExhaustiveOne:
    @pytest.mark.parametrize(
        "rom",
        [
            bytes(0x100) + mode_entry() + mode_entry(width=640, vsync=625) + bytes(0x100),
            mode_entry()[4:] + bytes(0x100),
            bytes(0x100) + mode_entry(),
            bytes(0x800),
            bytes(((i * 31 + 7) & 0xFF) for i in range(0x4000)),
            bytes(2) + mode_entry() + bytes(0x100),
        ],
        ids=["planted", "at the start", "at the end", "empty", "noise", "unaligned"],
    )
    def test_both_scans_find_the_same_entries(self, rom):
        # Arrange
        expected = reference_scan(rom)

        # Act
        found = offsets(rom)

        # Assert
        assert found == expected
