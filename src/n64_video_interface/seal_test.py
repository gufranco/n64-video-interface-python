import struct
import zlib

import pytest

from n64_video_interface import checksum
from n64_video_interface.byteorder import ByteOrder, swap
from n64_video_interface.checksum_test import boot_code_with_crc
from n64_video_interface.conftest import SEALABLE_SIZE, make_rom
from n64_video_interface.seal import SealError, reseal


def rom_with_chip(chip):
    data = bytearray(make_rom(size=SEALABLE_SIZE))
    crc = next(value for value, name in checksum.IPL3_CRC32.items() if name == chip)
    data[checksum.IPL3_START : checksum.IPL3_END] = boot_code_with_crc(crc)
    return bytes(data)


class TestResealing:
    @pytest.mark.parametrize("chip", ["6102", "6103", "6105", "6106"])
    def test_the_chip_named_by_the_boot_code_seals_the_dump(self, chip):
        # Arrange
        rom = rom_with_chip(chip)

        # Act
        sealed = reseal(rom)

        # Assert
        assert checksum.verify(sealed) == (True, chip)

    def test_a_chip_given_by_the_caller_overrides_the_boot_code(self):
        # Arrange
        rom = rom_with_chip("6102")

        # Act
        sealed = reseal(rom, "6105")

        # Assert
        assert checksum.verify(sealed) == (True, "6105")

    def test_only_the_two_checksum_words_move(self):
        # Arrange
        rom = make_rom(size=SEALABLE_SIZE)

        # Act
        sealed = reseal(rom, "6102")

        # Assert
        assert {i for i in range(len(rom)) if rom[i] != sealed[i]} <= set(range(0x10, 0x18))

    @pytest.mark.parametrize("order", [ByteOrder.V64, ByteOrder.N64])
    def test_a_dump_in_another_order_is_sealed_in_its_own_order(self, order):
        # Arrange
        rom = rom_with_chip("6102")

        # Act
        sealed = reseal(swap(rom, order))

        # Assert
        assert swap(sealed, order) == reseal(rom)

    @pytest.mark.parametrize(
        ("rom", "message"),
        [
            (bytes(0x100), "need 1052672 bytes to checksum, got 256"),
            (
                make_rom(size=SEALABLE_SIZE),
                "the boot code names no known chip, so the seal is unknown",
            ),
        ],
        ids=["too short", "unknown boot code"],
    )
    def test_a_dump_that_cannot_be_sealed_is_refused(self, rom, message):
        # Arrange
        data = rom

        # Act
        with pytest.raises(SealError) as caught:
            reseal(data)

        # Assert
        assert str(caught.value) == message


def test_the_forged_boot_code_has_the_crc_it_was_built_for():
    # Arrange
    target = 0x0B050EE0

    # Act
    code = boot_code_with_crc(target)

    # Assert
    assert (zlib.crc32(code), len(code)) == (target, checksum.IPL3_END - checksum.IPL3_START)


def test_a_sealed_pair_is_written_big_endian_at_0x10():
    # Arrange
    rom = rom_with_chip("6102")

    # Act
    sealed = reseal(rom)

    # Assert
    assert struct.unpack_from(">2I", sealed, 0x10) == checksum.compute(rom, "6102")
