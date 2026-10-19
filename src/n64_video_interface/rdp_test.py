import struct

import pytest

from n64_video_interface import rdp

TEXT_BASE = 0x40000
IMEM_BASE = 0x1080
RDP_HANDLER_IMEM = 0x1210
CAVE_IMEM = 0x11C0
SETOM_IMEM = 0x1240
MATH_IMEM = 0x1400


def jump(imem):
    return (0x02 << 26) | (imem >> 2)


def rom_at(imem):
    return TEXT_BASE + imem - IMEM_BASE


DMA_IO_HANDLER = (
    0x0C000489,
    0x877409C1,
    0x33330FF8,
    0x0014A083,
    0x080007F6,
    0x201F1190,
)

RDP_HANDLER = (0xAEF80004, 0xAEF90000, 0x0800047A, 0x22F70008)

OTHERMODE = (
    0x8D63EF8C,
    0x3C028000,
    0x03221007,
    0x00190A02,
    0x00221006,
    0x00401027,
    0x00621824,
    0x00781825,
    0xAD63EF8C,
    0x8C1900C8,
    jump(RDP_HANDLER_IMEM),
    0x8C1800CC,
)

SETOTHERMODE = (0xAC1900C8, jump(RDP_HANDLER_IMEM), 0xAC1800CC)


def place(blob, imem, words):
    for index, value in enumerate(words):
        struct.pack_into(">I", blob, rom_at(imem) + index * 4, value)


def microcode_rom(*, cave=DMA_IO_HANDLER):
    blob = bytearray(TEXT_BASE + 0x2000)
    place(blob, CAVE_IMEM - 4, (0x24020001,))
    place(blob, CAVE_IMEM, cave)
    place(blob, RDP_HANDLER_IMEM, RDP_HANDLER)
    place(blob, SETOM_IMEM, SETOTHERMODE)
    place(blob, MATH_IMEM, OTHERMODE)
    return bytes(blob)


def word(data, at):
    return struct.unpack_from(">I", data, at)[0]


class TestFindingTheOthermodeSends:
    def test_a_microcode_with_both_sends_and_a_spare_handler_is_found(self):
        # Arrange
        rom = microcode_rom()

        # Act
        sites = rdp.find_dither_sites(rom)

        # Assert
        assert [(s.math_jump, s.setom_jump, s.cave, s.cave_imem) for s in sites] == [
            (rom_at(MATH_IMEM) + 40, rom_at(SETOM_IMEM) + 4, rom_at(CAVE_IMEM), CAVE_IMEM)
        ]

    def test_a_spare_handler_that_does_not_match_in_full_is_not_used(self):
        # Arrange
        rom = microcode_rom(cave=(*DMA_IO_HANDLER[:3], 0x00000000, *DMA_IO_HANDLER[4:]))

        # Act
        sites = rdp.find_dither_sites(rom)

        # Assert
        assert sites == ()

    @pytest.mark.parametrize("index", [9, 11])
    def test_a_send_that_does_not_reload_both_othermode_words_is_not_a_site(self, index):
        # Arrange
        blob = bytearray(microcode_rom())
        struct.pack_into(">I", blob, rom_at(MATH_IMEM) + index * 4, 0)

        # Act
        sites = rdp.find_dither_sites(bytes(blob))

        # Assert
        assert sites == ()

    def test_an_unaligned_copy_of_the_routine_is_ignored(self):
        # Arrange
        blob = bytearray(0x200)
        blob[0x42 : 0x42 + len(rdp.OTHERMODE_MATH)] = rdp.OTHERMODE_MATH

        # Act
        sites = rdp.find_dither_sites(bytes(blob))

        # Assert
        assert sites == ()

    def test_handlers_too_far_apart_for_one_microcode_are_not_combined(self):
        # Arrange
        blob = bytearray(microcode_rom())
        far = rom_at(CAVE_IMEM) - 0x1100
        blob[far : far + 24] = blob[rom_at(CAVE_IMEM) : rom_at(CAVE_IMEM) + 24]
        blob[rom_at(CAVE_IMEM) : rom_at(CAVE_IMEM) + 24] = bytes(24)

        # Act
        sites = rdp.find_dither_sites(bytes(blob))

        # Assert
        assert sites == ()

    def test_data_without_a_microcode_has_no_site(self):
        # Arrange
        rom = bytes(0x4000)

        # Act
        sites = rdp.find_dither_sites(rom)

        # Assert
        assert sites == ()


