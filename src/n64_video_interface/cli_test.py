import importlib
import json

import pytest

from n64_video_interface import cli
from n64_video_interface.audit import audit
from n64_video_interface.checksum import verify
from n64_video_interface.conftest import SEALABLE_SIZE, make_rom, mode_entry, sealable_rom
from n64_video_interface.ips_test import apply_ips
from n64_video_interface.patch import ALREADY_OFF
from n64_video_interface.seal import reseal
from n64_video_interface.version import VERSION

EVERY_SWITCH = [f"--{flag}" for flag, _, _ in cli.SWITCHES]


@pytest.fixture
def sample(tmp_path):
    rom = bytearray(sealable_rom()[0])
    rom[0x20:0x34] = b"SHARP TEST ROM      "
    path = tmp_path / "game.z64"
    path.write_bytes(reseal(bytes(rom), "6102"))
    return path


def run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


class TestAudit:
    def test_the_report_names_the_title_and_the_table(self, sample, capsys):
        # Arrange
        argv = ("audit", str(sample))

        # Act
        code, out, _ = run(capsys, *argv)

        # Assert
        assert (code, out) == (
            0,
            "title              SHARP TEST ROM\n"
            "video mode table   1 entries, NTSC\n"
            "anti-aliasing      on in 1 of 1\n"
            "divot filter       on in 1 of 1\n"
            "gamma dithering    on in 1 of 1\n"
            "dedither requests  0\n",
        )

    def test_json_carries_the_counts(self, sample, capsys):
        # Arrange
        argv = ("audit", str(sample), "--json")

        # Act
        _, out, _ = run(capsys, *argv)

        # Assert
        assert json.loads(out) == {
            "title": "SHARP TEST ROM",
            "modes": 1,
            "antialiasing_on": 1,
            "dither_filter_on": 0,
            "divot_on": 1,
            "gamma_dither_on": 1,
            "special_features_sites": 0,
            "dedither_requests": 0,
            "standards": ["NTSC"],
            "ctrl_values": ["0000311E"],
        }

    def test_a_rom_with_no_table_or_header_still_reports(self, tmp_path, capsys):
        # Arrange
        plain = tmp_path / "plain.z64"
        plain.write_bytes(bytes(0x1000))

        # Act
        code, out, _ = run(capsys, "audit", str(plain))

        # Assert
        assert (code, out) == (
            0,
            "title              unknown\n"
            "video mode table   none could be proven present\n"
            "dedither requests  0\n",
        )

    def test_a_missing_file_is_refused_not_crashed(self, tmp_path, capsys):
        # Arrange
        missing = tmp_path / "nope.z64"

        # Act
        code, _, err = run(capsys, "audit", str(missing))

        # Assert
        assert (code, err.startswith(f"could not read {missing}")) == (1, True)


class TestPatch:
    def test_the_copy_is_edited_and_resealed_and_the_rom_untouched(self, sample, tmp_path, capsys):
        # Arrange
        before = sample.read_bytes()
        out = tmp_path / "sharp.z64"

        # Act
        code, stdout, _ = run(capsys, "patch", str(sample), "--no-aa", "--output", str(out))

        # Assert
        assert (
            code,
            audit(out.read_bytes()).antialiasing_on,
            verify(out.read_bytes()),
            sample.read_bytes() == before,
            stdout,
        ) == (
            0,
            0,
            (True, "6102"),
            True,
            "1 of 1 mode entries changed, boot chip 6102, checksum resealed\n"
            f"written to {out}. The ROM you named was not modified.\n",
        )

    @pytest.mark.parametrize(
        ("extra", "message"),
        [
            ((), "name at least one setting to turn off, such as --no-aa\n"),
            (
                ("--no-dither",),
                f"refused: {ALREADY_OFF}\n",
            ),
        ],
        ids=["no setting", "refused by the patch"],
    )
    def test_a_refusal_writes_no_file(self, sample, tmp_path, capsys, extra, message):
        # Arrange
        out = tmp_path / "sharp.z64"

        # Act
        code, _, err = run(capsys, "patch", str(sample), *extra, "--output", str(out))

        # Assert
        assert (code, err, out.exists()) == (1, message, False)

    @pytest.mark.parametrize("verb", ["patch", "ips"])
    def test_an_output_naming_the_input_is_refused_and_the_rom_kept(self, verb, sample, capsys):
        # Arrange
        before = sample.read_bytes()
        same = sample.parent / "." / sample.name

        # Act
        code, _, err = run(capsys, verb, str(sample), "--no-aa", "--output", str(same))

        # Assert
        assert (code, sample.read_bytes() == before, err) == (1, True, cli.OUTPUT_IS_INPUT + "\n")

    @pytest.mark.parametrize("verb", ["patch", "ips"])
    def test_a_path_that_cannot_be_written_is_reported(self, verb, sample, tmp_path, capsys):
        # Arrange
        blocked = tmp_path / "missing" / "deep" / "sharp"

        # Act
        code, _, err = run(capsys, verb, str(sample), "--no-aa", "--output", str(blocked))

        # Assert
        assert (code, err.startswith(f"could not write {blocked}")) == (1, True)

    def test_a_rom_that_cannot_be_read_is_reported(self, tmp_path, capsys):
        # Arrange
        missing = tmp_path / "nope.z64"

        # Act
        code, _, err = run(
            capsys, "patch", str(missing), "--no-aa", "--output", str(tmp_path / "s.z64")
        )

        # Assert
        assert (code, err.startswith(f"could not read {missing}")) == (1, True)


