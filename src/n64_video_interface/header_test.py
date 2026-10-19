import pytest

from n64_video_interface import header
from n64_video_interface.byteorder import ByteOrder, swap
from n64_video_interface.conftest import make_rom


class TestByteOrder:
    @pytest.mark.parametrize(
        ("order", "name"),
        [
            (ByteOrder.Z64, "big endian"),
            (ByteOrder.V64, "byteswapped"),
            (ByteOrder.N64, "little endian"),
        ],
    )
    def test_every_order_is_named_and_decodes_the_same_fields(self, order, name):
        # Arrange
        rom = make_rom(title="ANY ORDER", crc1=0xEC58EABF, order=order)

        # Act
        info = header.parse(rom)

        # Assert
        assert (info.byte_order, info.true_extension, info.internal_name, info.crc1) == (
            name,
            order.value,
            "ANY ORDER",
            "EC58EABF",
        )

    @pytest.mark.parametrize("order", list(ByteOrder))
    def test_a_dump_whose_config_word_is_not_the_common_one_is_read(self, order):
        # Arrange
        data = bytearray(make_rom(title="OTHER CONFIG"))
        data[0:4] = bytes.fromhex("80270740")

        # Act
        info = header.parse(swap(bytes(data), order))

        # Assert
        assert (info.internal_name, info.true_extension) == ("OTHER CONFIG", order.value)

    @pytest.mark.parametrize(
        "data", [bytes(0x40), b"\x80\x37\x12\x40"], ids=["no header", "shorter than a header"]
    )
    def test_content_that_is_not_a_rom_header_is_rejected(self, data):
        # Arrange
        content = data

        # Act
        info = header.parse(content)

        # Assert
        assert info is None


class TestFields:
    def test_every_field_is_read_from_its_offset(self):
        # Arrange
        rom = make_rom(
            title="SHORT", cart="SM", region="E", version=2, crc1=0xEC58EABF, crc2=0xAD7C7169
        )

        # Act
        info = header.parse(rom)

        # Assert
        assert (
            info.internal_name,
            info.game_code,
            info.region,
            info.version,
            info.crc1,
            info.crc2,
        ) == ("SHORT", "NSME", "USA", 2, "EC58EABF", "AD7C7169")

    @pytest.mark.parametrize(("code", "region"), [("J", "JPN"), ("P", "EUR"), ("Q", "unknown")])
    def test_the_region_code_maps_to_a_name(self, code, region):
        # Arrange
        rom = make_rom(region=code)

        # Act
        info = header.parse(rom)

        # Assert
        assert (info.region_code, info.region) == (code, region)

    def test_unprintable_media_and_region_bytes_read_as_a_question_mark(self):
        # Arrange
        data = bytearray(make_rom())
        data[0x3B] = 0x01
        data[0x3E] = 0xFF

        # Act
        info = header.parse(bytes(data))

        # Assert
        assert (info.media, info.region_code, info.region) == ("?", "?", "unknown")
