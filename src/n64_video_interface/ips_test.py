import pytest

from n64_video_interface.ips import IpsError, make_ips

EOF_OFFSET = 0x454F46


def apply_ips(original: bytes, patch: bytes) -> bytes:
    assert patch[:5] == b"PATCH"
    out = bytearray(original)
    at = 5
    while patch[at : at + 3] != b"EOF":
        offset = int.from_bytes(patch[at : at + 3], "big")
        size = int.from_bytes(patch[at + 3 : at + 5], "big")
        at += 5
        out[offset : offset + size] = patch[at : at + size]
        at += size
    assert at + 3 == len(patch)
    return bytes(out)


def edited(original: bytes, edits: dict[int, bytes]) -> bytes:
    out = bytearray(original)
    for offset, payload in edits.items():
        out[offset : offset + len(payload)] = payload
    return bytes(out)


class TestMakeIps:
    def test_identical_data_gives_an_empty_patch(self):
        # Arrange
        original = bytes(0x2000)

        # Act
        patch = make_ips(original, original)

        # Assert
        assert patch == b"PATCHEOF"

    def test_one_changed_word_becomes_one_record(self):
        # Arrange
        original = bytes(0x2000)
        patched = edited(original, {0x1004: b"\x00\x00\x32\x0e"})

        # Act
        patch = make_ips(original, patched)

        # Assert
        assert patch == b"PATCH" + b"\x00\x10\x06\x00\x02\x32\x0e" + b"EOF"

    def test_applying_the_patch_reproduces_scattered_edits(self):
        # Arrange
        original = bytes(range(256)) * 64
        patched = edited(original, {0x10: b"\xaa" * 8, 0x0FFE: b"\xbb" * 4, 0x3000: b"\xcc"})

        # Act
        patch = make_ips(original, patched)

        # Assert
        assert apply_ips(original, patch) == patched

    def test_a_run_starting_at_the_eof_offset_starts_one_byte_earlier(self):
        # Arrange
        original = bytes(EOF_OFFSET + 0x10)
        patched = edited(original, {EOF_OFFSET: b"\x01\x02"})

        # Act
        patch = make_ips(original, patched)

        # Assert
        assert patch[5:8] == (EOF_OFFSET - 1).to_bytes(3, "big")
        assert apply_ips(original, patch) == patched

    def test_a_run_longer_than_one_record_is_split(self):
        # Arrange
        original = bytes(0x20000)
        patched = edited(original, {0x100: b"\xff" * 0x10001})

        # Act
        patch = make_ips(original, patched)

        # Assert
        assert apply_ips(original, patch) == patched

    def test_an_edit_beyond_sixteen_mebibytes_is_refused(self):
        # Arrange
        original = bytes(0x1000010)
        patched = edited(original, {0x1000004: b"\x01"})

        # Act
        with pytest.raises(IpsError) as caught:
            make_ips(original, patched)

        # Assert
        assert str(caught.value) == "offset 0x1000004 is beyond what IPS can address, 0xFFFFFF"

    def test_data_of_different_sizes_is_refused(self):
        # Arrange
        original = bytes(0x100)

        # Act
        with pytest.raises(IpsError) as caught:
            make_ips(original, original + b"\x00")

        # Assert
        assert str(caught.value) == "IPS here only expresses edits that keep the ROM the same size"
