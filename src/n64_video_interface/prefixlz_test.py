import random
import struct

import pytest

from n64_video_interface import prefixlz
from n64_video_interface.bitlz import Copy, Literals, expand
from n64_video_interface.conftest import mode_entry
from n64_video_interface.edits import NO_TARGETS, Settings

RNG = random.Random(64)
PLAIN = b"N64 video " * 3 + b"interface"
TOKENS = (Literals(PLAIN[:10]), Copy(10, 20), Literals(PLAIN[30:]))
STREAM = bytes.fromhex("00000027") + b"\x89N64 video \xa2\x19\x88interface"
ENTRY = 0x80200400
STREAM_AT = 0x12B0
LOADER_AT = 0x1090
LOADER = (0x3C028020, 0x8C4306B0, 0x00000000, 0x2C820090, 0x2C8200A0, 0x2C8200C0)
TEXT = b"N64 video interface, every option off. " * 0x20


def entry_block():
    return TEXT + mode_entry() + TEXT


def stream(data):
    return prefixlz.encode(prefixlz.optimal(data, 0, len(data)), data)


def rom_with(body, *, loader=LOADER, entry=ENTRY):
    rom = bytearray(0x101000)
    rom[0:4] = b"\x80\x37\x12\x40"
    struct.pack_into(">I", rom, 8, entry)
    struct.pack_into(f">{len(loader)}I", rom, LOADER_AT, *loader)
    rom[STREAM_AT : STREAM_AT + len(body)] = body
    return bytes(rom)


class TestTheStream:
    def test_tokens_encode_to_the_known_stream(self):
        # Arrange
        tokens = TOKENS

        # Act
        encoded = prefixlz.encode(tokens, PLAIN)

        # Assert
        assert encoded == STREAM

    def test_the_stream_decodes_back_to_its_tokens_and_length(self):
        # Arrange
        data = bytes(4) + STREAM + bytes(8)

        # Act
        decoded = prefixlz.decode(data, 4)

        # Assert
        assert decoded == (TOKENS, len(STREAM))

    @pytest.mark.parametrize(
        "body",
        [
            bytes.fromhex("00000005") + b"\x81ab" + bytes.fromhex("9001"),
            bytes.fromhex("00000015a180") + RNG.randbytes(17) + bytes.fromhex("c1a000"),
        ],
        ids=["paired prefix", "offset and length prefixes"],
    )
    def test_every_prefix_kind_is_understood(self, body):
        # Arrange
        data = body

        # Act
        tokens, used = prefixlz.decode(data, 0)

        # Assert
        assert (used, len(expand(tokens))) == (len(data), struct.unpack_from(">I", data)[0])

    @pytest.mark.parametrize(
        "tokens",
        [
            (Literals(RNG.randbytes(40)),),
            (Literals(b"ab"), Copy(2, 2)),
            (Literals(RNG.randbytes(0x30)), Copy(0x30, 0x200)),
            (Literals(RNG.randbytes(0x500)), Copy(0x500, 3)),
        ],
        ids=["long literal run", "two-byte copy", "long copy", "far copy too short to encode"],
    )
    def test_any_tokens_round_trip(self, tokens):
        # Arrange
        data = expand(tokens)

        # Act
        decoded, _ = prefixlz.decode(prefixlz.encode(tokens, data), 0)

        # Assert
        assert expand(decoded) == data

    @pytest.mark.parametrize("size", [1, 17, 300, 5000])
    def test_any_data_survives_a_round_trip(self, size):
        # Arrange
        data = bytes(RNG.choice(b"abc\x00") for _ in range(size)) + RNG.randbytes(size)

        # Act
        decoded, _ = prefixlz.decode(stream(data), 0)

        # Assert
        assert expand(decoded) == data

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            (bytes.fromhex("00000004") + b"\x05", "a copy reaches before the start"),
            (STREAM[:-3], "the stream ends before its data"),
            (STREAM[:16], "the stream ends before its data"),
            (STREAM[:3] + b"\x26" + STREAM[4:], "the stream does not decode to its size"),
        ],
        ids=["copy before the start", "cut literal run", "cut off", "overshoots"],
    )
    def test_a_broken_stream_is_refused(self, data, message):
        # Arrange
        given = data

        # Act
        with pytest.raises(prefixlz.FormatError) as caught:
            prefixlz.decode(given, 0)

        # Assert
        assert str(caught.value).startswith(message)


