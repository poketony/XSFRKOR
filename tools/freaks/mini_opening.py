"""Opening-only title anchors and the private palette of the hero sprite."""

import struct

from elf_probe import Elf
from unpack_disc import require


HERO = 15
TITLE_SHEETS = (11, 12, 206)
# Measured from 206_KOR relative to its yellow plate, then mapped to the
# opening plate's native rectangle. The subtitle animation is independent.
TITLE_CENTERS = ((202, 256), (279, 260), (354, 256), (430, 256))
OLD_CENTERS = ((223, 266), (294, 257), (355, 275), (407, 259))


def hero_palette(sprites):
    texture = sprites.textures[HERO]
    colors = bytearray(sprites.files["clutimg.bin"])
    base = texture.palette_start * 64
    require((texture.colors, texture.palettes, texture.sprite_start, texture.sprites) == (16, 1, 78, 1)
            and set(texture.indices()) == {0, 1, 2}
            and colors[base:base + 12] == bytes((0, 0, 0, 0, 24, 16, 96, 128, 176, 104, 200, 128)),
            "MINI hero texture/palette no longer matches the opening sprite")
    require(all(t.index == HERO or not
                (t.palette_start * 64 < base + 64 and base < t.palette_start * 64 + t.colors * 4 * t.palettes)
                for t in sprites.textures), "MINI hero palette is not private")
    require(colors[base + 12:base + 64] == b"\0\0\0\x80" * 13,
            "Unexpected colors in the MINI hero's unused palette slots")
    # Intermediate outline/fill colors and half-alpha edge colors, without dithering.
    ramp = [tuple(round(a + (b - a) * i / 11) for a, b in zip((24, 16, 96), (176, 104, 200))) + (128,)
            for i in range(1, 11)]
    ramp += [(24, 16, 96, 64), (100, 60, 148, 64), (176, 104, 200, 64)]
    colors[base + 12:base + 64] = bytes(c for entry in ramp for c in entry)
    return bytes(colors)


def patch_title(data):
    elf = Elf(data)
    output = bytearray(data)

    def imm(address, before, after):
        offset = elf.offset(address)
        require(struct.unpack_from("<I", data, offset)[0] == before,
                f"Original MINI opening instruction differs at {address:08X}")
        struct.pack_into("<I", output, offset, before & 0xffff0000 | after)

    def fhi(value):
        return struct.unpack("<I", struct.pack("<f", value))[0] >> 16

    # Three paths: sliding in, scaling around x=112, and the final hold.
    slides = (0x1762e4, 0x1764a4, 0x1764b8, 0x1764cc)
    slide_ops = (0x26450000, 0x26850000, 0x26850000, 0x26850000)
    scale_x = (0x176318, 0x176518, 0x1765b4, 0x176630)
    hold_x = (0x1763b8, 0x17672c, 0x176740, 0x176754)
    phases_y = ((0x1762f4, 0x1763a0, 0x1763c0),
                (0x1764a8, 0x1765a0, 0x176730),
                (0x1764bc, 0x17661c, 0x176744),
                (0x1764d0, 0x17669c, 0x176758))
    for i, ((old_x, old_y), (x, y)) in enumerate(zip(OLD_CENTERS, TITLE_CENTERS)):
        imm(slides[i], slide_ops[i] | old_x, x)
        imm(hold_x[i], 0x24050000 | old_x, x)
        imm(scale_x[i], 0x3c010000 | fhi(old_x - 112), fhi(x - 112))
        for address in phases_y[i]:
            imm(address, 0x24060000 | old_y, y)
    # The last original anchor used a fractional float from LUI/ORI.
    imm(0x176634, 0x34218000, 0)
    return bytes(output)
