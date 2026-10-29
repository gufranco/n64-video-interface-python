<div align="center">

<h1>N64 Video Interface</h1>

<strong>Turn off the blur a Nintendo 64 applies to its own picture, and reseal the checksum so the ROM still boots.</strong>

<br>
<br>

[![ci](https://img.shields.io/github/actions/workflow/status/gufranco/n64-video-interface-python/ci.yml?branch=main&style=flat-square&label=ci)](https://github.com/gufranco/n64-video-interface-python/actions/workflows/ci.yml)
[![release](https://img.shields.io/github/v/release/gufranco/n64-video-interface-python?style=flat-square&label=release)](https://github.com/gufranco/n64-video-interface-python/releases/latest)
[![coverage](https://img.shields.io/badge/coverage-100%25-brightgreen?style=flat-square)](https://github.com/gufranco/n64-video-interface-python/actions/workflows/ci.yml)
[![openssf scorecard](https://img.shields.io/ossf-scorecard/github.com/gufranco/n64-video-interface-python?style=flat-square&label=openssf%20scorecard)](https://scorecard.dev/viewer/?uri=github.com/gufranco/n64-video-interface-python)
[![license](https://img.shields.io/github/license/gufranco/n64-video-interface-python?style=flat-square&label=license)](LICENSE.txt)

</div>

<p align="center">
  <a href="#quick-start">Quick start</a> |
  <a href="#what-actually-causes-the-blur">What causes the blur</a> |
  <a href="#why-the-checksum-is-the-hard-part">Why the checksum is hard</a> |
  <a href="#rom-or-patch">ROM or patch</a> |
  <a href="#every-guard-refuses-rather-than-guesses">The guards</a> |
  <a href="https://github.com/gufranco/n64-video-interface-python/issues">Issues</a>
</p>

**3** commands · **664** tests · **100%** statement and branch coverage · **zero** runtime dependencies · types checked **strict**

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
uv sync
uv run n64vi --help
```

[uv](https://docs.astral.sh/uv/) creates the environment and installs the locked tool versions.

To install without cloning:

```bash
pip install https://github.com/gufranco/n64-video-interface-python/archive/refs/tags/latest.zip
```

`latest` is a tag the release job moves onto each release, so that line never carries a version and never goes stale.

Without installing anything, `PYTHONPATH=src python3 -m n64_video_interface` runs the same command line from the checkout. `pytest` needs no environment at all: the path is in `pyproject.toml`.

## What actually causes the blur

The VI is the Video Interface, the part of the RCP that scans a framebuffer out to the display. Four of the settings that soften the picture live in a table of VI modes inside the ROM:

| Setting          | What it does                                                               | Switch              |
| :--------------- | :------------------------------------------------------------------------- | :------------------ |
| Anti-aliasing    | Blends edge pixels against their neighbours                                | `--no-aa`           |
| Divot filter     | Fills single-pixel gaps left by anti-aliasing. Does nothing once AA is off | `--no-divot`        |
| Gamma dithering  | Adds noise before gamma correction to hide banding                         | `--no-gamma-dither` |
| Gamma correction | Brightens the output. Changes brightness, not sharpness                    | `--no-gamma`        |

The fifth is the one that matters most and the one people miss.

**The dedither filter is the largest single source of blur, and it is not in the table.** It never appears there, because the game switches it on at run time through `osViSetSpecialFeatures` rather than declaring it. Clearing it means finding that call and neutralising it, which is what `--no-dither` does and why it is listed separately from the four above.

### Settings that live outside the table

The table and the `osViSetSpecialFeatures` call are where most games keep these settings, not where all of them do.

- Some games write the control register straight from code: a loader that sets up the screen before the game is unpacked, or a game's own VI setup routine. Those constants get the same change as a table entry.
- HAL's games build their VI mode in memory and set the control word from a constant of their own, then switch features on and off through their own routines rather than the library's.
- When a game rebuilds the control word on every frame, the store that hands it to the hardware is filtered: the bits that were switched off are masked out of every value the game writes.
- `--no-rdp-color-dither` and `--no-rdp-alpha-dither` reach past the VI into the graphics microcode, where the RDP's own dithering is set.

## Compressed game code

Most of these settings are in the game's code, and many retail games ship that code compressed. Each supported format is unpacked, edited, and packed again into the bytes it occupied, so nothing else in the ROM moves:

| Format                                           | Where it appears                           |
| :----------------------------------------------- | :----------------------------------------- |
| Rare zip, with the `0x1172` and `0x1173` headers | Banjo-Kazooie, Perfect Dark                |
| Size-prefixed deflate chains                     | Banjo-Tooie                                |
| Yaz0                                             | The Legend of Zelda: Ocarina of Time       |
| gzip members, with or without a header           | Blast Corps, Gauntlet Legends              |
| RNC methods 1 and 2                              | Turok, WWF, All-Star Baseball, NFL QB Club |
| The Madden bit-packed and byte-coded segments    | Madden 64 to Madden 2002                   |
| RefPack                                          | FIFA, NHL 99, Supercross 2000              |
| LZSS                                             | Extreme-G, Extreme-G 2                     |
| Flag-byte LZ                                     | Mario Tennis                               |
| MIO0 block chains                                | the Ken Griffey Jr. games                  |
| Prefix-coded LZ                                  | Ms. Pac-Man: Maze Madness                  |
| LZHUF                                            | Star Wars: Shadows of the Empire           |
| Distance-class LZ programs                       | Excitebike 64                              |

A stream is only treated as one of these when it decodes to exactly its stated size, and the repacked stream has to fit where the old one was. Where the original packer left no room, the edited code is parsed again until it compresses at least as well. A stream that still does not fit is left alone, and the refusal says so.

Integrity checks are handled as they were found: Banjo-Kazooie's checksums are recomputed, Perfect Dark's checksummed functions are outside anything this changes, and Super Smash Bros., which freezes when the control filter is written into its code, gets the same result from its constants and setters instead.

Excitebike 64 with either RDP dither option carries an edited `F3DLX.Rej` microcode. It runs under low-level RSP and RDP emulation, and stalls after boot under mupen64plus's high-level RSP with glide64mk2. A copy with a single bit changed in microcode code that never runs stalls there too.

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
- The ROM has to be a retail dump. Prototypes, hacks, translations and homebrew are out of scope, and a patch is only claimed for what retail games have been checked against.
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

| Name                          | Returns                                                                   |
| :---------------------------- | :------------------------------------------------------------------------ |
| `audit(rom)`                  | What the ROM's video configuration currently asks for                     |
| `safe_patch(rom, **settings)` | An edited ROM with the checksum resealed, or a refusal                    |
| `reseal(rom, cic=None)`       | The checksum recomputed, for a caller that changed the ROM some other way |
| `find_mode_tables(rom)`       | The mode entries found, for a caller doing its own analysis               |

## Tests

664 tests, 100% statement and branch coverage, gated in CI rather than asserted here. Each test file sits beside the module it tests, as `<module>_test.py`. Every ROM the suite needs is synthesised, so it passes in a fresh clone with no game files and no network.

```bash
pytest
```

The matrix runs Python 3.11 through 3.13 on Linux x86_64 and arm64 and on macOS Intel and Apple silicon. A separate job installs the package the way a user would and drives the three verbs through the console script, because a suite passing from a checkout does not prove the thing a user gets works.

A weekly job re-runs everything against unpinned tooling, which is what catches a linter or a type checker changing its mind about code nobody touched.

## What has been verified, and what has not (as of 2026-10-20)

The check covered all 292 USA retail dumps in the Mr. Backup set, under mupen64plus 2.6.0:

- Every one of the 292 is patched with anti-aliasing, divot, gamma dithering and dedither all switched off.
- Each patched ROM booted and ran for 25 seconds. The VI control value it settled on had those four switched off: `0x3202` in 217 games, `0x3242` in 53, `0x320A` or `0x324A` in 14 that keep their own gamma correction, `0x0202` in HAL's three games, and the 32-bit equivalents in the remaining 5.
- With the speed limiter on, the patched games drew frames at the same rate as the originals. Two games, Wetrix and World Driver Championship, drew fewer, and so did a copy of each with one byte changed outside anything the game reads. That points at the emulator keying its per-game settings to the file, not at the patch.

Before-and-after pictures were taken with angrylion, which reproduces the VI filters, from one deterministic run per pair: the game is stopped at the same frame, only the setting changes, and a pair is kept only when the console's memory differs nowhere except in that setting.

Patched ROMs from earlier releases, which only edited uncompressed games, have booted and run on real Nintendo 64 hardware. The compressed-code repacks in this release have been verified in the emulator only, and a few behaviours, such as Banjo-Tooie's own checksums of its code over long play, have not been exercised.

Regions other than the USA have not been run through the same check. A dump this cannot prove is safe to edit is refused rather than edited, so the failure mode is a game that goes unpatched rather than one that breaks, but a game that is patched and looks wrong is still possible.

Please open an issue with the title and region if a game is refused or comes out wrong.

## License

[MIT](LICENSE.txt)
