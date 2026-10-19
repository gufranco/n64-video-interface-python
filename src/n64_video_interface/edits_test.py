from n64_video_interface.conftest import mode_entry, routine
from n64_video_interface.edits import Settings, describe, edit_region


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
