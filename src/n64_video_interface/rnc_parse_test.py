import random

from n64_video_interface import rnc_codec, rnc_parse
from n64_video_interface.rnc_codec import Chunk, Sub

TABLES = ((0, 0, 0, 0, 1), (0, 0, 0, 0, 1), (0, 0, 0, 0, 0, 1))
PLAIN = b"N64 video " * 3 + b"interface"
CHUNK = Chunk(TABLES, (Sub(b"N64 video ", 10, 20), Sub(b"interface")))
UNIFORM = ((4,) * 16, (4,) * 16, (4,) * 16)


def chunk_for(subs):
    return Chunk(rnc_codec.tables_for(subs), subs)


class TestSplitting:
    def test_a_copied_byte_that_must_change_becomes_a_literal(self):
        # Arrange
        desired = PLAIN[:12] + b"X" + PLAIN[13:]

        # Act
        chunks = rnc_parse.split((CHUNK,), desired)

        # Assert
        assert (rnc_codec.expand(chunks), chunks[0].subs) == (
            desired,
            (Sub(b"N64 video ", 10, 2), Sub(b"X", 10, 9), Sub(b"4", 10, 7), Sub(b"interface")),
        )

    def test_a_literal_that_must_change_is_rewritten_in_place(self):
        # Arrange
        desired = b"M" + PLAIN[1:10] + b"M" + PLAIN[11:20] + b"M" + PLAIN[21:]

        # Act
        chunks = rnc_parse.split((CHUNK,), desired)

        # Assert
        assert (rnc_codec.expand(chunks), chunks[0].tables) == (desired, TABLES)

    def test_a_copy_left_one_byte_long_turns_into_a_literal(self):
        # Arrange
        desired = PLAIN[:10] + b"N" + b"Z" + PLAIN[12:]

        # Act
        chunks = rnc_parse.split((CHUNK,), desired)

        # Assert
        assert (rnc_codec.expand(chunks), chunks[0].subs[0]) == (
            desired,
            Sub(b"N64 video NZ", 10, 9),
        )

    def test_a_chunk_with_nothing_to_change_is_kept_as_it_was(self):
        # Arrange
        chunks = (CHUNK, CHUNK)

        # Act
        kept = rnc_parse.split(chunks, PLAIN * 2)

        # Assert
        assert kept == chunks


class TestOptimalParsing:
    def test_the_parse_expands_back_to_the_data(self):
        # Arrange
        rng = random.Random(64)
        data = bytes(rng.choice(b"ab") for _ in range(0x400))

        # Act
        subs = rnc_parse.optimal(data, 0, len(data), UNIFORM)

        # Assert
        assert rnc_codec.expand((chunk_for(subs),)) == data

    def test_repeats_cost_fewer_bits_than_literals(self):
        # Arrange
        data = PLAIN * 8

        # Act
        subs = rnc_parse.optimal(data, 0, len(data), UNIFORM)

        # Assert
        assert rnc_codec.bits(chunk_for(subs)) < rnc_codec.bits(chunk_for((Sub(data),)))

    def test_copies_never_reach_past_the_window(self):
        # Arrange
        rng = random.Random(7)
        block = rng.randbytes(0x100)
        data = block + rng.randbytes(rnc_parse.WINDOW) + block

        # Act
        subs = rnc_parse.optimal(data, len(data) - 0x100, len(data), UNIFORM)

        # Assert
        assert all(sub.distance <= rnc_parse.WINDOW for sub in subs)

    def test_a_later_part_can_copy_from_before_it(self):
        # Arrange
        data = PLAIN + PLAIN

        # Act
        subs = rnc_parse.optimal(data, len(PLAIN), len(data), UNIFORM)

        # Assert
        assert (subs[0].literals, subs[0].distance, subs[0].length) == (b"", len(PLAIN), len(PLAIN))


class TestImproving:
    def test_only_chunks_that_changed_are_parsed_again(self):
        # Arrange
        original = (CHUNK, CHUNK)
        desired = PLAIN + PLAIN[:12] + b"X" + PLAIN[13:]
        edited = rnc_parse.split(original, desired)

        # Act
        improved = rnc_parse.improve(edited, original, desired)

        # Assert
        assert (improved[0], rnc_codec.expand(improved)) == (CHUNK, desired)

    def test_a_reparse_that_costs_more_is_dropped(self):
        # Arrange
        changed = Chunk(TABLES, (Sub(b"N64 video ", 10, 20), Sub(b"interfacf")))
        desired = rnc_codec.expand((changed,))

        # Act
        improved = rnc_parse.improve((changed,), (CHUNK,), desired)

        # Assert
        assert rnc_codec.bits(improved[0]) <= rnc_codec.bits(changed)