class TestForcingTheDitherOff:
    def test_both_sends_jump_to_a_stub_that_disables_both_dithers(self):
        # Arrange
        rom = microcode_rom()

        # Act
        out, changes = rdp.disable_dither(rom)

        # Assert
        assert (
            word(out, rom_at(MATH_IMEM) + 40),
            word(out, rom_at(SETOM_IMEM) + 4),
            word(out, rom_at(CAVE_IMEM)),
            word(out, rom_at(CAVE_IMEM) + 4),
            len(changes),
        ) == (jump(CAVE_IMEM), jump(CAVE_IMEM), jump(RDP_HANDLER_IMEM), 0x373900F0, 4)

    def test_nothing_outside_the_four_words_moves(self):
        # Arrange
        rom = microcode_rom()

        # Act
        out, changes = rdp.disable_dither(rom)

        # Assert
        moved = {at for at in range(0, len(rom), 4) if word(rom, at) != word(out, at)}
        assert moved == {offset for offset, _, _ in changes}

    def test_a_patched_microcode_is_not_patched_again(self):
        # Arrange
        once, _ = rdp.disable_dither(microcode_rom())

        # Act
        twice, changes = rdp.disable_dither(once)

        # Assert
        assert (twice, changes) == (once, ())

    @pytest.mark.parametrize("bits", [0xC0, 0x30])
    def test_color_and_alpha_dither_can_be_turned_off_alone(self, bits):
        # Arrange
        rom = microcode_rom()

        # Act
        out, _ = rdp.disable_dither(rom, bits=bits)

        # Assert
        assert word(out, rom_at(CAVE_IMEM) + 4) == 0x37390000 | bits


def sealed_rom_with_microcode():
    from n64_video_interface import vi
    from n64_video_interface.conftest import make_rom

    data = bytearray(make_rom(size=vi.CHECKSUM_END + 0x4000))
    blob = microcode_rom()
    data[TEXT_BASE : TEXT_BASE + 0x2000] = blob[TEXT_BASE:]
    return vi.reseal(bytes(data), "6102")


class TestSafePatchTurnsTheRdpDitherOff:
    @pytest.mark.parametrize(
        ("settings", "bits", "label"),
        [
            ({"rdp_color_dither": False}, 0xC0, "colour"),
            ({"rdp_alpha_dither": False}, 0x30, "alpha"),
            ({"rdp_color_dither": False, "rdp_alpha_dither": False}, 0xF0, "colour and alpha"),
        ],
    )
    def test_each_dither_is_forced_off_in_the_microcode(self, settings, bits, label):
        # Arrange
        from n64_video_interface import vi

        rom = sealed_rom_with_microcode()

        # Act
        result = vi.safe_patch(rom, **settings)

        # Assert
        assert (result.applied, result.reason, word(result.data, rom_at(CAVE_IMEM) + 4)) == (
            True,
            f"{label} dither forced off in 1 microcode",
            0x37390000 | bits,
        )

    def test_a_rom_without_a_supported_microcode_is_refused(self):
        # Arrange
        from n64_video_interface import vi
        from n64_video_interface.conftest import make_rom

        rom = vi.reseal(make_rom(size=vi.CHECKSUM_END + 0x4000), "6102")

        # Act
        result = vi.safe_patch(rom, rdp_color_dither=False)

        # Assert
        assert (result.applied, result.reason) == (
            False,
            "no supported microcode was found, so the RDP dither cannot be changed",
        )

    def test_the_dither_can_only_be_switched_off(self):
        # Arrange
        from n64_video_interface import vi

        rom = sealed_rom_with_microcode()

        # Act
        result = vi.safe_patch(rom, rdp_color_dither=True)

        # Assert
        assert (result.applied, result.reason) == (
            False,
            "the RDP dither can be switched off here, never back on",
        )


