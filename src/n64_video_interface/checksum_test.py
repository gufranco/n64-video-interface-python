import struct
import zlib

import pytest

from n64_video_interface import checksum
from n64_video_interface.conftest import entropy, make_rom

SIZE = checksum.START + checksum.LENGTH + 0x1000


@pytest.fixture(scope="module")
def body():
    return make_rom(size=SIZE, fill=entropy(SIZE))


@pytest.fixture(scope="module")
def computed(body):
    return {cic: checksum.compute(body, cic) for cic in ("6102", "6103", "6105", "6106")}


def with_checksums(data, pair):
    out = bytearray(data)
    struct.pack_into(">I", out, 0x10, pair[0])
    struct.pack_into(">I", out, 0x14, pair[1])
    return bytes(out)


class TestCompute:
    def test_returns_a_pair_for_every_known_cic(self, computed):
        assert all(len(v) == 2 for v in computed.values())

    def test_every_value_fits_in_32_bits(self, computed):
        assert all(0 <= n <= 0xFFFFFFFF for pair in computed.values() for n in pair)

    def test_the_cic_variants_do_not_all_agree(self, computed):
        assert len(set(computed.values())) > 1

    def test_6105_differs_from_6102_because_it_reads_the_boot_table(self, computed):
        assert computed["6105"] != computed["6102"]

    def test_6103_differs_from_6102_because_the_final_combine_adds(self, computed):
        assert computed["6103"] != computed["6102"]

    def test_6106_differs_from_6103_because_the_final_combine_multiplies(self, computed):
        assert computed["6106"] != computed["6103"]

    def test_returns_none_when_the_data_is_too_short(self):
        assert checksum.compute(b"\x00" * 16, "6102") is None

    def test_rejects_an_unknown_cic(self, body):
        with pytest.raises(KeyError):
            checksum.compute(body, "9999")


class TestVerify:
    def test_accepts_a_rom_carrying_its_own_6102_checksum(self, body, computed):
        rom = with_checksums(body, computed["6102"])

        ok, cic = checksum.verify(rom)

        assert ok is True
        assert cic == "6102"

    def test_accepts_a_rom_carrying_its_own_6105_checksum(self, body, computed):
        rom = with_checksums(body, computed["6105"])

        ok, cic = checksum.verify(rom)

        assert ok is True
        assert cic == "6105"

    def test_rejects_a_rom_whose_checksum_matches_no_seed(self, body):
        rom = with_checksums(body, (0xDEADBEEF, 0xCAFEBABE))

        ok, cic = checksum.verify(rom)

        assert ok is False
        assert cic is None

    def test_reports_unverified_rather_than_raising_on_short_data(self):
        ok, cic = checksum.verify(b"\x80\x37\x12\x40")

        assert ok is False
        assert cic is None


class TestNamingTheChipFromTheBootCode:
    """The header checksum names a chip only when the header agrees with the data,
    and it cannot separate 6101 from 6102 at all because those two share a seed.
    The boot code answers both cases: it is chip-specific and it sits outside the
    range the checksum covers, so an edited header leaves it intact."""

    def rom_with_boot_code(self, code: bytes) -> bytes:
        base = bytearray(make_rom(size=0x101000))
        base[0x40 : 0x40 + len(code)] = code
        return bytes(base)

    def test_it_names_the_chip_whose_boot_code_matches(self, monkeypatch):
        code = (bytes(range(256)) * 16)[: checksum.IPL3_END - checksum.IPL3_START]
        crc = zlib.crc32(code) & 0xFFFFFFFF
        monkeypatch.setitem(checksum.IPL3_CRC32, crc, "6103")

        assert checksum.boot_chip(self.rom_with_boot_code(code)) == "6103"

    def test_boot_code_it_does_not_know_names_no_chip(self):
        code = (bytes(range(255, -1, -1)) * 16)[: checksum.IPL3_END - checksum.IPL3_START]

        assert checksum.boot_chip(self.rom_with_boot_code(code)) is None

    def test_a_rom_too_short_to_hold_boot_code_names_no_chip(self):
        assert checksum.boot_chip(bytes(0x100)) is None

    def test_the_published_table_is_pinned(self):
        """These are the values every emulator keys on. A typo here would name the
        wrong chip on a real cartridge, which is the one error worth pinning."""
        assert checksum.IPL3_CRC32 == {
            0x6170A4A1: "6101",
            0x009E9EA3: "7102",
            0x90BB6CB5: "6102",
            0x0B050EE0: "6103",
            0x98BC2C86: "6105",
            0xACC8580A: "6106",
        }

    def test_it_separates_the_two_chips_the_checksum_cannot(self):
        assert checksum.SEEDS["6101"] == checksum.SEEDS["6102"]
        assert len(set(checksum.IPL3_CRC32.values())) == len(checksum.IPL3_CRC32)
