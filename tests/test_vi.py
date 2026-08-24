import struct

import pytest

from n64_video_interface import vi

NTSC_LAN1_CTRL = 0x0000311E


def mode_entry(ctrl=NTSC_LAN1_CTRL, width=320, vsync=525, hsync=3093):
    """A VI mode struct as libultra lays it out: type, then nine comRegs words."""
    return struct.pack(
        ">IIIIIIIIII",
        0x00000001,
        ctrl,
        width,
        0x03E52239,
        vsync,
        hsync,
        0x0C150C15,
        0x006C02EC,
        0x00000200,
        0x00000000,
    )


class TestDecodeCtrl:
    def test_reads_every_documented_field(self):
        d = vi.decode_ctrl(0x0000311E)

        assert d.pixel_type == 2
        assert d.gamma_dither is True
        assert d.gamma is True
        assert d.divot is True
        assert d.aa_mode == 1
        assert d.dither_filter is False

    def test_recognises_anti_aliasing_as_enabled(self):
        assert vi.decode_ctrl(0x0000311E).antialiasing is True
        assert vi.decode_ctrl(0x00003000).antialiasing is True

    def test_recognises_anti_aliasing_as_disabled(self):
        assert vi.decode_ctrl(0x0000321E).antialiasing is False
        assert vi.decode_ctrl(0x0000331E).antialiasing is False

    def test_reads_the_dither_filter_bit(self):
        assert vi.decode_ctrl(0x0001311E).dither_filter is True

    def test_reports_the_serrate_bit(self):
        assert vi.decode_ctrl(0x0000315E).serrate is True

    def test_names_the_aa_mode(self):
        assert "AA_NEEDED" in vi.decode_ctrl(0x0000311E).aa_name
        assert "RESAMPLE" in vi.decode_ctrl(0x0000321E).aa_name

    def test_describes_what_blurs_the_picture(self):
        d = vi.decode_ctrl(0x0001311E)

        assert "dither filter" in d.blur_sources
        assert "divot" in d.blur_sources

    def test_a_clean_value_has_no_blur_sources(self):
        assert vi.decode_ctrl(0x00003202).blur_sources == ()


class TestClearBits:
    def test_clears_the_dither_filter_only(self):
        out = vi.apply_changes(0x0001311E, dither_filter=False)

        assert vi.decode_ctrl(out).dither_filter is False
        assert vi.decode_ctrl(out).divot is True

    def test_clears_divot_only(self):
        out = vi.apply_changes(0x0000311E, divot=False)

        assert vi.decode_ctrl(out).divot is False
        assert vi.decode_ctrl(out).gamma_dither is True

    def test_disables_anti_aliasing_by_setting_resample(self):
        out = vi.apply_changes(0x0000311E, antialiasing=False)

        assert vi.decode_ctrl(out).aa_mode == 2
        assert vi.decode_ctrl(out).antialiasing is False

    def test_leaves_the_pixel_type_untouched(self):
        out = vi.apply_changes(0x0000311F, antialiasing=False, divot=False)

        assert vi.decode_ctrl(out).pixel_type == 3

    def test_no_request_changes_nothing(self):
        assert vi.apply_changes(0x0000311E) == 0x0000311E

    def test_can_turn_a_feature_back_on(self):
        out = vi.apply_changes(0x00003202, divot=True)

        assert vi.decode_ctrl(out).divot is True


