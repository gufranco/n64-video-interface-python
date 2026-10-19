"""The command line, which is the whole of what this package offers a person.

Two properties carry the risk. Nothing may write over the ROM it was given, so
both editing verbs require an output path and neither has a form that defaults
to in-place. And a refusal must stay a refusal: when the video stage declines,
no file may appear, because a zero-byte patch beside a ROM reads as a change
that was applied.
"""

from __future__ import annotations

import json

import pytest

from n64_video_interface import cli, vi
from n64_video_interface.conftest import make_rom
from n64_video_interface.vi_test import mode_entry


def rom_with_table(ctrl: int = 0x0000311E) -> bytes:
    data = bytearray(make_rom(size=vi.CHECKSUM_END + 0x2000, title="SHARP TEST ROM"))
    entry = mode_entry(ctrl=ctrl)
    data[0x2000 : 0x2000 + len(entry)] = entry
    return vi.reseal(bytes(data), "6102")


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / "game.z64"
    path.write_bytes(rom_with_table())
    return path


class TestAudit:
    def test_it_reports_the_mode_table(self, sample, capsys):
        assert cli.main(["audit", str(sample)]) == 0
        assert "video mode table" in capsys.readouterr().out

    def test_it_names_the_internal_title(self, sample, capsys):
        cli.main(["audit", str(sample)])

        assert "SHARP TEST ROM" in capsys.readouterr().out

    def test_json_carries_the_counts(self, sample, capsys):
        cli.main(["audit", str(sample), "--json"])

        payload = json.loads(capsys.readouterr().out)
        assert payload["modes"] == 1
        assert payload["antialiasing_on"] == 1

    def test_a_rom_with_no_table_still_reports(self, tmp_path, capsys):
        plain = tmp_path / "plain.z64"
        plain.write_bytes(make_rom())

        assert cli.main(["audit", str(plain)]) == 0
        assert "none could be proven present" in capsys.readouterr().out

    def test_a_missing_file_is_refused_not_crashed(self, tmp_path, capsys):
        code = cli.main(["audit", str(tmp_path / "nope.z64")])

        assert code == 1
        assert "could not read" in capsys.readouterr().err


class TestPatch:
    def test_it_writes_an_edited_copy(self, sample, tmp_path):
        out = tmp_path / "sharp.z64"

        assert cli.main(["patch", str(sample), "--no-aa", "--output", str(out)]) == 0
        assert vi.audit(out.read_bytes()).antialiasing_on == 0

    def test_the_rom_it_was_given_is_untouched(self, sample, tmp_path):
        before = sample.read_bytes()

        cli.main(["patch", str(sample), "--no-aa", "--output", str(tmp_path / "s.z64")])

        assert sample.read_bytes() == before

    def test_the_copy_carries_a_valid_checksum(self, sample, tmp_path):
        from n64_video_interface import checksum

        out = tmp_path / "sharp.z64"
        cli.main(["patch", str(sample), "--no-aa", "--output", str(out)])

        valid, cic = checksum.verify(out.read_bytes())
        assert valid
        assert cic

    def test_naming_no_setting_is_refused(self, sample, tmp_path, capsys):
        code = cli.main(["patch", str(sample), "--output", str(tmp_path / "s.z64")])

        assert code == 1
        assert "at least one setting" in capsys.readouterr().err

    def test_a_refusal_writes_no_file(self, tmp_path, capsys):
        plain = tmp_path / "plain.z64"
        plain.write_bytes(make_rom())
        out = tmp_path / "sharp.z64"

        code = cli.main(["patch", str(plain), "--no-aa", "--output", str(out)])

        assert code == 1
        assert not out.exists()


