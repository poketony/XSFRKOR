"""Record-screen shared glyphs and two fixed-capacity title tables."""

import struct

from elf_probe import Elf
from mini_headings import visible_bounds
from unpack_disc import require

TEXTURE = 151
UPDATE_SLOTS = (0, 1, 3, 4, 5, 7, 8, 9)
GLYPHS = dict(zip(range(1106, 1119), ("하", "이", "스", "코", "어", "갱", "신", "", "!", "테", "", "지", "의")))
# name, address, stride, original handles, caption, new glyphs/spaces
CASES = (
    ("stage_record", 0x1c56a0, 20, (1110, 1109, 1108, 1107, 1106, 1118, 1117, 1116, 1115, 1108),
     "스테이지의 하이 스코어", (1108, 1115, 1107, 1117, 1118, None, 1106, 1107, None, 1108, 1109, 1110)),
    ("record_update", 0x1cc970, 12, (1106, 1107, 1108, 1109, 1110, 1109, 1111, 1112, 1113, 1114),
     "하이 스코어 갱신!", (1106, 1107, None, 1108, 1109, 1110, None, 1111, 1112, 1114)),
)


def requested(sprites, edits):
    return TEXTURE in edits and edits[TEXTURE].convert("RGBA").tobytes() != sprites.image(TEXTURE).tobytes()


def source_tables(data):
    elf, result = Elf(data), {}
    for name, address, stride, numbers, _, _ in CASES:
        at = elf.offset(address)
        rows = [list(struct.unpack_from("<Ihh" if stride == 20 else "<Iii", data, at + k * stride))
                for k in range(len(numbers))]
        require([r[0] for r in rows] == [n << 8 for n in numbers], "Original record title handles differ")
        if stride == 20:
            require([struct.unpack_from("<H", data, at + k * stride + 18)[0] for k in range(10)]
                    == list(range(10, 0, -1)) and data[at + 200:at + 220] == bytes(20),
                    "Original record title tree counts/parent differ")
        result[name] = rows
    return result


def geometry(sprites, number):
    f = struct.unpack_from("<16H", sprites.files["spritetbl.bin"], number * 32)
    require(f[0] == TEXTURE and f[2:4] == f[8:10], "Record title geometry differs")
    u, v, w, h = f[6:10]
    box = sprites.image(TEXTURE).crop((u, v, u + w, v + h)).getchannel("A").getbbox()
    return None if box is None else tuple(p - f[4 + k % 2] for k, p in enumerate(box))


def layout(sprites):
    from mini_sprites import originals
    import wwrom
    source = originals()
    for number in GLYPHS:
        at = number * 32
        require(sprites.files["spritetbl.bin"][at:at + 32] == source.files["spritetbl.bin"][at:at + 32],
                "Record title sprite table changed")
    old = source_tables(wwrom.originals()[0])
    shapes = {n: geometry(sprites, n) for n in GLYPHS}
    require(all(bool(GLYPHS[n]) == (shapes[n] is not None) for n in GLYPHS), "Record title letter/blank differs")
    result = []
    for name, address, stride, numbers, caption, sequence in CASES:
        require("".join(" " if n is None else GLYPHS[n] for n in sequence) == caption, "Record title spelling differs")
        before = visible_bounds(old[name], {n: geometry(source, n) for n in numbers})
        active, cursor = [], 0
        for n in sequence:
            if n is None:
                cursor += 14
            else:
                left, _, right, _ = shapes[n]
                active.append((n, cursor - left))
                cursor += right - left + 3
        origin = (before[0] + before[2] - cursor + 3) / 2
        rows = [[1113 << 8, x, y] for _, x, y in old[name]]
        slots = sorted(range(10), key=lambda k: old[name][k][1])
        if name == "record_update":
            # Spread the shorter title over the original arc; keep ! in its special final slot.
            slots = UPDATE_SLOTS
        for (n, x), slot in zip(active, slots):
            rows[slot] = [n << 8, round(origin + x), old[name][slot][2]]
        after = visible_bounds(rows, shapes)
        require(abs(after[0] + after[2] - before[0] - before[2]) <= 1
                and 0 <= after[0] < after[2] <= 640, "Record title centre/bounds differ")
        result.append(dict(name=name, address=address, stride=stride, caption=caption, rows=rows,
                           active_glyphs=len(active), gap_pixels=3, space_pixels=17,
                           visible_bounds=after, source_visible_bounds=before))
    patch_tables(wwrom.originals()[0], result)
    return result


def patch_tables(data, captions):
    import wwrom
    source = wwrom.originals()[0]
    elf, original, output = Elf(data), Elf(source), bytearray(data)
    tables = source_tables(source)
    require(len(captions) == 2 and {r["name"] for r in captions} == {c[0] for c in CASES},
            "Record title table set differs")
    for name, address, stride, numbers, text, sequence in CASES:
        caption = next(r for r in captions if r["name"] == name)
        rows = caption["rows"]
        at, old_at = elf.offset(address), original.offset(address)
        size = 10 * stride + (20 if stride == 20 else 0)
        require(caption["address"] == address and caption["stride"] == stride
                and caption["caption"] == text and len(rows) == 10
                and data[at:at + size] == source[old_at:old_at + size], "Record title table identity/preimage differs")
        expected = [n for n in sequence if n is not None]
        require([h >> 8 for h, _, _ in sorted(rows, key=lambda r: r[1]) if h >> 8 != 1113] == expected,
                "Record title glyph order differs")
        if name == "record_update":
            require(rows[9][0] == 1114 << 8, "Record title exclamation animation slot changed")
            require(tuple(k for k, r in enumerate(rows) if r[0] >> 8 != 1113) == UPDATE_SLOTS,
                    "Record title active animation slots changed")
        for k, (h, x, y) in enumerate(rows):
            require(h & 255 == 0 and h >> 8 in GLYPHS and y == tables[name][k][2] and 0 <= x <= 640,
                    "Record title row position/identity differs")
            struct.pack_into("<Ih" if stride == 20 else "<Ii", output, at + k * stride, h, x)
    return bytes(output)
