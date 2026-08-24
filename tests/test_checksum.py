import struct

import pytest
from tests.conftest import entropy, make_rom

from n64_video_interface import checksum

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
