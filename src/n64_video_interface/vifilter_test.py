import struct

import pytest

from n64_video_interface import vifilter
from n64_video_interface.edits import Settings

SWAP_AT = 0x400
CAVE_AT = 0x200
CAVE_WORDS = 7
LOAD_CONTROL = 0x8E28000C
STORE_CONTROL = 0xADA80000
VI_BASE = 0x3C0DA440
NEXT = 0x8DEF5A20
NOP = 0


def words(*values):
    return b"".join(struct.pack(">I", value) for value in values)


def region(*, before=(LOAD_CONTROL,), after=NEXT, cave=CAVE_WORDS, base=VI_BASE):
    data = bytearray(0x800)
    swap = words(base, *before, STORE_CONTROL, after, NOP)
    data[SWAP_AT - 4 * (1 + len(before)) : SWAP_AT + 12] = swap
    data[CAVE_AT : CAVE_AT + 4 * cave] = words(*([0x24020001] * cave))
    return bytes(data)


def word(data, at):
    return struct.unpack_from(">I", data, at)[0]


def run_filter(data, ctrl):
    registers = {0: 0, 8: ctrl, 13: 0xA4400000}
    at, stop = CAVE_AT, None
    for _ in range(16):
        value = word(data, at)
        op, rs, rt = value >> 26, (value >> 21) & 31, (value >> 16) & 31
        if op == 0x0C:
            registers[rt] = registers[rs] & (value & 0xFFFF)
        elif op == 0x0D:
            registers[rt] = registers[rs] | (value & 0xFFFF)
        elif op == 0x0F:
            registers[rt] = (value & 0xFFFF) << 16
        elif op == 0x2B:
            return registers[rt]
        elif op == 0x04:
            stop = at + 4
        elif value & 0x3F == 0x25:
            registers[(value >> 11) & 31] = registers[rs] | registers[rt]
        else:
            registers[(value >> 11) & 31] = (registers[rt] << ((value >> 6) & 31)) & 0xFFFFFFFF
        at = stop if stop is not None else at + 4
    return None


class TestFindingTheStore:
    def test_the_control_store_after_its_load_is_found(self):
        # Arrange
        data = region()

        # Act
        sites = vifilter.find_sites(data)

        # Assert
        assert [(s.store, s.value, s.base) for s in sites] == [(SWAP_AT, 8, 13)]

    def test_a_call_between_the_load_and_the_store_does_not_hide_the_load(self):
        # Arrange
        data = region(before=(LOAD_CONTROL, 0x0C000100, NOP))

        # Act
        sites = vifilter.find_sites(data)

        # Assert
        assert [s.store for s in sites] == [SWAP_AT]

    @pytest.mark.parametrize(
        "shape",
        [
            {"before": (LOAD_CONTROL, 0x25080001)},
            {"before": (0x8E280010,)},
            {"after": 0x25080001},
            {"after": 0x1000FFFF},
            {"before": (LOAD_CONTROL, 0x35AD0004)},
            {"before": (LOAD_CONTROL, 0x10000003)},
            {"before": (0x01204025,)},
            {"base": NOP},
        ],
        ids=[
            "value changed",
            "other field",
            "value reused",
            "branch after",
            "base moved",
            "delay slot",
            "value from a register",
            "no base load",
        ],
    )
    def test_a_store_that_cannot_be_moved_safely_is_not_a_site(self, shape):
        # Arrange
        data = region(**shape)

        # Act
        sites = vifilter.find_sites(data)

        # Assert
        assert sites == ()


