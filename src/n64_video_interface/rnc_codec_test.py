import pytest

from n64_video_interface import rnc_codec
from n64_video_interface.rnc_codec import Chunk, CodecError, Sub

PLAIN = b"N64 video " * 3 + b"interface"
TABLES = ((0, 0, 0, 0, 1), (0, 0, 0, 0, 1), (0, 0, 0, 0, 0, 1))
CHUNK = Chunk(TABLES, (Sub(b"N64 video ", 10, 20), Sub(b"interface")))
STREAM = bytes.fromhex("14008028000061000020040048884e363420766964656f200000696e74657266616365")


def framed(stream):
    return bytes(rnc_codec.HEADER) + stream


class TestDecoding:
    def test_a_stream_expands_to_the_bytes_it_was_packed_from(self):
        # Arrange
        data = framed(STREAM)

        # Act
        chunks = rnc_codec.decode(data, 0, len(PLAIN))

        # Assert
        assert (chunks, rnc_codec.expand(chunks)) == ((CHUNK,), PLAIN)

    def test_a_code_missing_from_its_table_is_refused(self):
        # Arrange
        writer = rnc_codec.Writer()
        for value, width in ((0, 2), (1, 5), (2, 4), (0, 5), (0, 5), (1, 16), (3, 2)):
            writer.bits(value, width)
        data = framed(writer.finish() + bytes(4))

        # Act
        with pytest.raises(CodecError) as caught:
            rnc_codec.decode(data, 0, 1)

        # Assert
        assert str(caught.value) == "a code is missing from its table"

    def test_a_copy_right_after_a_copy_has_an_empty_literal_run(self):
        # Arrange
        subs = (Sub(b"ab", 2, 2), Sub(b"", 4, 3), Sub(b""))
        stream = rnc_codec.encode((Chunk(rnc_codec.tables_for(subs), subs),))

        # Act
        produced = rnc_codec.expand(rnc_codec.decode(framed(stream), 0, 7))

        # Assert
        assert produced == b"abababa"

    def test_a_copy_can_reach_into_earlier_output(self):
        # Arrange
        chunks = (Chunk(TABLES, (Sub(b"", 3, 4), Sub(b""))),)

        # Act
        produced = rnc_codec.expand(chunks, b"abc")

        # Assert
        assert produced == b"abca"


class TestEncoding:
    def test_a_decoded_stream_encodes_back_to_the_same_bits(self):
        # Arrange
        chunks = (CHUNK,)

        # Act
        stream = rnc_codec.encode(chunks)

        # Assert
        assert stream == STREAM

    def test_a_last_word_with_eight_bits_or_fewer_loses_its_high_byte(self):
        # Arrange
        chunks = (Chunk(((1,), (), ()), (Sub(b""),)),)

        # Act
        stream = rnc_codec.encode(chunks)

        # Assert
        assert stream == bytes.fromhex("8400200000")


class TestTables:
    @pytest.mark.parametrize(
        ("weights", "expected"),
        [
            ((5,), (1,)),
            ((0, 3, 0), (0, 1, 0)),
            ((4, 2, 1, 1), (1, 2, 3, 3)),
        ],
        ids=["one symbol", "one used among unused", "skewed"],
    )
    def test_code_lengths_follow_the_weights(self, weights, expected):
        # Arrange
        given = weights

        # Act
        lengths = rnc_codec.huffman(given)

        # Assert
        assert lengths == expected

    def test_lengths_past_fifteen_bits_are_flattened(self):
        # Arrange
        fibonacci = (1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987, 1597)

        # Act
        lengths = rnc_codec.huffman(fibonacci)

        # Assert
        assert (max(lengths) <= 15, sum(2.0**-n for n in lengths)) == (True, 1.0)

    def test_the_tables_for_a_chunk_cover_every_value_it_uses(self):
        # Arrange
        subs = CHUNK.subs

        # Act
        tables = rnc_codec.tables_for(subs)

        # Assert
        assert tables == TABLES

    def test_the_size_of_a_chunk_is_counted_in_bits(self):
        # Arrange
        chunk = CHUNK

        # Act
        counted = rnc_codec.bits(chunk)

        # Assert
        assert counted == 264

    @pytest.mark.parametrize(
        ("value", "expected"), [(0, 0), (1, 1), (2, 2), (3, 2), (4, 3), (0x7FFF, 15)]
    )
    def test_a_value_falls_in_the_class_of_its_bit_length(self, value, expected):
        # Arrange
        given = value

        # Act
        found = rnc_codec.symbol(given)

        # Assert
        assert found == expected


def test_a_chunk_that_produces_nothing_is_refused():
    # Arrange
    writer = rnc_codec.Writer()
    for value, width in ((0, 2), (1, 5), (1, 4), (0, 5), (0, 5), (1, 16), (0, 1)):
        writer.bits(value, width)
    data = bytes(rnc_codec.HEADER) + writer.finish() + bytes(8)

    # Act
    with pytest.raises(CodecError) as caught:
        rnc_codec.decode(data, 0, 4)

    # Assert
    assert str(caught.value) == "a chunk produced nothing"