class TestCosts:
    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            (Literals(b"ab"), 3),
            (Literals(bytes(17)), 19),
            (Copy(16, 9), 1),
            (Copy(17, 9), 2),
            (Copy(0x500, 3), prefixlz.UNREACHED),
        ],
        ids=["short run", "run with a prefix", "near copy", "far copy", "unencodable copy"],
    )
    def test_each_token_costs_its_bytes(self, token, expected):
        # Arrange
        given = token

        # Act
        size = prefixlz.cost(given)

        # Assert
        assert size == expected


class TestFinding:
    def test_the_stream_the_loader_reads_is_found(self):
        # Arrange
        body = stream(entry_block())
        rom = rom_with(body)

        # Act
        found = prefixlz.find_streams(rom)

        # Assert
        assert [(s.at, s.size, s.packed) for s in found] == [
            (STREAM_AT, len(entry_block()), len(body))
        ]

    @pytest.mark.parametrize(
        ("loader", "entry"),
        [
            ((0, 0, 0, 0x2C820090, 0x2C8200A0, 0x2C8200C0), ENTRY),
            ((0x3C028020, 0x8C4306B0, 0, 0x2C820090, 0, 0), ENTRY),
            ((0x3C028040, 0x8C4306B0, 0, 0x2C820090, 0x2C8200A0, 0x2C8200C0), ENTRY),
            (LOADER, 0x80100400),
            ((0, 0x8C4306B0, 0, 0x2C820090, 0x2C8200A0, 0x2C8200C0), ENTRY),
        ],
        ids=[
            "no pointer",
            "no decoder",
            "pointer outside the boot code",
            "other load address",
            "pointer without its lui",
        ],
    )
    def test_a_rom_without_that_loader_has_no_stream(self, loader, entry):
        # Arrange
        rom = rom_with(stream(entry_block()), loader=loader, entry=entry)

        # Act
        found = prefixlz.find_streams(rom)

        # Assert
        assert found == ()

    @pytest.mark.parametrize(
        "body",
        [bytes.fromhex("00000004") + b"\x05", bytes(8)],
        ids=["does not decode", "empty"],
    )
    def test_a_pointer_at_bytes_that_are_not_a_stream_finds_nothing(self, body):
        # Arrange
        rom = rom_with(body)

        # Act
        found = prefixlz.find_streams(rom)

        # Assert
        assert found == ()

    def test_a_rom_shorter_than_its_boot_code_has_no_stream(self):
        # Arrange
        rom = rom_with(stream(entry_block()))[:0x2000]

        # Act
        found = prefixlz.find_streams(rom)

        # Assert
        assert found == ()


class TestPatching:
    def test_a_target_inside_the_stream_is_patched_in_place(self):
        # Arrange
        rom = rom_with(stream(entry_block()))

        # Act
        packed = prefixlz.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        tokens, _ = prefixlz.decode(packed.data, STREAM_AT)
        at = len(TEXT) + 4
        assert (packed.where, len(packed.data), expand(tokens)[at : at + 4]) == (
            "in 1 prefix-coded stream",
            len(rom),
            bytes.fromhex("0000321e"),
        )

    def test_a_stream_that_no_longer_fits_is_left_alone(self):
        # Arrange
        near = RNG.randbytes(0x80) + mode_entry() + RNG.randbytes(0x80)
        far = near[:0x90] + bytes(4) + near[0x94:]
        rom = rom_with(stream(b"".join(RNG.randbytes(0x20) + far + near for _ in range(8))))

        # Act
        packed = prefixlz.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        assert packed == prefixlz.NO_ROOM

    def test_a_stream_without_targets_is_left_alone(self):
        # Arrange
        rom = rom_with(stream(TEXT))

        # Act
        packed = prefixlz.edit_streams(rom, Settings(antialiasing=False))

        # Assert
        assert packed == NO_TARGETS
