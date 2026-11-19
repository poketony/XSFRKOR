"""Two ending captions: private glyph slots and bounded native drawing tables."""

import struct

from elf_probe import Elf
from unpack_disc import require


TEXTURES = (192, 193)
TABLES = (0x21c558, 0x21c5c8)
ORIGINAL = (
    ((1547, 117, 347), (1548, 170, 348), (1549, 220, 353), (1550, 287, 349),
     (1551, 339, 363), (1552, 396, 351), (1553, 446, 355), (1554, 470, 355)),
    ((1547, 130, 347), (1555, 197, 354), (1556, 262, 352), (1557, 324, 352),
     (1558, 380, 354), (1553, 429, 355), (1554, 453, 355)),
)
SEQUENCES = ((1547, None, 1548, 1549, 1550, 1551, 1552),
             (1555, 1556, 1557, 1558, 1553, 1551, 1552))
CAPTIONS = ("\uc62c \ud074\ub9ac\uc5b4!!", "\ucd95\ud558\ud569\ub2c8\ub2e4!!")


def requested(sprites, edits):
    changed = {i for i in TEXTURES if i in edits and edits[i].tobytes() != sprites.image(i).tobytes()}
    require(not changed or changed == set(TEXTURES),
            "MINI ending captions need both MINI_SPR_192_KOR.png and MINI_SPR_193_KOR.png")
    return bool(changed)


def layout(sprites):
    from mini_sprites import originals
    source = originals()
    images = {i: sprites.image(i) for i in TEXTURES}
    bounds = {}
    for number in sorted({n for seq in SEQUENCES for n in seq if n is not None}):
        fields = struct.unpack_from("<16H", sprites.files["spritetbl.bin"], number * 32)
        index, (u, v, w, h) = fields[0], fields[6:10]
        require(index in TEXTURES and (w, h) == (64, 64) and fields[2:6] == (64, 64, 0, 0),
                "MINI ending glyph geometry changed")
        box = images[index].crop((u, v, u + w, v + h)).getchannel("A").getbbox()
        require(box is not None, f"MINI ending glyph {number} is blank")
        bounds[number] = box
    result = []
    for caption, seq, original in zip(CAPTIONS, SEQUENCES, ORIGINAL):
        source_boxes = []
        for number, x, y in original:
            f = struct.unpack_from('<16H', source.files['spritetbl.bin'], number * 32)
            u, v, w, h = f[6:10]
            ink = source.image(f[0]).crop((u, v, u+w, v+h)).getchannel('A').getbbox()
            source_boxes.append((x+ink[0], y+ink[1], x+ink[2], y+ink[3]))
        source_box = (min(b[0] for b in source_boxes), min(b[1] for b in source_boxes),
                      max(b[2] for b in source_boxes), max(b[3] for b in source_boxes))
        rows, cursor = [], 0
        for number in seq:
            if number is None:
                cursor += 22
                continue
            left, top, right, bottom = bounds[number]
            rows.append([number << 8, cursor - left, 350])
            cursor += right - left + 6
        width = cursor - 6
        origin = round((source_box[0] + source_box[2] - width) / 2)
        top = min(350 + bounds[r[0] >> 8][1] for r in rows)
        bottom = max(350 + bounds[r[0] >> 8][3] for r in rows)
        dy = round((source_box[1] + source_box[3] - top - bottom) / 2)
        for row in rows:
            row[1] += origin
            row[2] += dy
        box = (origin, top + dy, origin + width, bottom + dy)
        require(0 <= box[0] < box[2] <= 640 and 330 <= box[1] < box[3] <= 420,
                "MINI ending caption exceeds its screen band")
        require(abs(box[0]+box[2]-source_box[0]-source_box[2]) <= 1
                and abs(box[1]+box[3]-source_box[1]-source_box[3]) <= 1,
                'MINI ending visible centre moved')
        result.append({"caption": caption, "rows": rows, "visible_bounds": box,
                       "source_visible_bounds": source_box})
    return result


def pack(rows, capacity):
    require(len(rows) <= capacity, "MINI ending caption table is full")
    return b"".join(struct.pack("<Iii", *row) for row in rows).ljust((capacity + 1) * 12, b"\0")


def patch_tables(data, captions):
    require(len(captions) == 2, "Both MINI ending caption tables are required")
    elf, output = Elf(data), bytearray(data)
    for address, original, caption in zip(TABLES, ORIGINAL, captions):
        start = elf.offset(address)
        before = pack([(n << 8, x, y) for n, x, y in original], len(original))
        require(data[start:start + len(before)] == before, "Original MINI ending table differs")
        output[start:start + len(before)] = pack(caption["rows"], len(original))
    return bytes(output)