class TestIps:
    def test_it_writes_a_patch(self, sample, tmp_path):
        out = tmp_path / "sharp.ips"

        assert cli.main(["ips", str(sample), "--no-aa", "--output", str(out)]) == 0
        assert out.read_bytes().startswith(b"PATCH")
        assert out.read_bytes().endswith(b"EOF")

    def test_the_rom_it_was_given_is_untouched(self, sample, tmp_path):
        before = sample.read_bytes()

        cli.main(["ips", str(sample), "--no-aa", "--output", str(tmp_path / "s.ips")])

        assert sample.read_bytes() == before

    def test_a_refusal_writes_no_file(self, tmp_path):
        plain = tmp_path / "plain.z64"
        plain.write_bytes(make_rom())
        out = tmp_path / "sharp.ips"

        assert cli.main(["ips", str(plain), "--no-aa", "--output", str(out)]) == 1
        assert not out.exists()


class TestTheParser:
    def test_no_arguments_prints_usage(self, capsys):
        assert cli.main([]) == 2
        assert "usage" in capsys.readouterr().out

    def test_every_switch_is_offered_by_both_editing_verbs(self):
        parser = cli.build_parser()
        text = parser.format_help()

        assert "audit" in text
        assert "patch" in text
        assert "ips" in text

    def test_neither_editing_verb_can_run_without_an_output(self, sample):
        for verb in ("patch", "ips"):
            with pytest.raises(SystemExit):
                cli.build_parser().parse_args([verb, str(sample), "--no-aa"])


class TestFailingToWrite:
    """A tool that cannot write its output has to say so, not traceback.

    The output path comes from the caller, so it can name a directory that does
    not exist, one they cannot write to, or a name the filesystem refuses. Every
    one of those is a normal mistake rather than a fault in this code.
    """

    def test_patch_reports_a_path_it_cannot_write(self, sample, tmp_path, capsys):
        blocked = tmp_path / "missing" / "deep" / "sharp.z64"

        code = cli.main(["patch", str(sample), "--no-aa", "--output", str(blocked)])

        assert code == 1
        assert "could not write" in capsys.readouterr().err

    def test_ips_reports_a_path_it_cannot_write(self, sample, tmp_path, capsys):
        blocked = tmp_path / "missing" / "deep" / "sharp.ips"

        code = cli.main(["ips", str(sample), "--no-aa", "--output", str(blocked)])

        assert code == 1
        assert "could not write" in capsys.readouterr().err

    def test_patch_refuses_a_rom_it_cannot_read(self, tmp_path, capsys):
        code = cli.main(
            ["patch", str(tmp_path / "nope.z64"), "--no-aa", "--output", str(tmp_path / "s.z64")]
        )

        assert code == 1
        assert "could not read" in capsys.readouterr().err

    def test_an_offset_beyond_what_ips_addresses_is_refused(self, sample, tmp_path, monkeypatch):
        from n64_video_interface import vi as module

        def too_far(*args, **kwargs):
            raise ValueError("offset 0x1000000 is beyond what IPS can address")

        monkeypatch.setattr(module, "make_ips", too_far)
        out = tmp_path / "sharp.ips"

        code = cli.main(["ips", str(sample), "--no-aa", "--output", str(out)])

        assert code == 1
        assert not out.exists()


class TestTheEntryPoints:
    """The two lines between a shell and `main`, which nothing reached.

    `python -m n64_video_interface` and the console script both end up in the
    same function, and the glue in front of it is small enough to look obviously
    right and still be the thing that breaks an install.
    """

    def test_the_module_entry_point_imports_the_same_main(self):
        import importlib

        module = importlib.import_module("n64_video_interface.__main__")

        assert module.main is cli.main

    def test_a_parser_exit_becomes_a_return_code(self, monkeypatch):
        def leave(_args):
            raise SystemExit(3)

        monkeypatch.setattr(cli, "build_parser", lambda: _parser_returning(leave))

        assert cli.main(["audit", "whatever"]) == 3

    def test_a_clean_parser_exit_is_a_zero(self, monkeypatch):
        def leave(_args):
            raise SystemExit(None)

        monkeypatch.setattr(cli, "build_parser", lambda: _parser_returning(leave))

        assert cli.main(["audit", "whatever"]) == 0


def _parser_returning(func):
    import argparse

    parser = argparse.ArgumentParser(prog="n64vi")
    sub = parser.add_subparsers(dest="command")
    one = sub.add_parser("audit")
    one.add_argument("rom")
    one.set_defaults(func=func)
    return parser