class TestFindModeTables:
    def test_finds_a_planted_mode_entry(self):
        rom = bytes(0x400) + mode_entry() + bytes(0x400)

        found = vi.find_mode_tables(rom)

        assert len(found) == 1
        assert found[0].ctrl == NTSC_LAN1_CTRL

    def test_reports_where_the_ctrl_word_lives(self):
        rom = bytes(0x400) + mode_entry() + bytes(0x400)

        assert vi.find_mode_tables(rom)[0].ctrl_offset == 0x404

    def test_records_the_video_standard(self):
        ntsc = vi.find_mode_tables(bytes(16) + mode_entry(vsync=525))
        pal = vi.find_mode_tables(bytes(16) + mode_entry(vsync=625))

        assert ntsc[0].standard == "NTSC"
        assert pal[0].standard == "PAL"

    def test_records_the_declared_width(self):
        found = vi.find_mode_tables(bytes(16) + mode_entry(width=640))

        assert found[0].width == 640

    def test_finds_several_entries_in_one_rom(self):
        rom = bytes(0x100) + mode_entry() + mode_entry(width=640) + bytes(0x100)

        assert len(vi.find_mode_tables(rom)) == 2

    def test_ignores_a_plausible_ctrl_with_an_implausible_width(self):
        rom = bytes(16) + mode_entry(width=12345)

        assert vi.find_mode_tables(rom) == ()

    def test_ignores_a_plausible_ctrl_with_an_implausible_vsync(self):
        rom = bytes(16) + mode_entry(vsync=999)

        assert vi.find_mode_tables(rom) == ()

    def test_finds_nothing_in_empty_data(self):
        assert vi.find_mode_tables(bytes(4096)) == ()

    def test_tolerates_data_shorter_than_one_entry(self):
        assert vi.find_mode_tables(b"\x00\x00\x31\x1e") == ()


class TestFindSpecialFeatures:
    def test_finds_the_library_routine_by_its_constants(self):
        blob = bytearray(0x200)
        for i, imm in enumerate((0xFFF7, 0xFFFB, 0xFFEF, 0xFCFF)):
            struct.pack_into(">I", blob, 0x40 + i * 8, 0x24010000 | imm)

        found = vi.find_special_features(bytes(blob))

        assert len(found) == 1
        assert found[0].offset == 0x40

    def test_records_where_each_mask_sits(self):
        blob = bytearray(0x200)
        for i, imm in enumerate((0xFFF7, 0xFFFB, 0xFFEF, 0xFCFF)):
            struct.pack_into(">I", blob, 0x40 + i * 8, 0x24010000 | imm)

        site = vi.find_special_features(bytes(blob))[0]

        assert site.masks["gamma"] == 0x40
        assert site.masks["antialias"] == 0x58

    def test_requires_the_constants_to_be_close_together(self):
        blob = bytearray(0x4000)
        for i, imm in enumerate((0xFFF7, 0xFFFB, 0xFFEF, 0xFCFF)):
            struct.pack_into(">I", blob, 0x40 + i * 0x800, 0x24010000 | imm)

        assert vi.find_special_features(bytes(blob)) == ()

    def test_requires_the_addiu_opcode_not_just_the_immediate(self):
        blob = bytearray(0x200)
        for i, imm in enumerate((0xFFF7, 0xFFFB, 0xFFEF, 0xFCFF)):
            struct.pack_into(">I", blob, 0x40 + i * 8, 0x00000000 | imm)

        assert vi.find_special_features(bytes(blob)) == ()

    def test_finds_nothing_in_empty_data(self):
        assert vi.find_special_features(bytes(4096)) == ()


class TestAudit:
    def test_summarises_a_rom_with_a_mode_table(self):
        rom = bytes(0x400) + mode_entry() + bytes(0x400)

        report = vi.audit(rom)

        assert report.mode_count == 1
        assert report.antialiasing_on == 1

    def test_counts_how_many_modes_blur(self):
        rom = bytes(0x100) + mode_entry(ctrl=0x0001311E) + mode_entry(ctrl=0x00003202)

        report = vi.audit(rom)

        assert report.dither_filter_on == 1
        assert report.divot_on == 1

    def test_reports_a_rom_with_no_recognisable_table(self):
        report = vi.audit(bytes(4096))

        assert report.mode_count == 0
        assert report.patchable is False

    def test_a_rom_with_a_table_is_patchable(self):
        report = vi.audit(bytes(16) + mode_entry())

        assert report.patchable is True

    def test_lists_the_distinct_ctrl_values(self):
        rom = bytes(0x100) + mode_entry(ctrl=0x0000311E) + mode_entry(ctrl=0x0000311E)

        assert vi.audit(rom).ctrl_values == (0x0000311E,)


