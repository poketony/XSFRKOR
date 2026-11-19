"""Stage-local Korean tile bitmaps; keep the original UVs and palette variants."""

import struct

from PIL import Image, ImageDraw, ImageFilter, ImageChops

import mini_font
from mini_sprites import Sprites
from unpack_disc import require
from verify_codecs import Native
import xs2_hangul
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_GP, UC_MIPS_REG_PC,
                               UC_MIPS_REG_RA, UC_MIPS_REG_SP, UC_MIPS_REG_V0)


def native_handles(elf_data, count):
    require(0 < count <= 72, "Tile handle audit exceeds 72 symbols")
    machine = Native(elf_data)
    calls = []

    def submit(uc, address, size, user):
        calls.append(uc.reg_read(UC_MIPS_REG_A0) & 0xffffffff)
        uc.reg_write(UC_MIPS_REG_V0, 0)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    machine.uc.hook_add(UC_HOOK_CODE, submit, begin=0x10b4b0, end=0x10b4b0)
    result = {}
    for name, kind, first in (("large", 156, 260), ("small", 84, 332)):
        result[name] = []
        for token in range(count):
            state = bytearray(32)
            state[0], state[3] = kind + token, 1
            struct.pack_into("<3I", state, 8, 100, 100, 0x3f800000)
            machine.uc.mem_write(machine.SOURCE, bytes(state))
            for reg, value in ((UC_MIPS_REG_A0, machine.SOURCE), (UC_MIPS_REG_GP, machine.elf.gp),
                               (UC_MIPS_REG_RA, machine.STOP), (UC_MIPS_REG_SP, machine.STACK)):
                machine.uc.reg_write(reg, value)
            calls.clear()
            machine.uc.emu_start(0x11e3a8, machine.STOP, count=1000)
            require(machine.uc.reg_read(UC_MIPS_REG_PC) == machine.STOP
                    and machine.uc.reg_read(UC_MIPS_REG_SP) == machine.STACK
                    and calls == [(first + token) << 8], "Native tile type selected a different sprite")
            require(bytes(machine.uc.mem_read(machine.SOURCE, 32)) == state, "Tile draw changed its input")
            result[name].append(calls[0] >> 8)
    return result


def glyph_pixels(pixels, size, large):
    mask = Image.frombytes("L", (16, 16), bytes(v * 85 for v in pixels))
    factor = 3 if large else 2
    mask = mask.resize((16 * factor, 16 * factor), Image.Resampling.NEAREST)
    canvas = Image.new("L", (size, size))
    canvas.paste(mask, ((size - mask.width) // 2, (size - mask.height) // 2))
    outline = canvas.filter(ImageFilter.MaxFilter(3))
    shadow = ImageChops.offset(outline, 1, 1)
    return bytes((12 if large else 9) if core >= 170 else 8 if core else 4 if edge >= 170
                 else 1 if edge else 15 if shade else 0
                 for core, edge, shade in zip(canvas.tobytes(), outline.tobytes(), shadow.tobytes()))


def build_tiles(sprites, alphabet, handles):
    require(len(set(alphabet)) == len(alphabet) and 0 < len(alphabet) <= 72, "Invalid Korean tile alphabet")
    all_chars = [c for c, _ in xs2_hangul.rows()]
    glyphs = mini_font.render_glyphs(all_chars, mini_font.DEFAULT_FONT)
    require(set(alphabet) <= glyphs.keys(), "Unsupported Hangul tile")
    output = bytearray(sprites.files["textureimg.bin"])
    indices, masks, changed_sprites = {}, {}, []
    for name, large, size in (("large", True, 60), ("small", False, 32)):
        require(len(handles[name]) == len(alphabet), "Tile handles and alphabet disagree")
        for char, sprite_id in zip(alphabet, handles[name]):
            fields = struct.unpack_from("<16H", sprites.files["spritetbl.bin"], sprite_id * 32)
            texture, u, v, width, height = fields[0], *fields[6:10]
            item = sprites.textures[texture]
            require(texture in range(46, 53) and not item.flags and (width, height) == (size, size)
                    and u + width <= item.width and v + height <= item.height,
                    "Korean tile no longer matches the original raw 4-bit sprite cell")
            if texture not in indices:
                indices[texture] = bytearray(item.indices())
                masks[texture] = bytearray(item.width * item.height)
            pixels = glyph_pixels(glyphs[char], size, large)
            for row in range(size):
                at = (v + row) * item.width + u
                require(not any(masks[texture][at:at + size]), "Korean tile sprite regions overlap")
                indices[texture][at:at + size] = pixels[row * size:(row + 1) * size]
                masks[texture][at:at + size] = b"\1" * size
            changed_sprites.append(sprite_id)
    for texture, pixels in indices.items():
        item = sprites.textures[texture]
        packed = bytes(pixels[i] | pixels[i + 1] << 4 for i in range(0, len(pixels), 2))
        require(len(packed) == item.end - item.offset, "Korean tile texture size changed")
        output[item.offset:item.end] = packed
    rebuilt = Sprites(dict(sprites.files, **{"textureimg.bin": bytes(output)}))
    for item in sprites.textures:
        after = rebuilt.textures[item.index]
        if item.index not in masks:
            require(after.pixels == item.pixels, "Unedited texture changed")
        else:
            require(all(a == b or owned for a, b, owned in zip(item.indices(), after.indices(), masks[item.index])),
                    "Pixels outside Korean sprite cells changed")
    return rebuilt, {"textures": sorted(indices), "changed_sprite_cells": changed_sprites,
                     "palettes_uv_dimensions_other_sprites": "preserved",
                     "native_type_to_sprite_cases": len(alphabet) * 2,
                     "scope": "Native graphic-type sprite dispatch; board/inventory type construction and GPU not run"}


def comparison(before, after, handles, title="Puzzle letter sprites: original / Korean | Actual rebuilt WWROM cells"):
    image = Image.new("RGB", (1120, 390), (30, 32, 36))
    draw = ImageDraw.Draw(image)
    draw.text((20, 10), title, fill="white")
    for y, name, factor in ((40, "large", 1), (220, "small", 2)):
        for row, (label, source) in enumerate((("Original", before), ("Korean", after))):
            draw.text((20, y + row * 78 + 24), label, fill="white")
            for column, sprite_id in enumerate(handles[name][:12]):
                fields = struct.unpack_from("<16H", source.files["spritetbl.bin"], sprite_id * 32)
                texture, u, v, width, height = fields[0], *fields[6:10]
                tile = source.image(texture).crop((u, v, u + width, v + height))
                tile = tile.resize((width * factor, height * factor), Image.Resampling.NEAREST)
                image.paste(tile, (110 + column * 82, y + row * 78), tile)
    return image
