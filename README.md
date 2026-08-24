# n64-video-interface

Read and edit the video configuration of a Nintendo 64 ROM, and reseal the checksum so the result still boots.

[![CI](https://github.com/gufranco/n64-video-interface-python/actions/workflows/ci.yml/badge.svg)](https://github.com/gufranco/n64-video-interface-python/actions/workflows/ci.yml)
[![Analysis](https://github.com/gufranco/n64-video-interface-python/actions/workflows/analysis.yml/badge.svg)](https://github.com/gufranco/n64-video-interface-python/actions/workflows/analysis.yml)

**3** commands · **zero** runtime dependencies · types checked **strict**

```
$ n64vi audit "Super Mario 64 (USA).z64"
title              SUPER MARIO 64
video mode table   15 entries, NTSC
anti-aliasing      on in 15 of 15
divot filter       on in 15 of 15
gamma dithering    on in 15 of 15
dedither requests  0
```

## What this is for

The VI is the Video Interface, the part of the RCP that scans a framebuffer out to the display. A retail ROM carries a table of VI modes, and the flags in it decide whether the picture is anti-aliased, dithered, gamma corrected, and put through the divot filter.

Turning them off is what people mean by a sharp N64 picture. The dedither filter is the one that matters most: it is the largest single source of blur, and it never appears in the mode table because the game switches it on at run time through `osViSetSpecialFeatures`.

## Why the checksum matters

The mode table lives inside the region the ROM checksum covers, so editing it invalidates the header. A ROM with a stale checksum will not boot on hardware, and will not boot under an accurate emulator either.

This reseals it, and refuses when it cannot prove the result is correct. Every guard below refuses rather than guesses:

- a mode-table change needs a table this code can prove is one
- a filter change needs a request it can prove belongs to the library routine
- the existing checksum must validate, otherwise the boot chip is unknown and the result could not be resealed correctly
- at least one byte must actually change, so a no-op is reported as such
- the recomputed checksum must validate before the data is handed back

A refusal names its reason. It never emits a file.

## Install

```bash
pip install https://github.com/gufranco/n64-video-interface-python/archive/refs/tags/latest.zip
```

`latest` is a tag the release job moves onto each new release, so that line never carries a version and never goes stale. To pin one instead, swap `latest` for a version tag such as `v1.0.0`.

Or clone it to work on it:

```bash
git clone https://github.com/gufranco/n64-video-interface-python.git
cd n64-video-interface-python
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
n64vi --help
```

The virtual environment is not optional on a system Python. Homebrew, Debian, Ubuntu and Fedora all ship one marked externally managed, where `pip install` stops with `error: externally-managed-environment` and installs nothing.

Without installing anything, `PYTHONPATH=src python3 -m n64_video_interface` runs the same command line from the checkout.

## Commands

| Command | Description |
|:--------|:------------|
| `audit` | Report what a ROM's video configuration asks for. Writes nothing |
| `patch` | Write an edited copy of the ROM, checksum resealed |
| `ips` | Write an IPS patch and leave the ROM byte-identical |

Every switch is available on both editing verbs:

| Switch | Effect |
|:-------|:-------|
| `--no-aa` | Disable anti-aliasing |
| `--no-dither` | Disable the dedither filter, the main source of blur |
| `--no-divot` | Disable the divot filter, which only does anything while anti-aliasing is on |
| `--no-gamma-dither` | Disable gamma dithering |
| `--no-gamma` | Disable gamma correction, which changes brightness rather than sharpness |

```bash
n64vi audit game.z64                                       # writes nothing
n64vi patch game.z64 --no-aa --no-dither --output sharp.z64
n64vi ips   game.z64 --no-aa --no-dither --output sharp.ips
```

Neither editing verb has a form that writes over its input. Both require `--output`, so no invocation of this tool can destroy the dump it was given.

## Which form to use

`patch` produces a ROM. `ips` produces a patch file and leaves the ROM alone.

Prefer `ips` when the ROM must stay byte-identical, which is the case whenever something else already binds to it. Many flash carts and copiers locate a game's patch by matching the first 64 bytes of the ROM, and the checksum sits at offset `0x10`, inside that window. Resealing after a video edit moves those bytes, so the ROM stops being recognised and the patch it needed is silently dropped.

A device that applies one patch per ROM cannot take an IPS alongside a patch the game already needs, because the two compete for the same slot. Folding both changes into a single patch is the way out, and it is what [n64-mr-backup-z64-python](https://github.com/gufranco/n64-mr-backup-z64-python) does with this library for the Mr. Backup Z64.

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

`safe_patch` never raises for a ROM it declines to edit. It returns a result whose `applied` is `False` and whose `reason` says why.

## What has not been verified

The mode-table layout and the dedither request pattern were established by scanning retail ROMs and requiring every candidate to satisfy several independent structural checks before being treated as a table. The checksum implementation is verified against every boot chip in the test suite.

None of it has been run on original hardware.

## License

[MIT](LICENSE.txt)