class TestPatch:
    def test_rewrites_every_mode_entry(self):
        rom = bytes(0x100) + mode_entry(ctrl=0x0001311E) + bytes(0x100)

        out, changed = vi.patch(rom, dither_filter=False)

        assert changed == 1
        assert vi.audit(out).dither_filter_on == 0

    def test_leaves_the_rest_of_the_rom_byte_identical(self):
        rom = bytes(0x100) + mode_entry(ctrl=0x0001311E) + bytes(0x100)

        out, _ = vi.patch(rom, dither_filter=False)
        site = vi.find_mode_tables(rom)[0].ctrl_offset

        assert out[:site] == rom[:site]
        assert out[site + 4 :] == rom[site + 4 :]

    def test_changes_nothing_when_no_request_is_made(self):
        rom = bytes(0x100) + mode_entry() + bytes(0x100)

        out, changed = vi.patch(rom)

        assert out == rom
        assert changed == 0

    def test_reports_zero_changes_when_the_bits_are_already_right(self):
        rom = bytes(0x100) + mode_entry(ctrl=0x00003202) + bytes(0x100)

        _, changed = vi.patch(rom, dither_filter=False, divot=False)

        assert changed == 0

    def test_preserves_the_rom_length(self):
        rom = bytes(0x100) + mode_entry() + bytes(0x100)

        out, _ = vi.patch(rom, antialiasing=False)

        assert len(out) == len(rom)


class TestScannerEquivalence:
    """The fast path must agree with an exhaustive scan, byte for byte."""

    def reference(self, rom):
        import struct as _s

        out = []
        for off in range(0, max(0, len(rom) - 16), 4):
            ctrl = _s.unpack_from(">I", rom, off)[0]
            if not vi._plausible_ctrl(ctrl):
                continue
            if _s.unpack_from(">I", rom, off + 4)[0] not in vi.PLAUSIBLE_WIDTHS:
                continue
            if _s.unpack_from(">I", rom, off + 12)[0] not in vi.VSYNC_BY_STANDARD:
                continue
            out.append(off)
        return out

    def test_agrees_on_a_planted_table(self):
        rom = bytes(0x100) + mode_entry() + mode_entry(width=640, vsync=625) + bytes(0x100)

        assert [m.ctrl_offset for m in vi.find_mode_tables(rom)] == self.reference(rom)

    def test_agrees_when_an_entry_sits_at_the_very_start(self):
        rom = mode_entry() + bytes(0x100)

        assert [m.ctrl_offset for m in vi.find_mode_tables(rom)] == self.reference(rom)

    def test_agrees_when_an_entry_sits_at_the_very_end(self):
        rom = bytes(0x100) + mode_entry()

        assert [m.ctrl_offset for m in vi.find_mode_tables(rom)] == self.reference(rom)

    def test_agrees_on_data_with_no_entries(self):
        rom = bytes(0x800)

        assert [m.ctrl_offset for m in vi.find_mode_tables(rom)] == self.reference(rom)

    def test_agrees_on_noisy_data(self):
        rom = bytes(((i * 31 + 7) & 0xFF) for i in range(0x4000))

        assert [m.ctrl_offset for m in vi.find_mode_tables(rom)] == self.reference(rom)

    def test_ignores_an_unaligned_vsync_match(self):
        rom = bytes(2) + mode_entry() + bytes(0x100)
        found = [m.ctrl_offset for m in vi.find_mode_tables(rom)]

        assert found == self.reference(rom)
        assert all(o % 4 == 0 for o in found)


