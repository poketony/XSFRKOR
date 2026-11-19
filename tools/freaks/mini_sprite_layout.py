"""Readable MINI banner canvases, mapped back to the original sprite rectangles."""

import struct

from PIL import Image

from elf_probe import Elf
from unpack_disc import require


ASSEMBLED = {18: (85, 9), 54: (405, 10), 112: (842, 6), 285: (1969, 3), 290: (1986, 5), 291: (1991, 4), 292: (1995, 6),
             293: (2001, 3), 296: (2006, 5), 297: (2011, 5)}

# 0x12E9C8 draws these variants separately, sharing the two bubble backgrounds.
FEEDBACK_BACKGROUNDS = ((405, 0, 0), (405, 104, 0), (405, 208, 0),
                        (406, 0, 232), (406, 184, 232))
FEEDBACK_TEXT = {407: (136, 24), 408: (48, 24), 409: (24, 56),
                 410: (216, 272), 411: (32, 264), 412: (240, 32),
                 413: (240, 104), 414: (312, 0)}


def patch_feedback_button(data):
    elf, result = Elf(data), bytearray(data)
    # Selector 5: centre both circle frames over the Korean column with a 5px gap.
    for address, before, after in ((0x12eb6c, 0x24a5000c, 0x24a50006),
                                   (0x12eb70, 0x24c6fefd, 0x24c6fef8)):
        offset = elf.offset(address)
        require(struct.unpack_from("<I", data, offset)[0] == before,
                "MINI feedback button coordinate instruction differs")
        struct.pack_into("<I", result, offset, after)
    return bytes(result)


def translated_rule_table(sprites, edits, table):
    if 114 not in edits or edits[114].convert("RGBA").tobytes() == sprites.image(114).tobytes():
        return table
    start = 854 * 32
    original = sprites.files["spritetbl.bin"][start:start + 32]
    fields = struct.unpack("<16H", original)
    require(fields[0] == 114 and fields[2:10] == (200, 35, 0, 0, 0, 0, 200, 35)
            and table[start:start + 32] == original, "MINI rule caption source geometry differs")
    require(sprites.image(114).crop((200, 0, 201, 35)).getchannel("A").getbbox() is None,
            "MINI rule caption's extra seam column is not unused")
    # Two original draws start 201px apart. Include the previously omitted
    # pixel in both logical/UV width so a Korean glyph can span the seam 1:1.
    result = bytearray(table)
    struct.pack_into("<H", result, start + 4, 201)
    struct.pack_into("<H", result, start + 16, 201)
    return bytes(result)


def layout(sprites, index):
    require(index in ASSEMBLED, "MINI texture has no assembled editor layout")
    first, count = ASSEMBLED[index]
    item = sprites.textures[index]
    require((item.sprite_start, item.sprites) == (first, count), "MINI banner sprite range changed")
    result = []
    cursor = 0
    for number in range(count):
        fields = struct.unpack_from("<16H", sprites.files["spritetbl.bin"], (first + number) * 32)
        u, v, width, height = fields[6:10]
        require(fields[0] == index and u + width <= item.width and v + height <= item.height,
                "MINI banner UV is outside its source texture")
        if index == 54:
            if first + number not in FEEDBACK_TEXT:
                continue
            x, y = FEEDBACK_TEXT[first + number]
        elif index == 18:
            # Only the title is continuous; keep prompts and decorative pieces separate.
            x, y = ((0, 0), (256, 0), (0, 72), (168, 72), (0, 104),
                    (0, 136), (29, 136), (58, 136), (87, 136))[number]
        elif index == 112:
            # The wide START prompt spans two atlas rows; the outlined prompt is separate.
            x, y = ((0, 0), (0, 16), (0, 40), (143, 40), (0, 76), (0, 104))[number]
        else:
            x, y = ((0, 0), (228, 0), (102, 64))[number] if index == 285 else (cursor, 0)
        result.append((u, v, width, height, x, y))
        cursor += width
    size = (max(x + w for _, _, w, _, x, _ in result), max(y + h for _, _, _, h, _, y in result))
    if index == 54:
        size = (364, 336)
    return result, size


def image(sprites, index):
    source = sprites.image(index)
    if index not in ASSEMBLED:
        return source
    pieces, size = layout(sprites, index)
    result = Image.new("RGBA", size)
    if index == 54:
        # Preview-only backgrounds: never bake lettering into a shared bubble.
        for sprite, x, y in FEEDBACK_BACKGROUNDS:
            fields = struct.unpack_from("<16H", sprites.files["spritetbl.bin"], sprite * 32)
            u, v, width, height = fields[6:10]
            result.alpha_composite(source.crop((u, v, u + width, v + height)), (x, y))
    for u, v, width, height, x, y in pieces:
        result.alpha_composite(source.crop((u, v, u + width, v + height)), (x, y))
    return result


def to_texture(sprites, index, edited):
    if index not in ASSEMBLED:
        return edited
    source = sprites.image(index)
    if edited.size == source.size:
        return edited
    pieces, size = layout(sprites, index)
    require(edited.size == size,
            f"MINI banner {index:03} must remain {size[0]}x{size[1]} (legacy {source.width}x{source.height} also accepted)")
    before, after = list(image(sprites, index).getdata()), list(edited.convert("RGBA").getdata())
    dirty = [a != b and (a[3] != 0 or b[3] != 0) for a, b in zip(before, after)]
    if not any(dirty):
        return source
    owners = [-1] * len(before)
    for number, (_, _, width, height, x, y) in enumerate(pieces):
        for row in range(height):
            at = (y + row) * size[0] + x
            owners[at:at + width] = [number] * width
    error = f"MINI banner {index:03}: paint outside the visible sprite areas would not appear in game"
    if index == 54:
        error = ("MINI bubble 054: edit only the lettering rectangles; bubble backgrounds are preview-only "
                 "(legacy 256x256 input supports background edits)")
    require(not any(changed and owner < 0 for changed, owner in zip(dirty, owners)), error)
    pixels = list(source.getdata())
    for number, (u, v, width, height, x, y) in enumerate(pieces):
        for row in range(height):
            for column in range(width):
                at = (y + row) * size[0] + x + column
                if dirty[at]:
                    # The two-pixel overlap in banner 285 must not draw new pixels twice.
                    pixels[(v + row) * source.width + u + column] = after[at] if owners[at] == number else (0, 0, 0, 0)
    result = Image.new("RGBA", source.size)
    result.putdata(pixels)
    return result
