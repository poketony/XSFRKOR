"""Stage-return yes/no lettering; one relocated word sprite, original frame trees."""

import struct

from PIL import Image
from elf_probe import Elf
from unpack_disc import require

TEXTURE = 102
NO_SPRITE = 759
OLD_UV = (91, 0, 41, 45)
NEW_UV = (0, 80, 136, 45)
# name, tree, original text rows, retained word row, frame interior
CASES = (("yes", 0x1c3dd8, (4, 5), 4, (123, 285)),
         ("no", 0x1c4168, (4, 5, 6), 6, (354, 516)))


def requested(sprites, edits):
    return TEXTURE in edits and edits[TEXTURE].convert("RGBA").tobytes() != sprites.image(TEXTURE).tobytes()


def prepare_table(sprites, table):
    at = NO_SPRITE * 32
    require(table[at:at + 32] == sprites.files["spritetbl.bin"][at:at + 32], "Choice word descriptor preimage differs")
    f = list(struct.unpack_from("<16H", table, at))
    require(f[0] == TEXTURE and tuple(f[2:10]) == (41, 45, 0, 0, *OLD_UV), "Original choice word geometry differs")
    f[2], f[3], f[6], f[7], f[8], f[9] = NEW_UV[2], NEW_UV[3], *NEW_UV
    out = bytearray(table)
    struct.pack_into("<16H", out, at, *f)
    return bytes(out)


def geometry(sprites, number):
    f = struct.unpack_from("<16H", sprites.files["spritetbl.bin"], number * 32)
    require(f[0] == TEXTURE and f[2:4] == f[8:10] and f[4:6] == (0, 0), "Choice glyph geometry differs")
    texture = sprites.textures[TEXTURE]
    image = Image.frombytes("L", (texture.width, texture.height), texture.indices())
    u, v, w, h = f[6:10]
    return image.crop((u, v, u + w, v + h)).point(lambda p: 0 if p in (0, 15) else 255).getbbox()


def source_trees(data):
    elf, result = Elf(data), {}
    expected = {"yes": (757, 758, 761), "no": (758, 758, 759)}
    for name, address, _, _, _ in CASES:
        at = elf.offset(address)
        rows = [list(struct.unpack_from("<Ihh6H", data, at + k * 20)) for k in range(15)]
        require([r[0] >> 8 for r in rows[4:7]] == list(expected[name])
                and all(r[0] == 760 << 8 for r in rows[:4])
                and [r[-1] for r in rows[:14]] == list(range(14, 0, -1)) and not any(rows[14]),
                "Original choice tree differs")
        result[name] = rows
    return result


def layout(sprites):
    from mini_sprites import originals
    import wwrom
    source = originals()
    trees = source_trees(wwrom.originals()[0])
    f = struct.unpack_from("<16H", sprites.files["spritetbl.bin"], NO_SPRITE * 32)
    require(f[2:10] == (136, 45, 0, 0, *NEW_UV), "Relocated choice word geometry differs")
    require(geometry(sprites, 758) is None and geometry(sprites, 757) is not None
            and geometry(sprites, 759) is not None, "Choice word/blank coverage differs")
    result = []
    for name, address, slots, retained, interior in CASES:
        rows = trees[name]
        boxes = [(row[1] + geometry(source, row[0] >> 8)[0], row[1] + geometry(source, row[0] >> 8)[2])
                 for row in (rows[k] for k in slots)]
        before = [min(b[0] for b in boxes), max(b[1] for b in boxes)]
        number, old_x, y = rows[retained][:3]
        ink = geometry(sprites, number >> 8)
        x = round((sum(before) - ink[0] - ink[2]) / 2)
        width = struct.unpack_from("<H", sprites.files["spritetbl.bin"], (number >> 8) * 32 + 4)[0]
        require(interior[0] <= x and x + width <= interior[1], "Choice word background crosses its frame")
        after = [x + ink[0], x + ink[2]]
        require(abs(sum(before) - sum(after)) <= 1, "Choice text centre moved")
        result.append(dict(name=name, address=address, row=retained, handle=number, x=x, y=y, old_x=old_x,
                           caption="예" if name == "yes" else "아니요", source_bounds=before, visible_bounds=after))
    patch_tables(wwrom.originals()[0], result)
    return result


def patch_tables(data, captions):
    import wwrom
    source = wwrom.originals()[0]
    elf, original, out = Elf(data), Elf(source), bytearray(data)
    trees = source_trees(source)
    require(len(captions) == 2 and {r["name"] for r in captions} == {"yes", "no"}, "Choice table set differs")
    for name, address, _, retained, interior in CASES:
        row = next(r for r in captions if r["name"] == name)
        at, old = elf.offset(address), original.offset(address)
        h, x, y = trees[name][retained][:3]
        require(data[at:at + 300] == source[old:old + 300] and row["address"] == address
                and row["row"] == retained and (row["handle"], row["old_x"], row["y"]) == (h, x, y)
                and row["caption"] == ("예" if name == "yes" else "아니요")
                and interior[0] <= row["x"] and row["x"] + (49 if name == "yes" else 136) <= interior[1],
                "Choice tree identity/preimage/position differs")
        require(abs(sum(row["source_bounds"]) - sum(row["visible_bounds"])) <= 1, "Choice text centre moved")
        struct.pack_into("<h", out, at + retained * 20 + 4, row["x"])
    return bytes(out)