class TestSafePatch:
    """The guarded entry point. Refuses anything it cannot prove is correct."""

    def rom_with_table(self, ctrl=0x0000311E):
        from tests.conftest import make_rom

        base = bytearray(make_rom(size=vi.CHECKSUM_END + 0x2000))
        base[0x2000 : 0x2000 + len(mode_entry(ctrl=ctrl))] = mode_entry(ctrl=ctrl)
        return vi.reseal(bytes(base))

    def test_a_sealed_rom_starts_valid(self):
        from n64_video_interface import checksum

        ok, cic = checksum.verify(self.rom_with_table())

        assert ok is True
        assert cic is not None

    def test_refuses_a_rom_with_no_mode_table(self):
        from tests.conftest import make_rom

        result = vi.safe_patch(
            vi.reseal(make_rom(size=vi.CHECKSUM_END + 0x2000)), antialiasing=False
        )

        assert result.applied is False
        assert "no video mode table" in result.reason

    def test_refuses_when_no_change_is_requested(self):
        result = vi.safe_patch(self.rom_with_table())

        assert result.applied is False
        assert "nothing requested" in result.reason

    def test_refuses_a_rom_whose_checksum_does_not_validate(self):
        broken = bytearray(self.rom_with_table())
        broken[0x10] ^= 0xFF

        result = vi.safe_patch(bytes(broken), antialiasing=False)

        assert result.applied is False
        assert "checksum" in result.reason

    def test_applies_the_requested_change(self):
        result = vi.safe_patch(self.rom_with_table(), antialiasing=False)

        assert result.applied is True
        assert result.modes_changed == 1

    def test_the_result_carries_a_valid_checksum(self):
        from n64_video_interface import checksum

        result = vi.safe_patch(self.rom_with_table(), antialiasing=False)
        ok, _ = checksum.verify(result.data)

        assert ok is True

    def test_the_boot_chip_is_unchanged_by_patching(self):
        from n64_video_interface import checksum

        original = self.rom_with_table()
        _, before = checksum.verify(original)
        result = vi.safe_patch(original, antialiasing=False)
        _, after = checksum.verify(result.data)

        assert before == after

    def test_the_intended_bit_actually_changed(self):
        result = vi.safe_patch(self.rom_with_table(), antialiasing=False)

        assert vi.audit(result.data).antialiasing_on == 0

    def test_nothing_outside_the_ctrl_words_and_checksum_moved(self):
        original = self.rom_with_table()
        result = vi.safe_patch(original, antialiasing=False)

        allowed = {0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17}
        allowed |= set(range(0x2004, 0x2008))
        differing = {i for i in range(len(original)) if original[i] != result.data[i]}

        assert differing <= allowed

    def test_preserves_the_rom_length(self):
        original = self.rom_with_table()

        assert len(vi.safe_patch(original, antialiasing=False).data) == len(original)

    def test_reports_every_change_it_made(self):
        result = vi.safe_patch(self.rom_with_table(), antialiasing=False, divot=False)

        assert len(result.changes) == 1
        before, after = result.changes[0][1], result.changes[0][2]
        assert vi.decode_ctrl(before).antialiasing is True
        assert vi.decode_ctrl(after).antialiasing is False
        assert vi.decode_ctrl(after).divot is False

    def test_is_a_no_op_when_the_bits_are_already_right(self):
        result = vi.safe_patch(
            self.rom_with_table(ctrl=0x00003202), antialiasing=False, divot=False
        )

        assert result.applied is False
        assert "already" in result.reason

    def test_the_dither_filter_can_only_be_switched_off(self):
        """Switching it back on would mean restoring an instruction this code never
        wrote, and asking for a filter a ROM cannot reach means nothing."""
        result = vi.safe_patch(self.rom_with_table(), dither_filter=True)

        assert result.applied is False
        assert "never back on" in result.reason

    def test_a_rom_that_cannot_reach_the_filter_is_reported_as_already_off(self):
        result = vi.safe_patch(self.rom_with_table(), dither_filter=False)

        assert result.applied is False
        assert "already off" in result.reason

    def test_the_dither_filter_needs_no_mode_table(self):
        """GoldenEye 007 carries the routine and no table, so requiring one would
        exclude exactly the ROMs that most need this."""
        from tests.conftest import make_rom

        base = bytearray(make_rom(size=vi.CHECKSUM_END + 0x2000))
        blob, _, _, _ = rom_with_dither_request()
        base[0x2000 : 0x2000 + len(blob)] = blob
        rom = vi.reseal(bytes(base))

        result = vi.safe_patch(rom, dither_filter=False)

        assert vi.find_mode_tables(rom) == ()
        assert result.applied is True
        assert "dedither request neutralised" in result.reason

    def test_the_result_still_verifies_after_a_dither_only_patch(self):
        from tests.conftest import make_rom

        from n64_video_interface import checksum

        base = bytearray(make_rom(size=vi.CHECKSUM_END + 0x2000))
        blob, _, _, _ = rom_with_dither_request()
        base[0x2000 : 0x2000 + len(blob)] = blob
        rom = vi.reseal(bytes(base))

        result = vi.safe_patch(rom, dither_filter=False)

        assert checksum.verify(result.data)[0] is True
        assert vi.find_dither_requests(result.data) == ()

    def test_patching_twice_is_stable(self):
        once = vi.safe_patch(self.rom_with_table(), antialiasing=False).data
        twice = vi.safe_patch(once, antialiasing=False)

        assert twice.applied is False