class TestTheFilter:
    @pytest.mark.parametrize(
        ("ctrl", "expected"),
        [(0x0001311E, 0x0000320A), (0x0001301F, 0x0000330B), (0x00003242, 0x00003242)],
        ids=["16-bit", "32-bit", "already sharp"],
    )
    def test_everything_off_forces_point_sampling_by_pixel_size(self, ctrl, expected):
        # Arrange
        data, _, _ = vifilter.install(
            region(), [(CAVE_AT, CAVE_AT + 4 * CAVE_WORDS)], vifilter.mask_for(ALL)
        )

        # Act
        written = run_filter(data, ctrl)

        # Assert
        assert written == expected

    @pytest.mark.parametrize(
        ("ctrl", "expected"),
        [(0x0001311E, 0x0000320A), (0x0001301F, 0x0000320B)],
        ids=["16-bit", "32-bit"],
    )
    def test_a_small_cave_still_turns_anti_aliasing_off_by_resampling(self, ctrl, expected):
        # Arrange
        data, _, _ = vifilter.install(
            region(cave=6), [(CAVE_AT, CAVE_AT + 24)], vifilter.mask_for(ALL)
        )

        # Act
        written = run_filter(data, ctrl)

        # Assert
        assert written == expected

    @pytest.mark.parametrize("target", [SWAP_AT, SWAP_AT + 4], ids=["store", "moved"])
    def test_a_store_or_moved_instruction_that_a_branch_reaches_is_not_a_site(self, target):
        # Arrange
        data = bytearray(region(after=0x8CCD0000))
        struct.pack_into(
            ">I", data, SWAP_AT + 0x20, 0x14C80000 | (((target - SWAP_AT - 0x24) >> 2) & 0xFFFF)
        )

        # Act
        sites = vifilter.find_sites(bytes(data))

        # Assert
        assert sites == ()

    def test_an_instruction_that_reuses_the_base_moves_after_the_store(self):
        # Arrange
        moved = 0x8CCD0000
        data = region(after=moved)

        # Act
        patched, _, _ = vifilter.install(data, [(CAVE_AT, CAVE_AT + 32)], vifilter.mask_for(ALL))

        # Assert
        assert (
            run_filter(patched, 0x0001311E),
            word(patched, SWAP_AT + 4),
            word(patched, CAVE_AT + 28),
        ) == (
            0x0000320A,
            NOP,
            moved,
        )

    def test_without_anti_aliasing_the_short_filter_only_clears_bits(self):
        # Arrange
        settings = Settings(divot=False, gamma_dither=False, dither_filter=False)
        data, _, _ = vifilter.install(
            region(cave=3), [(CAVE_AT, CAVE_AT + 12)], vifilter.mask_for(settings)
        )

        # Act
        written = run_filter(data, 0x0001311E)

        # Assert
        assert written == 0x0000310A

    def test_the_store_branches_to_the_cave_and_back(self):
        # Arrange
        data = region()

        # Act
        patched, changes, _ = vifilter.install(
            data, [(CAVE_AT, CAVE_AT + 4 * CAVE_WORDS)], vifilter.mask_for(ALL)
        )

        # Assert
        back = word(patched, CAVE_AT + 20)
        assert (
            word(patched, SWAP_AT) >> 16,
            vifilter.branch_target(SWAP_AT, word(patched, SWAP_AT)),
            vifilter.branch_target(CAVE_AT + 20, back),
            word(patched, CAVE_AT + 24),
            len(changes),
        ) == (0x1000, CAVE_AT, SWAP_AT + 8, STORE_CONTROL, 8)

    @pytest.mark.parametrize(
        "caves",
        [[], [(CAVE_AT, CAVE_AT + 12)], [(0x40000, 0x40000 + 28)]],
        ids=["no cave", "too small", "out of reach"],
    )
    def test_without_a_cave_that_fits_nothing_is_changed(self, caves):
        # Arrange
        data = region()

        # Act
        patched, changes, _ = vifilter.install(data, caves, vifilter.mask_for(ALL))

        # Assert
        assert (patched, changes) == (data, ())

    def test_a_cave_that_a_branch_reaches_is_not_used(self):
        # Arrange
        data = bytearray(region())
        struct.pack_into(">I", data, CAVE_AT - 8, 0x10000002)

        # Act
        patched, changes, _ = vifilter.install(
            bytes(data), [(CAVE_AT, CAVE_AT + 28)], vifilter.mask_for(ALL)
        )

        # Assert
        assert (patched, changes) == (bytes(data), ())

    def test_a_request_that_keeps_the_dedither_switch_installs_nothing(self):
        # Arrange
        mask = vifilter.mask_for(Settings(antialiasing=False))

        # Act
        patched, changes, _ = vifilter.install(region(), [(CAVE_AT, CAVE_AT + 28)], mask)

        # Assert
        assert (patched, changes) == (region(), ())


ALL = Settings(antialiasing=False, divot=False, gamma_dither=False, dither_filter=False)


class TestADistantBase:
    def test_a_base_loaded_far_back_in_straight_code_still_counts(self):
        # Arrange
        data = region(before=(*(NOP,) * 20, LOAD_CONTROL))

        # Act
        sites = vifilter.find_sites(data)

        # Assert
        assert [s.store for s in sites] == [SWAP_AT]

    def test_a_branch_between_a_distant_base_and_the_store_rules_it_out(self):
        # Arrange
        data = region(before=(*(NOP,) * 5, 0x10000001, *(NOP,) * 14, LOAD_CONTROL))

        # Act
        sites = vifilter.find_sites(data)

        # Assert
        assert sites == ()


class TestRelocatableCode:
    @pytest.mark.parametrize(
        "at", [SWAP_AT + 0x40, CAVE_AT + 0x40], ids=["near the store", "near the cave"]
    )
    def test_code_still_waiting_for_relocation_is_left_alone(self, at):
        # Arrange
        data = bytearray(region())
        struct.pack_into(">I", data, at, 0x3C180000)

        # Act
        patched, changes, _ = vifilter.install(
            bytes(data), [(CAVE_AT, CAVE_AT + 28)], vifilter.mask_for(ALL)
        )

        # Assert
        assert (patched, changes) == (bytes(data), ())

    def test_a_lui_with_a_real_upper_half_is_not_a_relocation(self):
        # Arrange
        data = bytearray(region())
        struct.pack_into(">I", data, SWAP_AT + 0x40, 0x3C188005)

        # Act
        relocatable = vifilter.relocatable(bytes(data), SWAP_AT)

        # Assert
        assert relocatable is False


