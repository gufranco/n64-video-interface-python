import struct

import pytest

from n64_video_interface import features
from n64_video_interface.conftest import (
    ADDIU_AT,
    LUI_AT_ONE,
    MASK_IMMEDIATES,
    ROUTINE_AT,
    SWITCH_BLOCKS,
    routine,
)

LUI_V1_ONE = 0x3C030001


def reference_sites(rom):
    def is_mask(at, immediate):
        word = struct.unpack_from(">I", rom, at)[0]
        return word >> 26 == 0x09 and word & 0xFFFF == immediate

    sites = []
    for offset in range(0, len(rom) - 3, 4):
        if not is_mask(offset, 0xFFF7):
            continue
        masks = {"gamma": offset}
        for name, immediate in (("gamma_dither", 0xFFFB), ("divot", 0xFFEF), ("antialias", 0xFCFF)):
            last = masks[list(masks)[-1]]
            window = range(last + 4, min(len(rom) - 3, last + features.MASK_SEARCH_WINDOW), 4)
            hit = next((probe for probe in window if is_mask(probe, immediate)), None)
            if hit is None:
                break
            masks[name] = hit
        if len(masks) == 4:
            sites.append((offset, masks))
    return sites


def planted_masks(at, gap=8, length=None):
    blob = bytearray(at + 4 * gap + 4 if length is None else length)
    for index, immediate in enumerate(MASK_IMMEDIATES):
        struct.pack_into(">I", blob, at + index * gap, ADDIU_AT | immediate)
    return blob


def sites(rom):
    return [(site.offset, site.masks) for site in features.find_special_features(rom)]


class TestFindingTheRoutine:
    def test_the_four_masks_in_order_locate_the_routine(self):
        # Arrange
        rom = bytes(planted_masks(0x40, length=0x200))

        # Act
        found = sites(rom)

        # Assert
        assert found == [
            (0x40, {"gamma": 0x40, "gamma_dither": 0x48, "divot": 0x50, "antialias": 0x58})
        ]

    def test_a_routine_ending_on_the_last_word_is_found(self):
        # Arrange
        rom = bytes(planted_masks(0x40))[: 0x40 + 3 * 8 + 4]

        # Act
        found = sites(rom)

        # Assert
        assert [offset for offset, _ in found] == [0x40]

    def test_masks_too_far_apart_are_not_a_routine(self):
        # Arrange
        rom = bytes(planted_masks(0x40, gap=0x800))

        # Act
        found = sites(rom)

        # Assert
        assert found == []

    def test_the_immediates_without_the_addiu_opcode_are_not_a_routine(self):
        # Arrange
        blob = bytearray(0x200)
        for index, immediate in enumerate(MASK_IMMEDIATES):
            struct.pack_into(">I", blob, 0x40 + index * 8, immediate)

        # Act
        found = sites(bytes(blob))

        # Assert
        assert found == []

    @pytest.mark.parametrize(
        "rom",
        [
            bytes(0x1000),
            bytes(b"\x24\x01" + bytes(planted_masks(0x40, length=0x200))[2:]),
            bytes(((i * 2654435761) >> 13) & 0xFF for i in range(0x8000)),
        ],
        ids=["empty", "unaligned lookalike", "noise"],
    )
    def test_the_scan_agrees_with_an_exhaustive_one(self, rom):
        # Arrange
        expected = reference_sites(rom)

        # Act
        found = sites(rom)

        # Assert
        assert found == expected


class TestFindingSwitchOnRequests:
    @pytest.mark.parametrize("name", list(SWITCH_BLOCKS))
    def test_each_switch_on_block_is_found_by_its_flag(self, name):
        # Arrange
        blob, placed = routine(list(SWITCH_BLOCKS))

        # Act
        found = features.find_switch_requests(blob, name)

        # Assert
        assert [(r.guard, r.branch, r.setter) for r in found] == [placed[name]]

    @pytest.mark.parametrize(
        "block",
        [{"setter_word": LUI_V1_ONE}, {"branch_op": 0x14}, {"guard_gap": 6}],
        ids=["setter in another register", "branch likely", "guard in an earlier slot"],
    )
    def test_compiler_variations_of_the_dedither_block_are_found(self, block):
        # Arrange
        blob, placed = routine(("dedither",), **block)

        # Act
        found = features.find_switch_requests(blob, "dedither")

        # Assert
        assert [r.guard for r in found] == [placed["dedither"][0]]

    @pytest.mark.parametrize(
        "block", [{"flag": 0x4000}, {"skip": 1}], ids=["other flag", "branch short of setter"]
    )
    def test_a_block_that_is_not_the_dedither_request_is_ignored(self, block):
        # Arrange
        blob, _ = routine(("dedither",), **block)

        # Act
        found = features.find_switch_requests(blob, "dedither")

        # Assert
        assert found == ()

    def test_a_setter_without_the_routine_is_ignored(self):
        # Arrange
        blob = bytearray(0x400)
        struct.pack_into(">I", blob, 0x80, LUI_AT_ONE)

        # Act
        found = features.find_switch_requests(bytes(blob), "dedither")

        # Assert
        assert found == ()

    def test_a_branch_whose_register_no_andi_wrote_is_not_a_guard(self):
        # Arrange
        blob = bytearray(routine(("dedither",))[0])
        guard, _, _ = routine(("dedither",))[1]["dedither"]
        struct.pack_into(">I", blob, guard, 0)

        # Act
        found = features.find_switch_requests(bytes(blob), "dedither")

        # Assert
        assert found == ()

    def test_the_routine_found_is_reported_at_its_first_mask(self):
        # Arrange
        blob, _ = routine(("dedither",))

        # Act
        found = features.find_special_features(blob)

        # Assert
        assert [site.offset for site in found] == [ROUTINE_AT]