class TestIps:
    def test_applying_the_patch_reproduces_the_patched_rom(self, sample, tmp_path, capsys):
        # Arrange
        edited = tmp_path / "sharp.z64"
        run(capsys, "patch", str(sample), "--no-aa", "--output", str(edited))
        patch = tmp_path / "sharp.ips"

        # Act
        code, _, _ = run(capsys, "ips", str(sample), "--no-aa", "--output", str(patch))

        # Assert
        assert (code, apply_ips(sample.read_bytes(), patch.read_bytes())) == (
            0,
            edited.read_bytes(),
        )

    def test_an_edit_beyond_what_ips_addresses_is_refused(self, tmp_path, capsys):
        # Arrange
        data = bytearray(make_rom(size=0x1000100))
        data[0x1000000 : 0x1000000 + 80] = mode_entry()
        rom = tmp_path / "big.z64"
        rom.write_bytes(reseal(bytes(data), "6102"))
        out = tmp_path / "sharp.ips"

        # Act
        code, _, err = run(capsys, "ips", str(rom), "--no-aa", "--output", str(out))

        # Assert
        assert (code, out.exists(), err) == (
            1,
            False,
            "refused: offset 0x1000006 is beyond what IPS can address, 0xFFFFFF\n",
        )


class TestTheParser:
    def test_no_verb_prints_usage(self, capsys):
        # Arrange
        argv = ()

        # Act
        code, out, _ = run(capsys, *argv)

        # Assert
        assert (code, out.startswith("usage: n64vi")) == (2, True)

    @pytest.mark.parametrize("verb", ["patch", "ips"])
    def test_every_switch_maps_to_its_setting_turned_off(self, verb):
        # Arrange
        parser = cli.build_parser()

        # Act
        args = parser.parse_args([verb, "game.z64", "--output", "o", *EVERY_SWITCH])

        # Assert
        assert cli.requested(args) == {name: False for _, name, _ in cli.SWITCHES}

    @pytest.mark.parametrize("verb", ["patch", "ips"])
    def test_neither_editing_verb_runs_without_an_output(self, verb, capsys):
        # Arrange
        parser = cli.build_parser()

        # Act
        with pytest.raises(SystemExit) as caught:
            parser.parse_args([verb, "game.z64", "--no-aa"])

        # Assert
        assert (caught.value.code, "--output" in capsys.readouterr().err) == (2, True)

    def test_the_version_is_printed(self, capsys):
        # Arrange
        parser = cli.build_parser()

        # Act
        with pytest.raises(SystemExit) as caught:
            parser.parse_args(["--version"])

        # Assert
        assert (caught.value.code, capsys.readouterr().out) == (0, f"{VERSION}\n")

    def test_the_module_entry_point_runs_the_same_main(self):
        # Arrange
        name = "n64_video_interface.__main__"

        # Act
        module = importlib.import_module(name)

        # Assert
        assert module.main is cli.main


def test_a_sealable_rom_is_large_enough_to_checksum():
    # Arrange
    size = SEALABLE_SIZE

    # Act
    rom = make_rom(size=size)

    # Assert
    assert len(rom) == size
