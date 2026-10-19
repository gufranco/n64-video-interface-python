import pytest

from n64_video_interface.audit import Report, audit
from n64_video_interface.byteorder import ByteOrder, swap
from n64_video_interface.conftest import mode_entry, routine


class TestTheAudit:
    def test_a_rom_with_a_table_and_a_routine_is_summarised(self):
        # Arrange
        blob, _ = routine(("dedither",))
        rom = (
            bytes(0x100)
            + mode_entry(ctrl=0x0001311E)
            + mode_entry(ctrl=0x00003202, vsync=625)
            + mode_entry(ctrl=0x0001311E)
            + blob
        )

        # Act
        report = audit(rom)

        # Assert
        assert report == Report(
            mode_count=3,
            ctrl_values=(0x0001311E, 0x00003202),
            antialiasing_on=2,
            dither_filter_on=2,
            divot_on=2,
            gamma_dither_on=2,
            standards=("NTSC", "PAL"),
            special_features_sites=1,
            dedither_requests=1,
        )

    def test_a_rom_with_nothing_recognisable_reports_zeros(self):
        # Arrange
        rom = bytes(0x1000)

        # Act
        report = audit(rom)

        # Assert
        assert report == Report(0, (), 0, 0, 0, 0, (), 0, 0)

    @pytest.mark.parametrize("order", [ByteOrder.V64, ByteOrder.N64])
    def test_a_dump_in_any_order_is_read_the_same(self, order):
        # Arrange
        rom = b"\x80\x37\x12\x40" + bytes(0x10C) + mode_entry()

        # Act
        report = audit(swap(rom, order))

        # Assert
        assert report == audit(rom)
