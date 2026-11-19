"""Private menu-title glyphs; preserve original renderers and table capacities."""

import struct

from elf_probe import Elf
from unpack_disc import require


TEXTURES = (38, 84, 127)
GLYPHS = dict(zip(range(184, 193), ("플", "레", "이", "방", "법", "설", "명", "", "")))
GLYPHS.update(zip(range(655, 670), ("플", "이", "방", "법", "해", "를", "선", "택", "줘", "!", "을", "스", "테", "레", "지")))
GLYPHS.update(zip(range(934, 942), ("잠", "깐", "쉬", "어", "가", "기", "", "")))
BLANKS = {38: 191, 127: 940}
# name, texture, address, mode, original glyphs, palette, caption, new sequence
CASES = (
    ("help_full", 38, 0x1c6a90, "tree", tuple(range(192, 183, -1)), 0,
     "플레이 방법 설명", (184, 185, 186, None, 187, 188, None, 189, 190)),
    ("help_short", 38, 0x1c6b58, "tree", tuple(range(188, 183, -1)), 0,
     "플레이 방법", (184, 185, 186, None, 187, 188)),
    ("stage_pick", 84, 0x216718, "rows", (*range(666, 670), *range(660, 666)), 0,
     "스테이지를 선택해 줘!", (666, 667, 656, 669, 660, None, 661, 662, 659, None, 663, 664)),
    ("stage_pick_selected", 84, 0x216790, "rows", (*range(666, 670), *range(660, 666)), 1,
     "스테이지를 선택해 줘!", (666, 667, 656, 669, 660, None, 661, 662, 659, None, 663, 664)),
    ("stage_pick_alternate", 84, 0x216808, "rows", (*range(666, 670), *range(660, 666)), 0,
     "스테이지를 선택해 줘!", (666, 667, 656, 669, 660, None, 661, 662, 659, None, 663, 664)),
    ("mode_pick", 84, 0x2168a0, "rows", tuple(range(655, 666)), 0,
     "플레이 방법을 선택해 줘!", (655, 668, 656, None, 657, 658, 665, None, 661, 662, 659, None, 663, 664)),
    ("pause", 127, 0x21a640, "scaled", tuple(range(934, 942)), 0,
     "잠깐 쉬어가기", (934, 935, None, 936, 937, 938, 939)),
)


def requested(sprites, edits):
    return {i for i in TEXTURES if i in edits and edits[i].convert("RGBA").tobytes() != sprites.image(i).tobytes()}


def source_tables(data):
    elf, result = Elf(data), {}
    for name, index, address, mode, numbers, palette, _, _ in CASES:
        stride = 12 if mode == "rows" else 20
        fmt = "<Ihh" if mode == "tree" else "<Iii"
        start = elf.offset(address)
        rows = [list(struct.unpack_from(fmt, data, start + slot * stride)) for slot in range(len(numbers))]
        require([r[0] for r in rows] == [(n << 8) | palette for n in numbers],
                f"Original MINI heading table differs: {name}")
        if mode == "tree":
            require([struct.unpack_from("<H", data, start + k * 20 + 18)[0] for k in range(len(numbers))]
                    == list(range(len(numbers), 0, -1)), "MINI heading tree counts differ")
            parent = struct.unpack_from("<IhhHH", data, start + len(numbers) * 20)
            require(parent == ((0, 323, 57, 346, 50) if name == "help_full" else (0, 250, 57, 125, 50)),
                    "MINI heading parent origin/bounds differ")
        elif mode == "scaled":
            require(all(struct.unpack_from("<i", data, start + k * 20 + 16)[0] == row[1] - 320
                        for k, row in enumerate(rows)), "MINI pause scale origin differs")
        result[name] = rows
    return result


def geometry(sprites, images, number):
    f = struct.unpack_from("<16H", sprites.files["spritetbl.bin"], number * 32)
    require(f[0] in TEXTURES and f[2] == f[3] and f[4] * 2 == f[2] and f[5] * 2 == f[3]
            and f[6] + f[8] <= images[f[0]].width and f[7] + f[9] <= images[f[0]].height
            and f[2:4] == f[8:10], "MINI heading glyph geometry differs")
    u, v, w, h = f[6:10]
    box = images[f[0]].crop((u, v, u + w, v + h)).getchannel("A").getbbox()
    return None if box is None else tuple(value - f[4 if axis % 2 == 0 else 5] for axis, value in enumerate(box))


