import pytest

from n64_video_interface.byteorder import ByteOrder, detect, swap

Z64_WORDS = bytes.fromhex("80371240 0000000f 80246000 00001449")
V64_WORDS = bytes.fromhex("37804012 00000f00 24800060 00004914")
N64_WORDS = bytes.fromhex("40123780 0f000000 00602480 49140000")


class TestDetect:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            (Z64_WORDS, ByteOrder.Z64),
            (V64_WORDS, ByteOrder.V64),
            (N64_WORDS, ByteOrder.N64),
            (bytes.fromhex("80270740"), ByteOrder.Z64),
            (bytes.fromhex("27804007"), ByteOrder.V64),
            (bytes.fromhex("40072780"), ByteOrder.N64),
            (bytes.fromhex("37804112"), ByteOrder.V64),
            (bytes.fromhex("41123780"), ByteOrder.N64),
            (bytes.fromhex("80371241"), ByteOrder.Z64),
        ],
    )
    def test_the_order_comes_from_where_the_config_bytes_land(self, data, expected):
        # Arrange
        head = data

        # Act
        order = detect(head)

        # Assert
        assert order is expected

    @pytest.mark.parametrize("data", [bytes(4), b"\x80\x37", b"PATCH", bytes.fromhex("12408037")])
    def test_data_that_is_not_a_rom_header_has_no_order(self, data):
        # Arrange
        head = data

        # Act
        order = detect(head)

        # Assert
        assert order is None


class TestSwap:
    @pytest.mark.parametrize(
        ("data", "order"), [(V64_WORDS, ByteOrder.V64), (N64_WORDS, ByteOrder.N64)]
    )
    def test_swapping_reaches_big_endian(self, data, order):
        # Arrange
        stored = data

        # Act
        big = swap(stored, order)

        # Assert
        assert big == Z64_WORDS

    @pytest.mark.parametrize("order", list(ByteOrder))
    def test_swapping_twice_restores_the_data(self, order):
        # Arrange
        stored = Z64_WORDS

        # Act
        twice = swap(swap(stored, order), order)

        # Assert
        assert twice == stored

    def test_big_endian_data_is_returned_unchanged(self):
        # Arrange
        stored = Z64_WORDS

        # Act
        same = swap(stored, ByteOrder.Z64)

        # Assert
        assert same == stored
