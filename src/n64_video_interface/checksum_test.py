import struct
import zlib

import pytest

from n64_video_interface import checksum
from n64_video_interface.conftest import entropy, make_rom

SIZE = checksum.START + checksum.LENGTH + 0x1000
SEED_THEN_ZEROS_6103 = (0xE498A9E8, 0x689C1E60)
BOOT_CODE_LENGTH = checksum.IPL3_END - checksum.IPL3_START


def crc_table():
    table = []
    for value in range(256):
        crc = value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if crc & 1 else crc >> 1
        table.append(crc)
    return table


CRC_TABLE = crc_table()
CRC_BY_TOP_BYTE = {CRC_TABLE[i] >> 24: i for i in range(256)}


def boot_code_with_crc(target):
    prefix = (bytes(range(256)) * 16)[: BOOT_CODE_LENGTH - 4]
    register = target ^ 0xFFFFFFFF
    for _ in range(4):
        index = CRC_BY_TOP_BYTE[register >> 24]
        register = (((register ^ CRC_TABLE[index]) << 8) | index) & 0xFFFFFFFF
    current = zlib.crc32(prefix) ^ 0xFFFFFFFF
    return prefix + (register ^ current).to_bytes(4, "little")


def rom_with_boot_code(code):
    base = bytearray(make_rom(size=0x2000))
    base[checksum.IPL3_START : checksum.IPL3_END] = code
    return bytes(base)


def with_checksums(data, pair):
    out = bytearray(data)
    struct.pack_into(">II", out, 0x10, *pair)
    return bytes(out)


@pytest.fixture(scope="module")
def body():
    return make_rom(size=SIZE, fill=entropy(SIZE))


class TestCompute:
    def test_a_tie_takes_the_rotated_branch_as_the_boot_code_does(self):
        # Arrange
        rom = bytearray(checksum.START + checksum.LENGTH)
        struct.pack_into(">I", rom, checksum.START, checksum.SEEDS["6103"])

        # Act
        produced = checksum.compute(bytes(rom), "6103")

        # Assert
        assert produced == SEED_THEN_ZEROS_6103

    def test_each_seed_and_combine_gives_a_different_pair(self, body):
        # Arrange
        chips = ("6102", "6103", "6105", "6106")

        # Act
        pairs = {chip: checksum.compute(body, chip) for chip in chips}

        # Assert
        assert len(set(pairs.values())) == len(chips)

    def test_data_too_short_to_checksum_is_refused(self):
        # Arrange
        data = bytes(16)

        # Act
        with pytest.raises(ValueError) as caught:
            checksum.compute(data, "6102")

        # Assert
        assert str(caught.value) == "need 1052672 bytes to checksum, got 16"


class TestVerify:
    @pytest.mark.parametrize("chip", ["6102", "6103", "6105", "6106"])
    def test_a_dump_carrying_its_own_pair_names_its_chip(self, body, chip):
        # Arrange
        rom = with_checksums(body, checksum.compute(body, chip))

        # Act
        verdict = checksum.verify(rom)

        # Assert
        assert verdict == (True, chip)

    @pytest.mark.parametrize(
        "rom",
        [with_checksums(make_rom(size=SIZE), (0xDEADBEEF, 0xCAFEBABE)), b"\x80\x37\x12\x40"],
        ids=["matching no seed", "too short"],
    )
    def test_a_pair_no_chip_produces_is_not_verified(self, rom):
        # Arrange
        data = rom

        # Act
        verdict = checksum.verify(data)

        # Assert
        assert verdict == (False, None)


class TestNamingTheChipFromTheBootCode:
    @pytest.mark.parametrize(("crc", "chip"), list(checksum.IPL3_CRC32.items()))
    def test_boot_code_with_a_known_crc_names_its_chip(self, crc, chip):
        # Arrange
        rom = rom_with_boot_code(boot_code_with_crc(crc))

        # Act
        named = checksum.boot_chip(rom)

        # Assert
        assert named == chip

    @pytest.mark.parametrize(
        "rom",
        [rom_with_boot_code(bytes(BOOT_CODE_LENGTH)), bytes(0x100)],
        ids=["unknown boot code", "too short"],
    )
    def test_unknown_or_missing_boot_code_names_no_chip(self, rom):
        # Arrange
        data = rom

        # Act
        named = checksum.boot_chip(data)

        # Assert
        assert named is None
