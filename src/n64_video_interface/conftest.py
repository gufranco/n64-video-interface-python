import struct

import pytest

from n64_video_interface.byteorder import ByteOrder, swap
from n64_video_interface.checksum import LENGTH, START

ROM_SIZE = 4 * 1024 * 1024
SEALABLE_SIZE = START + LENGTH + 0x4000
NTSC_LAN1_CTRL = 0x0000311E
LUI_AT_ONE = 0x3C010001
MASK_IMMEDIATES = (0xFFF7, 0xFFFB, 0xFFEF, 0xFCFF)
ADDIU_AT = 0x24010000
ROUTINE_AT = 0x40
BLOCKS_AT = 0x60
BLOCK_STRIDE = 0x20
FIELD_REGISTERS = (
    0x00000280,
    0x00000400,
    0x002501FF,
    0x000E0204,
    0x00000002,
)


def make_rom(
    *,
    title="SYNTHETIC TEST ROM",
    cart="ZZ",
    region="E",
    version=0,
    crc1=0x11223344,
    crc2=0x55667788,
    size=ROM_SIZE,
    order=ByteOrder.Z64,
    fill=None,
):
    head = bytearray(0x40)
    head[0:4] = b"\x80\x37\x12\x40"
    struct.pack_into(">I", head, 0x04, 0x0000000F)
    struct.pack_into(">I", head, 0x10, crc1)
    struct.pack_into(">I", head, 0x14, crc2)
    head[0x20:0x34] = title.ljust(20).encode("ascii")[:20]
    head[0x3B] = ord("N")
    head[0x3C:0x3E] = cart.encode("ascii")[:2]
    head[0x3E] = ord(region)
    head[0x3F] = version
    tail = fill if fill is not None else bytes(size - 0x40)
    body = bytes(head) + tail[: size - 0x40].ljust(size - 0x40, b"\x00")
    return swap(body, order)


def entropy(length):
    out = bytearray(length)
    state = 0x12345678
    for i in range(length):
        state = (state * 1103515245 + 12345) & 0xFFFFFFFF
        out[i] = (state >> 16) & 0xFF
    return bytes(out)


def mode_entry(
    ctrl=NTSC_LAN1_CTRL, width=320, vsync=525, hsync=3093, burst=0x03E52239, kind=0x02000000
):
    return struct.pack(
        ">20I",
        kind,
        ctrl,
        width,
        burst,
        vsync,
        hsync,
        0x0C150C15,
        0x006C02EC,
        0x00000200,
        0x00000000,
        *FIELD_REGISTERS,
        *FIELD_REGISTERS,
    )


def ori_setting(bit):
    return (0x0D << 26) | (25 << 21) | (8 << 16) | bit


SWITCH_BLOCKS = {
    "dedither": {"flag": 0x0040, "setter_word": LUI_AT_ONE},
    "divot": {"flag": 0x0010, "setter_word": ori_setting(0x0010)},
    "gamma dither": {"flag": 0x0004, "setter_word": ori_setting(0x0004)},
    "gamma": {"flag": 0x0001, "setter_word": ori_setting(0x0008)},
}


def switch_block(base, *, flag=0x0040, branch_op=0x04, guard_gap=1, skip=6, setter_word=LUI_AT_ONE):
    guard = base
    branch = base + guard_gap * 4
    setter = branch + 8
    words = {
        guard: 0x30000000 | (4 << 21) | (14 << 16) | flag,
        branch: (branch_op << 26) | (14 << 21) | skip,
        setter: setter_word,
    }
    return words, guard, branch, setter


def routine(names=("dedither",), **block):
    blob = bytearray(0x400)
    for index, immediate in enumerate(MASK_IMMEDIATES):
        struct.pack_into(">I", blob, ROUTINE_AT + index * 8, ADDIU_AT | immediate)
    placed = {}
    for index, name in enumerate(names):
        words, guard, branch, setter = switch_block(
            BLOCKS_AT + index * BLOCK_STRIDE, **{**SWITCH_BLOCKS[name], **block}
        )
        for at, value in words.items():
            struct.pack_into(">I", blob, at, value)
        placed[name] = (guard, branch, setter)
    return bytes(blob), placed


def sealable_rom(*, table_at=0x2000, routine_at=None, names=("dedither",), entry=None):
    from n64_video_interface.seal import reseal

    data = bytearray(make_rom(size=SEALABLE_SIZE))
    if table_at is not None:
        placed_entry = entry if entry is not None else mode_entry()
        data[table_at : table_at + len(placed_entry)] = placed_entry
    guards = {}
    if routine_at is not None:
        blob, placed = routine(names)
        data[routine_at : routine_at + len(blob)] = blob
        guards = {name: routine_at + guard for name, (guard, _, _) in placed.items()}
    return reseal(bytes(data), "6102"), guards


@pytest.fixture
def rom_factory():
    return make_rom
