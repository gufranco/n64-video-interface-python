import random
import struct

import pytest

from n64_video_interface import lzss
from n64_video_interface.conftest import mode_entry
from n64_video_interface.edits import NO_TARGETS, Settings

RNG = random.Random(64)
PLAIN = b"N64 video " * 3 + b"interface"
STREAM = bytes.fromhex("ff4e36342076696465fb6f20eeff6f20696e743f657266616365")


def entry_block():
    return RNG.randbytes(0x80) + mode_entry() + RNG.randbytes(0x80)


def segment(data):
    body = lzss.encode(lzss.optimal(data, 0, len(data)))
    return lzss.MAGIC + struct.pack(">2I", len(data), len(body)) + body


def rom_with(*segments):
    return bytes(0x40) + b"".join(segments) + bytes(0x40)


class TestTheStream:
    def test_tokens_encode_to_the_known_stream(self):
        # Arrange
        tokens = (lzss.Literals(PLAIN[:10]), lzss.Copy(10, 18), lzss.Literals(PLAIN[28:]))

        # Act
        stream = lzss.encode(tokens)

        # Assert
        assert stream == STREAM

    def test_the_stream_decodes_to_its_bytes(self):
        # Arrange
        data = STREAM

        # Act
        produced, used = lzss.decode(data, 0, len(PLAIN))

        # Assert
        assert (produced, used) == (PLAIN, len(STREAM))

    def test_a_copy_can_read_the_zeros_the_ring_starts_with(self):
        # Arrange
        stream = bytes((0x00, 0x00, 0x0F))

        # Act
        produced, _ = lzss.decode(stream, 0, 18)

        # Assert
        assert produced == bytes(18)

    @pytest.mark.parametrize("size", [1, 17, 300, 5000])
    def test_any_data_survives_a_round_trip(self, size):
        # Arrange
        data = bytes(RNG.choice(b"abc\x00") for _ in range(size))

        # Act
        produced, _ = lzss.decode(lzss.encode(lzss.optimal(data, 0, len(data))), 0, size)

        # Assert
        assert produced == data

    def test_a_stream_that_runs_out_is_refused(self):
        # Arrange
        stream = STREAM[:5]

        # Act
        with pytest.raises(lzss.FormatError) as caught:
            lzss.decode(stream, 0, len(PLAIN))

        # Assert
        assert str(caught.value) == "the stream ends before its data"


class TestImproving:
    def test_windows_that_did_not_change_are_kept_once_the_stream_fits(self):
        # Arrange
        data = b"".join(RNG.choice((PLAIN, RNG.randbytes(8))) for _ in range(0x900))
        original = lzss.optimal(data, 0, len(data))
        desired = b"Z" + data[1:]
        edited = lzss.split(original, desired)
        budget = sum(map(lzss.cost, original)) + 64

        # Act
        improved = lzss.improve(edited, desired, data, budget)

        # Assert
        assert (lzss.expand(improved), improved[-3:]) == (desired, original[-3:])


class TestPatching:
    def test_a_target_inside_a_segment_is_patched(self):
        # Arrange
        rom = rom_with(segment(RNG.randbytes(0x40)), segment(entry_block()))

        # Act
        packed = lzss.edit_segments(rom, Settings(antialiasing=False))

        # Assert
        found = lzss.find_segments(packed.data)
        data, _ = lzss.decode(packed.data, found[1].body, found[1].size)
        assert (packed.where, data[0x84:0x88]) == ("in 1 LZSS segment", bytes.fromhex("0000321e"))

    def test_a_segment_that_no_longer_fits_is_left_alone(self):
        # Arrange
        pairs = []
        for _ in range(8):
            near = entry_block()
            pairs.append(near[:0x90] + bytes(4) + near[0x94:] + near)
        rom = rom_with(segment(b"".join(pairs)))

        # Act
        packed = lzss.edit_segments(rom, Settings(antialiasing=False))

        # Assert
        assert packed == lzss.NO_ROOM

    @pytest.mark.parametrize(
        "rom",
        [
            bytes(0x400),
            bytes(0x40) + lzss.MAGIC + struct.pack(">2I", 0x400, 0x10) + bytes(0x10),
            bytes(0x40) + lzss.MAGIC + struct.pack(">2I", 0, 0x10) + bytes(0x10),
            bytes(0x40) + lzss.MAGIC + b"\x00",
        ],
        ids=["no magic", "does not decode to its size", "empty", "cut off"],
    )
    def test_a_rom_without_a_valid_segment_has_no_targets(self, rom):
        # Arrange
        data = rom

        # Act
        packed = lzss.edit_segments(data, Settings(antialiasing=False))

        # Assert
        assert packed == NO_TARGETS
