from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audit import Report, audit
from .header import parse as parse_header
from .ips import IpsError, make_ips
from .patch import PatchResult, safe_patch
from .version import VERSION

DESCRIPTION = (
    "Turn off the blur, dither and anti-aliasing a Nintendo 64 applies to its picture, "
    "resealing the checksum so the ROM still boots."
)
OUTPUT_IS_INPUT = "refused: the output names the ROM being read, which would destroy it"
SWITCHES = (
    ("no-aa", "antialiasing", "disable anti-aliasing"),
    ("no-divot", "divot", "disable the divot filter"),
    ("no-gamma-dither", "gamma_dither", "disable gamma dithering"),
    ("no-gamma", "gamma", "disable gamma correction"),
    ("no-dither", "dither_filter", "disable the dedither filter, the main source of blur"),
    ("no-rdp-color-dither", "rdp_color_dither", "disable the RDP colour dither in the microcode"),
    ("no-rdp-alpha-dither", "rdp_alpha_dither", "disable the RDP alpha dither in the microcode"),
)
USAGE_ERROR = 2


def _add_switches(parser: argparse.ArgumentParser) -> None:
    for flag, _, help_text in SWITCHES:
        parser.add_argument(f"--{flag}", action="store_true", help=help_text)


def requested(args: argparse.Namespace) -> dict[str, bool]:
    return {name: False for flag, name, _ in SWITCHES if getattr(args, flag.replace("-", "_"))}


def _read(path: str) -> bytes | None:
    try:
        return Path(path).read_bytes()
    except OSError as error:
        print(f"could not read {path}: {error}", file=sys.stderr)
        return None


def _write(path: str, data: bytes) -> bool:
    try:
        Path(path).write_bytes(data)
    except OSError as error:
        print(f"could not write {path}: {error}", file=sys.stderr)
        return False
    return True


def _title(rom: bytes) -> str:
    parsed = parse_header(rom)
    return parsed.internal_name if parsed else "unknown"


def _as_json(rom: bytes, report: Report) -> str:
    return json.dumps(
        {
            "title": _title(rom),
            "modes": report.mode_count,
            "antialiasing_on": report.antialiasing_on,
            "dither_filter_on": report.dither_filter_on,
            "divot_on": report.divot_on,
            "gamma_dither_on": report.gamma_dither_on,
            "special_features_sites": report.special_features_sites,
            "dedither_requests": report.dedither_requests,
            "standards": list(report.standards),
            "ctrl_values": [f"{value:08X}" for value in report.ctrl_values],
        },
        indent=1,
    )


def _as_text(rom: bytes, report: Report) -> str:
    lines = [f"title              {_title(rom)}"]
    if not report.mode_count:
        lines.append("video mode table   none could be proven present")
    else:
        count = report.mode_count
        lines.append(f"video mode table   {count} entries, {','.join(report.standards)}")
        lines.append(f"anti-aliasing      on in {report.antialiasing_on} of {count}")
        lines.append(f"divot filter       on in {report.divot_on} of {count}")
        lines.append(f"gamma dithering    on in {report.gamma_dither_on} of {count}")
    lines.append(f"dedither requests  {report.dedither_requests}")
    return "\n".join(lines)


def cmd_audit(args: argparse.Namespace) -> int:
    rom = _read(args.rom)
    if rom is None:
        return 1
    report = audit(rom)
    print(_as_json(rom, report) if args.json else _as_text(rom, report))
    return 0


def _edit(args: argparse.Namespace) -> tuple[bytes, PatchResult] | None:
    settings = requested(args)
    if not settings:
        print("name at least one setting to turn off, such as --no-aa", file=sys.stderr)
        return None
    if Path(args.output).resolve() == Path(args.rom).resolve():
        print(OUTPUT_IS_INPUT, file=sys.stderr)
        return None
    rom = _read(args.rom)
    if rom is None:
        return None
    result = safe_patch(rom, **settings)
    if not result.applied:
        print(f"refused: {result.reason}", file=sys.stderr)
        return None
    return rom, result


def cmd_patch(args: argparse.Namespace) -> int:
    edited = _edit(args)
    if edited is None or not _write(args.output, edited[1].data):
        return 1
    print(f"{edited[1].reason}, boot chip {edited[1].cic}, checksum resealed")
    print(f"written to {args.output}. The ROM you named was not modified.")
    return 0


def cmd_ips(args: argparse.Namespace) -> int:
    edited = _edit(args)
    if edited is None:
        return 1
    rom, result = edited
    try:
        patch = make_ips(rom, result.data)
    except IpsError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    if not _write(args.output, patch):
        return 1
    print(f"{result.reason}, boot chip {result.cic}")
    print(f"{len(patch)} byte patch written to {args.output}. The ROM is untouched.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="n64vi", description=DESCRIPTION)
    parser.add_argument("--version", action="version", version=VERSION)
    verbs = parser.add_subparsers(dest="command")
    audit_verb = verbs.add_parser("audit", help="report the video configuration, writes nothing")
    audit_verb.add_argument("rom")
    audit_verb.add_argument("--json", action="store_true")
    audit_verb.set_defaults(func=cmd_audit)
    for name, handler, help_text, output in (
        ("patch", cmd_patch, "write an edited copy of the ROM", "where the edited copy goes"),
        ("ips", cmd_ips, "write an IPS patch and leave the ROM untouched", "where the patch goes"),
    ):
        verb = verbs.add_parser(name, help=help_text)
        verb.add_argument("rom")
        verb.add_argument("--output", required=True, help=output)
        _add_switches(verb)
        verb.set_defaults(func=handler)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_usage()
        return USAGE_ERROR
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
