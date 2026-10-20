# Agent instructions

Read [README.md](README.md) first; it describes what this project is and how it is used. This file holds the rules an agent working on the code must follow.

## A disabled option stays disabled for the whole run

This rule is MANDATORY and has no exceptions. When a patch turns off anti-aliasing, the divot filter, gamma dithering, gamma correction, the dedither filter, or the RDP colour or alpha dither, the change must hold for the whole game, in every mode, menu, level and cutscene, and the game must never be able to turn it back on. A patch that holds only until the game's next write to the video registers, or only in the part of the game that was tested, is a broken patch, however the first seconds look.

A title counts as patched only when the code and data it actually runs are patched. Changing something in the ROM is not enough: Excitebike 64 was reported as patched while only its small start-up loader had been changed, and the real program, packed elsewhere in the ROM and unpacked into RAM later, kept every original setting. Any report, table or commit that counts a title as patched must rest on the run-time checks below, never on the patcher's own result.

The code guarantees it in layers, starting from the last write:

1. **The VI control filter.** Every store that hands the control word to the hardware is redirected through a filter that clears the disabled bits, so whatever value the game computes, the register never receives them. It is installed by default.
2. **Constants.** Values written straight to the VI control register, and control words a game builds in memory, are rewritten.
3. **Every source of a bit.** Mode table entries, `osViSetSpecialFeatures` requests, a game's own setters and its anti-aliasing rebuild are neutralised, so nothing upstream asks for the bit.
4. **The RDP dither.** It is forced off where the microcode applies a display list's other modes, so no display list can set it.

The filter needs free space next to the store, which today comes from a neutralised switch request, so some titles get no filter. In those titles layers 2 to 4 carry the guarantee alone, and the full-run check below is what proves it. A title where the filter is deliberately left out must be named in code with the reason, as `INTEGRITY_CHECKED` does in `vifilter.py`.

Every packed format a game uses must be supported, or nothing inside it is patched. A title can boot with a patched loader while its real program, unpacked into RAM later, keeps the original mode table and VI routine. Excitebike 64 did exactly that until `classlz.py` handled its packed program.

## Verifying it

A title counts as supported only after this check:

- Run the patched ROM headless, with the speed limiter on, past the boot and into play, sampling the VI control register throughout the run.
- Check every non-zero value sampled, not the most frequent one. A single sample with a disabled bit set is a failure.
- Dump RDRAM during the run and run the patcher's own detection over it. Any mode table entry, switch request, setter, constant or microcode site it would still change is code or data the game loaded from somewhere the patch did not reach, and the title fails until that source is patched.
- Compare the patched run with the original run on the same plugins. A patched title that draws markedly fewer frames, or stops, fails.
- A run that never leaves the boot, attract or notice screens covers only those screens. Say so wherever the result is reported, and drive the game into play before calling the whole game covered.
- When a title shows a disabled bit, find the writer and close it in the code; never accept it as a boot-time exception.
- For an A/B picture, check the register still holds the wanted value at capture time, because a game can rewrite it every frame.
- Judge a microcode edit on low-level plugins, the cxd4 RSP and the angrylion RDP. Under the high-level RSP and glide64mk2, Excitebike 64 stalled after a one-bit change to microcode code that never runs, and ran normally on the low-level plugins, so a stall seen only under high-level emulation says nothing about the edit.

## Constraints

- Retail dumps only. Prototypes, hacks, translations and homebrew are out of scope.
- Never commit, cache, link to or ship a ROM. Dumps are identified by SHA-256 in [artifacts.manifest.json](artifacts.manifest.json).
- Code must run on original hardware; an emulator result is evidence, never the target.
- Emulators run headless, with no window and no sound.
