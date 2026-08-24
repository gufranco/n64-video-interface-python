"""Read and edit the video configuration of a Nintendo 64 ROM.

The VI is the Video Interface, the part of the RCP that scans a framebuffer out
to the display. A retail ROM carries a table of VI modes, and the settings in it
decide whether the picture is anti-aliased, dithered, gamma corrected, and put
through the divot filter. Turning them off is what people mean by a sharp ROM.

Three names are the whole surface. `audit` reports what a ROM currently asks
for. `safe_patch` turns settings off and reseals the checksum, refusing rather
than guessing whenever it cannot prove the edit is correct. `reseal` recomputes
the checksum alone, for a caller that changed the ROM some other way.

Nothing here knows about any particular flash cart, copier or emulator. The
output is a ROM, and what carries it is the caller's problem.
"""

from .version import VERSION
from .vi import audit, find_mode_tables, reseal, safe_patch

__all__ = ["VERSION", "audit", "find_mode_tables", "reseal", "safe_patch"]