def visible_bounds(rows, shapes):
    occupied = [(x + shapes[h >> 8][0], y + shapes[h >> 8][1],
                 x + shapes[h >> 8][2], y + shapes[h >> 8][3])
                for h, x, y in rows if shapes[h >> 8] is not None]
    require(occupied, "MINI heading has no visible letters")
    return [min(p[0] for p in occupied), min(p[1] for p in occupied),
            max(p[2] for p in occupied), max(p[3] for p in occupied)]


def layout(sprites, indices):
    import wwrom
    from mini_sprites import originals

    require(set(indices) <= set(TEXTURES), "Unknown MINI heading texture")
    source = originals()
    tables = source_tables(wwrom.originals()[0])
    old_images = {i: source.image(i) for i in TEXTURES}
    images = {i: sprites.image(i) for i in TEXTURES}
    shapes = {n: geometry(sprites, images, n) for n in GLYPHS}
    result = []
    for name, index, address, mode, numbers, palette, caption, sequence in CASES:
        if index not in indices:
            continue
        owned = [n for n in GLYPHS if struct.unpack_from("<H", sprites.files["spritetbl.bin"], n * 32)[0] == index]
        require(all((shapes[n] is not None) == bool(GLYPHS[n]) for n in owned),
                "MINI heading letter/blank coverage differs")
        require("".join(" " if n is None else GLYPHS[n] for n in sequence) == caption,
                "MINI heading spelling differs")
        original = tables[name]
        old_shapes = {n: geometry(source, old_images, n) for n in numbers}
        before = visible_bounds(original, old_shapes)
        gap, space = (6, 18) if index == 127 else (3, 14)
        active, cursor = [], 0
        for number in sequence:
            if number is None:
                cursor += space
                continue
            left, _, right, _ = shapes[number]
            active.append([number, cursor - left])
            cursor += right - left + gap
        require(len(active) <= len(original), "MINI heading table capacity exceeded")
        width = cursor - gap
        origin = (before[0] + before[2] - width) / 2
        slots = sorted(range(len(original)), key=lambda k: original[k][1])
        require(index in BLANKS or len(active) == len(original), "MINI heading needs an unused blank slot")
        rows = [[(BLANKS[index] << 8) | palette, x, y] for _, x, y in original] if index in BLANKS else [list(r) for r in original]
        for (number, x), slot in zip(active, slots):
            rows[slot] = [(number << 8) | palette, round(origin + x), original[slot][2]]
        after = visible_bounds(rows, shapes)
        require(abs(after[0] + after[2] - before[0] - before[2]) <= 1,
                "MINI heading centre moved")
        require(0 < after[2] - after[0] <= 620, "MINI heading exceeds screen width")
        result.append({"name": name, "index": index, "address": address, "caption": caption,
                       "rows": rows, "active_glyphs": len(active), "gap_pixels": gap,
                       "space_pixels": space + gap, "visible_bounds": after,
                       "source_visible_bounds": before, "mode": mode})
    return result


def patch_tables(data, captions):
    import wwrom
    from mini_sprites import originals as original_sprites

    source = wwrom.originals()[0]
    sprites = original_sprites()
    originals = source_tables(source)
    cases = {row[0]: row for row in CASES}
    require(len({r["name"] for r in captions}) == len(captions), "Duplicate MINI heading table")
    elf, old_elf, output = Elf(data), Elf(source), bytearray(data)
    for caption in captions:
        require(caption["name"] in cases, "Unknown MINI heading table")
        name, index, address, mode, numbers, palette, text, _ = cases[caption["name"]]
        rows = caption["rows"]
        require(caption["index"] == index and caption["address"] == address and caption["caption"] == text
                and len(rows) == len(numbers), "MINI heading table identity/count differs")
        stride = 12 if mode == "rows" else 20
        size = len(numbers) * stride + (20 if mode == "tree" else 0)
        start, old_start = elf.offset(address), old_elf.offset(address)
        require(data[start:start + size] == source[old_start:old_start + size],
                f"Original MINI heading table differs: {name}")
        for slot, (handle, x, y) in enumerate(rows):
            require(handle >> 8 in GLYPHS and handle & 255 == palette and y == originals[name][slot][2]
                    and struct.unpack_from("<H", sprites.files["spritetbl.bin"], (handle >> 8) * 32)[0] == index
                    and -640 <= x <= 640, "MINI heading row identity/position differs")
            struct.pack_into("<Ih" if mode == "tree" else "<Ii", output, start + slot * stride, handle, x)
            if mode == "scaled":
                struct.pack_into("<i", output, start + slot * stride + 16, x - 320)
    return bytes(output)
