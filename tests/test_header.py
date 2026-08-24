
from n64_video_interface import header


class TestByteOrderDetection:
    def test_reads_big_endian(self, rom_factory):
        info = header.parse(rom_factory(order="z64"))

        assert info.byte_order == "big endian"
        assert info.true_extension == "z64"

    def test_reads_byteswapped(self, rom_factory):
        info = header.parse(rom_factory(order="v64"))

        assert info.byte_order == "byteswapped"
        assert info.true_extension == "v64"

    def test_reads_little_endian(self, rom_factory):
        info = header.parse(rom_factory(order="n64"))

        assert info.byte_order == "little endian"
        assert info.true_extension == "n64"

    def test_all_orders_decode_to_the_same_fields(self, rom_factory):
        fields = [
            (
                header.parse(rom_factory(order=o)).internal_name,
                header.parse(rom_factory(order=o)).crc1,
            )
            for o in ("z64", "v64", "n64")
        ]

        assert len(set(fields)) == 1

    def test_rejects_content_that_is_not_a_rom(self):
        assert header.parse(b"\x00" * 0x40) is None

    def test_rejects_content_shorter_than_a_header(self):
        assert header.parse(b"\x80\x37\x12\x40") is None


class TestFields:
    def test_extracts_the_internal_title(self, rom_factory):
        info = header.parse(rom_factory(title="SUPER SYNTHETIC 64"))

        assert info.internal_name == "SUPER SYNTHETIC 64"

    def test_strips_padding_from_the_title(self, rom_factory):
        info = header.parse(rom_factory(title="SHORT"))

        assert info.internal_name == "SHORT"

    def test_extracts_checksums_as_uppercase_hex(self, rom_factory):
        info = header.parse(rom_factory(crc1=0xEC58EABF, crc2=0xAD7C7169))

        assert info.crc1 == "EC58EABF"
        assert info.crc2 == "AD7C7169"

    def test_builds_the_four_character_game_code(self, rom_factory):
        info = header.parse(rom_factory(cart="SM", region="E"))

        assert info.game_code == "NSME"

    def test_maps_the_region_code_to_a_name(self, rom_factory):
        assert header.parse(rom_factory(region="E")).region == "USA"
        assert header.parse(rom_factory(region="J")).region == "JPN"
        assert header.parse(rom_factory(region="P")).region == "EUR"

    def test_reports_an_unmapped_region_without_failing(self, rom_factory):
        info = header.parse(rom_factory(region="Q"))

        assert info.region == "unknown"
        assert info.region_code == "Q"

    def test_carries_the_revision_number(self, rom_factory):
        assert header.parse(rom_factory(version=2)).version == 2


class TestIdentityKey:
    def test_the_first_64_bytes_are_the_patch_binding_key(self, rom_factory):
        data = rom_factory()

        assert header.identity_key(data) == data[:64]

    def test_the_key_is_normalised_across_byte_orders(self, rom_factory):
        keys = {header.identity_key(rom_factory(order=o)) for o in ("z64", "v64", "n64")}

        assert len(keys) == 1

    def test_returns_none_when_the_content_is_not_a_rom(self):
        assert header.identity_key(b"nonsense") is None