class TestTheLoadEntry:
    def test_a_store_that_cannot_move_is_filtered_where_the_value_is_loaded(self):
        # Arrange
        loop_head = 0x8CCD0000
        data = bytearray(region(before=(LOAD_CONTROL, 0x3C028005, 0x3C018005), after=loop_head))
        struct.pack_into(">I", data, SWAP_AT + 0x10, 0x14C8FFFC)
        load = SWAP_AT - 12

        # Act
        patched, changes, _ = vifilter.install(
            bytes(data), [(CAVE_AT, CAVE_AT + 16)], vifilter.mask_for(ALL)
        )

        # Assert
        cave = [word(patched, CAVE_AT + 4 * i) for i in range(4)]
        assert (
            vifilter.branch_target(load, word(patched, load)),
            cave[0],
            cave[1] & 0xFFFF,
            vifilter.branch_target(CAVE_AT + 8, cave[2]),
            cave[3],
            word(patched, SWAP_AT),
            len(changes),
        ) == (CAVE_AT, LOAD_CONTROL, 0xFCEB, load + 8, 0x35080200, STORE_CONTROL, 5)

    def test_a_load_whose_neighbour_uses_the_value_is_not_an_entry(self):
        # Arrange
        data = bytearray(region(before=(LOAD_CONTROL, 0x25280001, 0x3C018005), after=0x8CCD0000))
        struct.pack_into(">I", data, SWAP_AT + 0x10, 0x14C8FFFC)

        # Act
        sites = vifilter.find_sites(bytes(data))

        # Assert
        assert sites == ()


ENTRY = 0x80000400


def booted_rom(jal_targets):
    data = bytearray(0x101000 + 0x100)
    data[:4] = bytes.fromhex("80371240")
    struct.pack_into(">I", data, 8, ENTRY)
    for index, target in enumerate(jal_targets):
        struct.pack_into(
            ">I",
            data,
            0x2000 + index * 4,
            0x0C000000 | ((ENTRY - 0x1000 + target) >> 2 & 0x3FFFFFF),
        )
        struct.pack_into(">I", data, target, 0x27BDFFE8)
    return data


class TestTheBootSegment:
    def test_a_base_that_lands_calls_on_function_starts_is_trusted(self):
        # Arrange
        data = booted_rom([0x4000 + 0x40 * i for i in range(20)])

        # Act
        base = vifilter.boot_base(bytes(data))

        # Assert
        assert base == ENTRY - 0x1000

    @pytest.mark.parametrize(
        "data",
        [bytes(0x400), bytes(booted_rom([0x4000 + 0x40 * i for i in range(10)]))],
        ids=["not a whole ROM", "too few calls"],
    )
    def test_a_base_without_enough_evidence_is_not_trusted(self, data):
        # Arrange
        given = data

        # Act
        base = vifilter.boot_base(given)

        # Assert
        assert base is None

    def test_a_boot_chip_that_moves_the_entry_point_is_recognised(self):
        # Arrange
        data = booted_rom([0x4000 + 0x40 * i for i in range(20)])
        struct.pack_into(">I", data, 8, ENTRY + 0x100000)

        # Act
        base = vifilter.boot_base(bytes(data))

        # Assert
        assert base == ENTRY - 0x1000

    def test_a_far_cave_is_reached_by_an_absolute_jump(self):
        # Arrange
        data = booted_rom([0x4000 + 0x40 * i for i in range(20)])
        store, cave = 0x80000, 0x2100
        words_at = (0x3C0DA440, LOAD_CONTROL, STORE_CONTROL, NEXT, NOP)
        for index, value in enumerate(words_at):
            struct.pack_into(">I", data, store - 8 + index * 4, value)

        # Act
        patched, _, installed = vifilter.install(
            bytes(data), [(cave, cave + 28)], vifilter.mask_for(ALL)
        )

        # Assert
        assert (installed, word(patched, store) >> 26, (word(patched, store) & 0x3FFFFFF) << 2) == (
            1,
            0x02,
            (ENTRY - 0x1000 + cave) & 0x0FFFFFFF,
        )

    def test_a_title_whose_integrity_check_rejects_code_edits_is_left_alone(self):
        # Arrange
        data = booted_rom([0x4000 + 0x40 * i for i in range(20)])
        data[0x3C:0x3E] = b"AL"
        for index, value in enumerate((0x3C0DA440, LOAD_CONTROL, STORE_CONTROL, NEXT, NOP)):
            struct.pack_into(">I", data, 0x2100 - 8 + index * 4, value)

        # Act
        patched, changes, installed = vifilter.install(
            bytes(data), [(0x2200, 0x2200 + 28)], vifilter.mask_for(ALL)
        )

        # Assert
        assert (patched, changes, installed) == (bytes(data), (), 0)
