"""The command line, which is the whole of what this package offers a person.

Three verbs. `audit` reads a ROM and reports what its video configuration asks
for. `patch` writes an edited copy. `ips` writes a patch file and leaves the ROM
byte-identical, for the common case where the ROM must not be touched and
something else applies the change.

Nothing writes over its input. `patch` and `ips` both require an output path, so
there is no invocation of this tool that can destroy the dump it was given.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import vi
from .header import parse as parse_header
from .version import VERSION

OUTPUT_IS_INPUT = "refused: the output names the ROM being read, which would destroy it"

SWITCHES = (
    ("no-aa", "antialiasing", "disable anti-aliasing"),
    ("no-divot", "divot", "disable the divot filter"),
    ("no-gamma-dither", "gamma_dither", "disable gamma dithering"),
    ("no-gamma", "gamma", "disable gamma correction"),
    ("no-dither", "dither_filter", "disable the dedither filter, the main source of blur"),
)


def _add_switches(parser: argparse.ArgumentParser) -> None:
    for flag, _, help_text in SWITCHES:
        parser.add_argument(f"--{flag}", action="store_true", help=help_text)


def _requested(args: argparse.Namespace) -> dict[str, bool]:
    return {name: False for flag, name, _ in SWITCHES if getattr(args, flag.replace("-", "_"))}


def _read(path: str) -> bytes | None:
    try:
        return Path(path).read_bytes()
    except OSError as error:
        print(f"could not read {path}: {error}", file=sys.stderr)
        return None


def _title(rom: bytes) -> str:
    """The internal title, or a placeholder when the header is not readable.

    A ROM whose header cannot be parsed is still worth auditing, because the
    mode table is found by scanning rather than by anything the header says.
    """
    parsed = parse_header(rom)
    return parsed.internal_name if parsed else "unknown"


def cmd_audit(args: argparse.Namespace) -> int:
    rom = _read(args.rom)
    if rom is None:
        return 1
    report = vi.audit(rom)

    if args.json:
        print(
            json.dumps(
                {
                    "title": _title(rom),
                    "modes": report.mode_count,
                    "antialiasing_on": report.antialiasing_on,
                    "dither_filter_on": report.dither_filter_on,
                    "divot_on": report.divot_on,
                    "gamma_dither_on": report.gamma_dither_on,
                    "special_features_sites": report.special_features_sites,
                    "standards": list(report.standards),
                    "ctrl_values": [f"{value:08X}" for value in report.ctrl_values],
                },
                indent=1,
            )
        )
        return 0

    print(f"title              {_title(rom)}")
    if not report.mode_count:
        print("video mode table   none could be proven present")
    else:
        print(f"video mode table   {report.mode_count} entries, {','.join(report.standards)}")
        print(f"anti-aliasing      on in {report.antialiasing_on} of {report.mode_count}")
        print(f"divot filter       on in {report.divot_on} of {report.mode_count}")
        print(f"gamma dithering    on in {report.gamma_dither_on} of {report.mode_count}")
    print(f"dedither requests  {report.special_features_sites}")
    return 0


def _edit(args: argparse.Namespace) -> tuple[bytes, vi.PatchResult] | None:
    requests = _requested(args)
    if not requests:
        print("name at least one setting to turn off, such as --no-aa", file=sys.stderr)
        return None
    if Path(args.output).resolve() == Path(args.rom).resolve():
        print(OUTPUT_IS_INPUT, file=sys.stderr)
        return None
    rom = _read(args.rom)
    if rom is None:
        return None
    result = vi.safe_patch(rom, **requests)
    if not result.applied:
        print(f"refused: {result.reason}", file=sys.stderr)
        return None
    return rom, result


def cmd_patch(args: argparse.Namespace) -> int:
    edited = _edit(args)
    if edited is None:
        return 1
    _, result = edited
    try:
        Path(args.output).write_bytes(result.data)
    except OSError as error:
        print(f"could not write {args.output}: {error}", file=sys.stderr)
        return 1
    print(f"{result.reason}, boot chip {result.cic}, checksum resealed")
    print(f"written to {args.output}. The ROM you named was not modified.")
    return 0


def cmd_ips(args: argparse.Namespace) -> int:
    edited = _edit(args)
    if edited is None:
        return 1
    _, result = edited
    try:
        patch = vi.make_ips(result.changes, checksum_words=result.data)
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    try:
        Path(args.output).write_bytes(patch)
    except OSError as error:
        print(f"could not write {args.output}: {error}", file=sys.stderr)
        return 1
    print(f"{result.reason}, boot chip {result.cic}")
    print(f"{len(patch)} byte patch written to {args.output}. The ROM is untouched.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="n64vi", description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    subparsers = parser.add_subparsers(dest="command")

    audit = subparsers.add_parser("audit", help="report the video configuration, writes nothing")
    audit.add_argument("rom")
    audit.add_argument("--json", action="store_true")
    audit.set_defaults(func=cmd_audit)

    patch = subparsers.add_parser("patch", help="write an edited copy of the ROM")
    patch.add_argument("rom")
    patch.add_argument("--output", required=True, help="where the edited copy goes")
    _add_switches(patch)
    patch.set_defaults(func=cmd_patch)

    ips = subparsers.add_parser("ips", help="write an IPS patch and leave the ROM untouched")
    ips.add_argument("rom")
    ips.add_argument("--output", required=True, help="where the patch goes")
    _add_switches(ips)
    ips.set_defaults(func=cmd_ips)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_usage()
        return 2
    try:
        return int(args.func(args))
    except SystemExit as exc:
        return int(exc.code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