class TestReseal:
    def test_writes_a_checksum_that_validates(self):
        from tests.conftest import make_rom

        from n64_video_interface import checksum

        sealed = vi.reseal(make_rom(size=vi.CHECKSUM_END + 0x1000))

        assert checksum.verify(sealed)[0] is True

    def test_only_touches_the_two_checksum_words(self):
        from tests.conftest import make_rom

        original = make_rom(size=vi.CHECKSUM_END + 0x1000)
        sealed = vi.reseal(original)
        differing = {i for i in range(len(original)) if original[i] != sealed[i]}

        assert differing <= set(range(0x10, 0x18))

    def test_refuses_data_too_short_to_checksum(self):
        with pytest.raises(ValueError, match="too short"):
            vi.reseal(bytes(0x100))


class TestEmitIps:
    def test_produces_a_valid_ips_header_and_terminator(self):
        patch = vi.make_ips([(0x1000, 0x0000311E, 0x0000320E)])

        assert patch.startswith(b"PATCH")
        assert patch.endswith(b"EOF")

    def test_encodes_one_record_per_change(self):
        patch = vi.make_ips([(0x1000, 0, 0x11223344), (0x2000, 0, 0x55667788)])

        assert patch.count(b"\x00\x04") == 2

    def test_writes_the_new_value_big_endian(self):
        patch = vi.make_ips([(0x001000, 0, 0x0000320E)])

        assert b"\x00\x00\x32\x0e" in patch

    def test_encodes_the_offset_as_three_bytes(self):
        patch = vi.make_ips([(0x123456, 0, 1)])

        assert b"\x12\x34\x56" in patch

    def test_an_empty_change_list_still_produces_a_valid_patch(self):
        assert vi.make_ips([]) == b"PATCHEOF"

    def test_refuses_an_offset_ips_cannot_address(self):
        with pytest.raises(ValueError, match="beyond"):
            vi.make_ips([(0x1000000, 0, 1)])

    def test_applying_the_patch_reproduces_the_patched_rom(self):
        from tests.conftest import make_rom

        base = bytearray(make_rom(size=vi.CHECKSUM_END + 0x2000))
        base[0x2000 : 0x2000 + len(mode_entry())] = mode_entry()
        original = vi.reseal(bytes(base))
        result = vi.safe_patch(original, antialiasing=False)

        patch = vi.make_ips(result.changes, checksum_words=result.data)
        rebuilt = bytearray(original)
        i = 5
        while patch[i : i + 3] != b"EOF":
            off = int.from_bytes(patch[i : i + 3], "big")
            i += 3
            size = int.from_bytes(patch[i : i + 2], "big")
            i += 2
            rebuilt[off : off + size] = patch[i : i + size]
            i += size

        assert bytes(rebuilt) == result.data

    def test_includes_the_resealed_checksum_when_asked(self):
        patch = vi.make_ips([(0x2004, 0, 1)], checksum_words=bytes(0x20))

        assert b"\x00\x00\x10" in patch


def dither_block(base, *, flag=0x0040, branch_op=0x04, guard_gap=1, skip=6):
    """A minimal OS_VI_DITHER_FILTER_ON block laid out like a compiled one.

    andi at base, the guard; a branch on that register clearing the setter; then
    `lui at, 1` which is the instruction that puts bit 16 in place.
    """
    words = {}
    guard = base
    branch = base + guard_gap * 4
    setter = branch + 8
    words[guard] = 0x30000000 | (4 << 21) | (14 << 16) | flag
    words[branch] = (branch_op << 26) | (14 << 21) | (0 << 16) | skip
    words[setter] = vi.LUI_AT_ONE
    return words, guard, branch, setter


