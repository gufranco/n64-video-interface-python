<div align="center">

<h1>N64 Video Interface</h1>

<strong>Turn off the blur a Nintendo 64 applies to its own picture, and reseal the checksum so the ROM still boots.</strong>

<br>
<br>

[![CI](https://github.com/gufranco/n64-video-interface-python/actions/workflows/ci.yml/badge.svg)](https://github.com/gufranco/n64-video-interface-python/actions/workflows/ci.yml)
[![Analysis](https://github.com/gufranco/n64-video-interface-python/actions/workflows/analysis.yml/badge.svg)](https://github.com/gufranco/n64-video-interface-python/actions/workflows/analysis.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25%20statement%20%2B%20branch-brightgreen)](#tests)
[![Tests](https://img.shields.io/badge/tests-160-brightgreen)](#tests)
[![Types](https://img.shields.io/badge/mypy-strict-blue)](pyproject.toml)
[![Dependencies](https://img.shields.io/badge/runtime%20dependencies-zero-blue)](pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE.txt)

</div>

<p align="center">
  <a href="#quick-start">Quick start</a> &nbsp;|&nbsp;
  <a href="#what-actually-causes-the-blur">What causes the blur</a> &nbsp;|&nbsp;
  <a href="#why-the-checksum-is-the-hard-part">Why the checksum is hard</a> &nbsp;|&nbsp;
  <a href="#rom-or-patch">ROM or patch</a> &nbsp;|&nbsp;
  <a href="#every-guard-refuses-rather-than-guesses">The guards</a> &nbsp;|&nbsp;
  <a href="https://github.com/gufranco/n64-video-interface-python/issues">Issues</a>
</p>

**3** commands · **160** tests · **100%** statement and branch coverage · **zero** runtime dependencies · types checked **strict**

```console
$ n64vi audit "Super Mario 64 (USA).z64"
title              SUPER MARIO 64
video mode table   15 entries, NTSC
anti-aliasing      on in 15 of 15
divot filter       on in 15 of 15
gamma dithering    on in 15 of 15
dedither requests  3

$ n64vi ips "Super Mario 64 (USA).z64" --no-aa --no-dither --output sharp.ips
15 of 15 mode entries changed, 3 dedither requests neutralised, boot chip 6102
206 byte patch written to sharp.ips. The ROM is untouched.
```

## Quick start

```bash
git clone https://github.com/gufranco/n64-video-interface-python.git
cd n64-video-interface-python
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
n64vi --help
```

The virtual environment is not optional on a system Python. Homebrew, Debian, Ubuntu and Fedora all ship one marked externally managed, where `pip install` stops with `error: externally-managed-environment` and installs nothing.

To install without cloning:

```bash
pip install https://github.com/gufranco/n64-video-interface-python/archive/refs/tags/latest.zip
```

`latest` is a tag the release job moves onto each release, so that line never carries a version and never goes stale.

Without installing anything, `PYTHONPATH=src python3 -m n64_video_interface` runs the same command line from the checkout. `pytest` needs no environment at all: the path is in `pyproject.toml`.

## What actually causes the blur

The VI is the Video Interface, the part of the RCP that scans a framebuffer out to the display. Four of the settings that soften the picture live in a table of VI modes inside the ROM:

| Setting | What it does | Switch |
|:--------|:-------------|:-------|
| Anti-aliasing | Blends edge pixels against their neighbours | `--no-aa` |
| Divot filter | Fills single-pixel gaps left by anti-aliasing. Does nothing once AA is off | `--no-divot` |
| Gamma dithering | Adds noise before gamma correction to hide banding | `--no-gamma-dither` |
| Gamma correction | Brightens the output. Changes brightness, not sharpness | `--no-gamma` |

The fifth is the one that matters most and the one people miss.

**The dedither filter is the largest single source of blur, and it is not in the table.** It never appears there, because the game switches it on at run time through `osViSetSpecialFeatures` rather than declaring it. Clearing it means finding that call and neutralising it, which is what `--no-dither` does and why it is listed separately from the four above.

## Why the checksum is the hard part

The mode table sits inside the region the ROM header checksum covers. Editing one word of it invalidates the header, and a ROM with a stale checksum does not boot on hardware and does not boot under an accurate emulator either.

So every edit has to be resealed, and resealing needs to know which boot chip the cartridge shipped with, because each one seeds the checksum differently. That is read back from the existing checksum rather than guessed, which is also why a ROM whose checksum does not already validate is refused: there is no way to know what to reseal it as.

## ROM or patch

Two verbs write, and which one you want depends on whether anything else is bound to the ROM.

```bash
n64vi patch game.z64 --no-aa --no-dither --output sharp.z64   # a new ROM
n64vi ips   game.z64 --no-aa --no-dither --output sharp.ips   # a patch, ROM untouched
```

Prefer `ips` whenever the ROM must stay byte-identical. Many flash carts and copiers locate a game's patch by matching the **first 64 bytes** of the ROM, and the checksum sits at offset `0x10`, inside that window. Resealing after a video edit moves those bytes, so the device stops recognising the game and the patch it needed is silently dropped. The picture gets sharper and the save fix disappears.

A device that applies one patch per ROM cannot take an IPS alongside a patch the game already needs, because the two compete for the same slot. Folding both changes into a single patch is the way out of that, and it is what [n64-mr-backup-z64-python](https://github.com/gufranco/n64-mr-backup-z64-python) does with this library for the Mr. Backup Z64.

Neither verb has a form that writes over its input. Both require `--output`, so no invocation of this tool can destroy the dump it was given.

## Every guard refuses rather than guesses

A refusal names its reason and writes nothing. There is no partial result and no file left behind to wonder about.

- A mode-table change needs a table this code can prove is one. Several independent structural checks have to agree before a run of words is treated as a table, because a false positive here rewrites arbitrary game code.
- A filter change needs a request it can prove belongs to the library routine.
- The existing checksum has to validate, otherwise the boot chip is unknown and the result could not be resealed correctly.
- At least one byte has to actually change, so a no-op is reported as such rather than written out as a change.
- The recomputed checksum has to validate before the data is handed back.

```console
$ n64vi patch homebrew.z64 --no-aa --output sharp.z64
refused: no video mode table could be proven present
```

## Using it as a library

```python
from n64_video_interface import audit, safe_patch

rom = open("game.z64", "rb").read()

print(audit(rom).antialiasing_on)

result = safe_patch(rom, antialiasing=False, dither_filter=False)
if result.applied:
    open("sharp.z64", "wb").write(result.data)
else:
    print(result.reason)
```

`safe_patch` never raises for a ROM it declines to edit. It returns a result whose `applied` is `False` and whose `reason` says why, so a caller iterating over a folder does not have to wrap every call.

| Name | Returns |
|:-----|:--------|
| `audit(rom)` | What the ROM's video configuration currently asks for |
| `safe_patch(rom, **settings)` | An edited ROM with the checksum resealed, or a refusal |
| `reseal(rom, cic=None)` | The checksum recomputed, for a caller that changed the ROM some other way |
| `find_mode_tables(rom)` | The mode entries found, for a caller doing its own analysis |

## Tests

160 tests, 100% statement and branch coverage, gated in CI rather than asserted here. Each test file sits beside the module it tests, as `<module>_test.py`. Every ROM the suite needs is synthesised, so it passes in a fresh clone with no game files and no network.

```bash
pytest
```

The matrix runs Python 3.11 through 3.13 on Linux x86_64 and arm64 and on macOS Intel and Apple silicon. A separate job installs the package the way a user would and drives the three verbs through the console script, because a suite passing from a checkout does not prove the thing a user gets works.

A weekly job re-runs everything against unpinned tooling, which is what catches a linter or a type checker changing its mind about code nobody touched.

## What has been verified, and what has not

The mode-table layout and the dedither request pattern were established by scanning retail ROMs and requiring every candidate to satisfy several independent structural checks before being treated as a table. The checksum implementation is verified against every boot chip in the test suite.

Patched ROMs produced by this have booted and run on real Nintendo 64 hardware.

What has not been established is how broadly that holds. The mode table is found by scanning rather than by reading a fixed offset, and the library has not been run against every ROM of every region. A dump whose table this cannot prove is a table is refused rather than edited, so the failure mode is a game that goes unpatched rather than one that breaks, but a game that is patched and looks wrong is still possible and has not been ruled out for every title.

Test before committing to a batch, and please open an issue with the title and region if a game is refused or comes out wrong. That is the fastest way for the scanner to learn a case it does not yet handle.

## License

[MIT](LICENSE.txt)