GBI1_ROUTINE = (
    0x0900049E,
    0x23A70008,
    0x23A7000C,
    0x8CE30000,
    0x9365FFFB,
    0x9366FFFA,
    0x20020001,
    0x00A21004,
    0x2042FFFF,
    0x00C21004,
    0x00401027,
    0x00431024,
    0x00581825,
    0xACE30000,
    0x8FB90008,
    0x090004DB,
    0x8FB8000C,
)
GBI1_AT = 0x200


def gbi1_rom():
    blob = bytearray(0x400)
    for index, value in enumerate(GBI1_ROUTINE):
        struct.pack_into(">I", blob, GBI1_AT + index * 4, value)
    return bytes(blob)


class TestTheOlderMicrocodeFamily:
    def test_the_shared_othermode_routine_is_found(self):
        # Arrange
        rom = gbi1_rom()

        # Act
        sites = rdp.find_inline_sites(rom)

        # Assert
        assert [(site.at, site.family) for site in sites] == [(GBI1_AT + 12, "F3DEX")]

    def test_the_routine_is_rewritten_to_force_the_dither_off_in_the_same_space(self):
        # Arrange
        rom = gbi1_rom()

        # Act
        out, changes = rdp.disable_dither(rom)

        # Assert
        assert [word(out, GBI1_AT + index * 4) for index in range(len(GBI1_ROUTINE))] == [
            0x0900049E,
            0x23A70008,
            0x23A7000C,
            0x8CE30000,
            0x00193202,
            0x20020001,
            0x03221004,
            0x2042FFFF,
            0x00C21004,
            0x00401027,
            0x00431024,
            0x00581825,
            0xACE30000,
            0x8FB90008,
            0x373900F0,
            0x090004DB,
            0x8FB8000C,
        ]
        assert len(changes) == 11

    def test_a_rewritten_routine_is_left_alone(self):
        # Arrange
        once, _ = rdp.disable_dither(gbi1_rom())

        # Act
        twice, changes = rdp.disable_dither(once)

        # Assert
        assert (twice, changes) == (once, ())

    def test_a_routine_with_a_different_tail_is_not_rewritten(self):
        # Arrange
        blob = bytearray(gbi1_rom())
        struct.pack_into(">I", blob, GBI1_AT + 16 * 4, 0)

        # Act
        sites = rdp.find_inline_sites(bytes(blob))

        # Assert
        assert sites == ()


FAST3D_ROUTINE = (
    0x090004B4,
    0x23A70008,
    0x23A7000C,
    0x8CE30000,
    0x2008FFFF,
    0x9365FFFB,
    0x9366FFFA,
    0x20020001,
    0x00A21004,
    0x2042FFFF,
    0x00C21004,
    0x00481026,
    0x00431024,
    0x00581825,
    0xACE30000,
    0x8FB90008,
    0x090004EA,
    0x8FB8000C,
)


def fast3d_rom():
    blob = bytearray(0x400)
    for index, value in enumerate(FAST3D_ROUTINE):
        struct.pack_into(">I", blob, GBI1_AT + index * 4, value)
    return bytes(blob)


class TestTheFast3dFamily:
    def test_the_routine_is_found_as_fast3d(self):
        # Arrange
        rom = fast3d_rom()

        # Act
        sites = rdp.find_inline_sites(rom)

        # Assert
        assert [(site.at, site.family) for site in sites] == [(GBI1_AT + 12, "Fast3D")]

    def test_the_routine_is_rewritten_with_nor_and_the_dither_forced_off(self):
        # Arrange
        rom = fast3d_rom()

        # Act
        out, _ = rdp.disable_dither(rom, bits=0xC0)

        # Assert
        assert [word(out, GBI1_AT + index * 4) for index in range(len(FAST3D_ROUTINE))] == [
            0x090004B4,
            0x23A70008,
            0x23A7000C,
            0x8CE30000,
            0x00193202,
            0x20020001,
            0x03221004,
            0x2042FFFF,
            0x00C21004,
            0x00401027,
            0x00431024,
            0x00581825,
            0xACE30000,
            0x8FB90008,
            0x373900C0,
            0x00000000,
            0x090004EA,
            0x8FB8000C,
        ]