def rom_with_dither_request(**kwargs):
    """A blob carrying the library signature and one dither-on block after it."""
    blob = bytearray(0x400)
    for i, imm in enumerate((0xFFF7, 0xFFFB, 0xFFEF, 0xFCFF)):
        struct.pack_into(">I", blob, 0x40 + i * 8, 0x24010000 | imm)
    words, guard, branch, setter = dither_block(0x80, **kwargs)
    for at, word in words.items():
        struct.pack_into(">I", blob, at, word)
    return bytes(blob), guard, branch, setter


class TestFindDitherRequests:
    """The dedither filter never sits in a mode table, so the only place a ROM can
    switch it on is osViSetSpecialFeatures. These locate that request."""

    def test_finds_the_request(self):
        blob, guard, branch, setter = rom_with_dither_request()

        found = vi.find_dither_requests(blob)

        assert len(found) == 1
        assert (found[0].guard, found[0].branch, found[0].setter) == (guard, branch, setter)

    def test_records_the_flag_it_matched(self):
        blob, _, _, _ = rom_with_dither_request()

        assert vi.find_dither_requests(blob)[0].flag == vi.DITHER_FILTER_ON_FLAG

    def test_ignores_a_block_guarded_by_a_different_flag(self):
        """Three retail ROMs carry the four mask constants inside an unrelated
        routine whose block is guarded by 0x4000. Patching those would be wrong."""
        blob, _, _, _ = rom_with_dither_request(flag=0x4000)

        assert vi.find_dither_requests(blob) == ()

    def test_accepts_a_branch_likely_as_the_guard(self):
        blob, _, _, _ = rom_with_dither_request(branch_op=0x14)

        assert len(vi.find_dither_requests(blob)) == 1

    def test_finds_the_guard_when_it_sits_in_an_earlier_delay_slot(self):
        """Majora's Mask schedules the andi far from its branch, so a fixed offset
        would miss it."""
        blob, guard, _, _ = rom_with_dither_request(guard_gap=6)

        assert vi.find_dither_requests(blob)[0].guard == guard

    def test_ignores_a_branch_whose_target_does_not_clear_the_setter(self):
        blob, _, _, _ = rom_with_dither_request(skip=1)

        assert vi.find_dither_requests(blob) == ()

    def test_finds_nothing_without_the_library_signature(self):
        blob = bytearray(0x400)
        struct.pack_into(">I", blob, 0x80, vi.LUI_AT_ONE)

        assert vi.find_dither_requests(bytes(blob)) == ()

    def test_finds_nothing_in_empty_data(self):
        assert vi.find_dither_requests(bytes(0x400)) == ()


class TestClearDitherRequests:
    def test_zeroes_the_guard_immediate(self):
        blob, guard, _, _ = rom_with_dither_request()

        out, changes = vi.clear_dither_requests(blob)

        assert struct.unpack_from(">I", out, guard)[0] & 0xFFFF == 0
        assert len(changes) == 1

    def test_changes_exactly_one_byte(self):
        blob, _, _, _ = rom_with_dither_request()

        out, _ = vi.clear_dither_requests(blob)

        assert sum(1 for a, b in zip(blob, out, strict=True) if a != b) == 1

    def test_leaves_the_setter_alone(self):
        """Neutering the lui would suppress the filter and leave the AA_MODE
        override that accompanies it forcing anti-aliasing on regardless."""
        blob, _, _, setter = rom_with_dither_request()

        out, _ = vi.clear_dither_requests(blob)

        assert struct.unpack_from(">I", out, setter)[0] == vi.LUI_AT_ONE

    def test_the_request_is_gone_afterwards(self):
        blob, _, _, _ = rom_with_dither_request()

        out, _ = vi.clear_dither_requests(blob)

        assert vi.find_dither_requests(out) == ()

    def test_running_it_twice_changes_nothing_more(self):
        blob, _, _, _ = rom_with_dither_request()

        once, _ = vi.clear_dither_requests(blob)
        twice, changes = vi.clear_dither_requests(once)

        assert twice == once
        assert changes == ()

    def test_leaves_a_rom_without_a_request_untouched(self):
        blob = bytes(0x400)

        out, changes = vi.clear_dither_requests(blob)

        assert out == blob
        assert changes == ()
