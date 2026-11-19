"""Shared game-message glyphs and bounded original message tables."""

import struct

from elf_probe import Elf
from unpack_disc import require

TEXTURES = (108, 109, 110, 111)
GLYPHS = dict(zip(range(812, 842), (
    "준", "비", "?", "시", "작", "!", "스", "테", "이",
    "지", "클", "리", "어", "", "간", "초", "과", "승",
    "무", "", "부", "패", "배", "게", "임", "종", "료", "", "", "",
)))
BLANK = 825
# Counts remain fixed: unused entries draw the transparent BLANK glyph.
CASES = (
    ("ready", 0x216a68, (812, 813, 814, 815, 816), 0, "준비?", 1),
    ("go", 0x216ab8, (817, 815, 818), 0, "시작!", 1),
    ("stage_clear", 0x216af8, (821, 823, 815, 824, 825, 826, 820, 818), 3, "스테이지 클리어!", 1),
    ("stage_clear_small", 0x216b58, (821, 823, 815, 824, 825, 826, 820, 818), 3, "스테이지 클리어!", 0.625),
    ("time_up", 0x216bd0, (833, 819, 834, 820, 822, 835), 1, "시간 초과", 1),
    ("draw", 0x216c30, (836, 831, 832, 830), 2, "무승부", 1),
    ("win", 0x216c78, (827, 828), 3, "승리", 1),
    ("loss", 0x216ca8, (829, 830), 4, "패배", 1),
    ("game_set", 0x216cd8, (837, 838, 839, 840, 822, 841), 1, "게임 종료", 1),
    ("time_up_versus", 0x219798, (833, 819, 834, 820, 822, 835), 1, "시간 초과", 1),
)


def requested(sprites, edits):
    changed = {i for i in TEXTURES if i in edits and edits[i].tobytes() != sprites.image(i).tobytes()}
    require(not changed or changed == set(TEXTURES),
            "MINI game messages need all four MINI_SPR_108/109/110/111_KOR.png files")
    return bool(changed)


def source_tables(data):
    elf = Elf(data)
    tables = []
    for name, address, numbers, palette, caption, scale in CASES:
        rows = [list(struct.unpack_from("<Iii", data, elf.offset(address) + slot * 12))
                for slot in range(len(numbers))]
        require([r[0] for r in rows] == [(n << 8) | palette for n in numbers],
                f"Original MINI message table differs: {name}")
        tables.append(rows)
    return tables


def geometry(sprites, images, number, scale=1):
    fields = struct.unpack_from("<16H", sprites.files["spritetbl.bin"], number * 32)
    require(fields[0] in TEXTURES and fields[2] == fields[3]
            and fields[4] * 2 == fields[2] and fields[5] * 2 == fields[3],
            f"MINI message glyph geometry differs: {number}")
    u, v, width, height = fields[6:10]
    require((width, height) == fields[2:4], "MINI message glyph UV/display size differs")
    box = images[fields[0]].crop((u, v, u + width, v + height)).getchannel("A").getbbox()
    if box is None:
        return None
    return tuple((value - fields[4 if axis % 2 == 0 else 5]) * scale
                 for axis, value in enumerate(box))


def visible_bounds(rows, shapes):
    occupied = [(x + shapes[handle >> 8][0], y + shapes[handle >> 8][1],
                 x + shapes[handle >> 8][2], y + shapes[handle >> 8][3])
                for handle, x, y in rows if shapes[handle >> 8] is not None]
    require(occupied, "MINI message has no visible glyphs")
    return [min(r[0] for r in occupied), min(r[1] for r in occupied),
            max(r[2] for r in occupied), max(r[3] for r in occupied)]


def layout(sprites):
    from mini_sprites import originals
    import wwrom

    source = originals()
    tables = source_tables(wwrom.originals()[0])
    old_images = {i: source.image(i) for i in TEXTURES}
    images = {i: sprites.image(i) for i in TEXTURES}
    lookup = {letter: number for number, letter in GLYPHS.items() if letter}
    for number, letter in GLYPHS.items():
        shape = geometry(sprites, images, number)
        require((shape is not None) == bool(letter), f"MINI message glyph/blank slot differs: {number}")
    result = []
    for spec, original in zip(CASES, tables):
        name, address, numbers, palette, caption, scale = spec
        shapes = {n: geometry(sprites, images, n, scale) for n in GLYPHS}
        old_shapes = {n: geometry(source, old_images, n, scale) for n in numbers}
        old_box = visible_bounds(original, old_shapes)
        target = (old_box[0] + old_box[2]) / 2
        cursor, active = 0, []
        for letter in caption:
            if letter == " ":
                cursor += 11 * scale  # Together with the preceding 3px gap: 14px.
                continue
            number = lookup[letter]
            left, _, right, _ = shapes[number]
            y = original[len(active)][2]
            active.append([(number << 8) | palette, cursor - left, y])
            cursor += right - left + 3 * scale
        width = cursor - 3 * scale
        require(len(active) <= len(original), f"MINI message table capacity exceeded: {name}")
        for row in active:
            row[1] = round(row[1] + target - width / 2)
        rows = active + [[(BLANK << 8) | palette, active[-1][1], active[-1][2]]
                         for _ in range(len(original) - len(active))]
        old_textures = {struct.unpack_from("<H", source.files["spritetbl.bin"], n * 32)[0] for n in numbers}
        needed = {struct.unpack_from("<H", sprites.files["spritetbl.bin"], (h >> 8) * 32)[0] for h, _, _ in rows}
        for kind, group, textures in source.groups:
            available = set(textures)
            if old_textures <= available:
                require(needed <= available,
                        f"MINI message {name} requires unloaded textures in original group {kind}/{group}")
        box = visible_bounds(rows, shapes)
        require(abs(box[0] + box[2] - old_box[0] - old_box[2]) <= 1,
                f"MINI message horizontal centre moved: {name}")
        if name not in ("win", "loss"):
            require(0 <= box[0] < box[2] <= 640 and 160 <= box[1] < box[3] <= 300,
                    f"MINI message exceeds its native screen band: {name}")
        result.append({"name": name, "address": address, "caption": caption, "rows": rows,
                       "active_glyphs": len(active), "visible_bounds": box,
                       "source_visible_bounds": old_box, "scale": scale})
    return result


def patch_tables(data, captions):
    import wwrom

    source = wwrom.originals()[0]
    original, elf, output = source_tables(source), Elf(data), bytearray(data)
    require(len(captions) == len(CASES), "All MINI game-message tables are required")
    for spec, expected, caption in zip(CASES, original, captions):
        name, address, numbers, palette, text, scale = spec
        require(caption["name"] == name and caption["address"] == address
                and caption["caption"] == text and len(caption["rows"]) == len(numbers),
                "MINI game-message table identity or count differs")
        start = elf.offset(address)
        before = b"".join(struct.pack("<Iii", *row) for row in expected)
        require(data[start:start + len(before)] == before, f"Original MINI message table differs: {name}")
        require(all(handle >> 8 in GLYPHS and handle & 255 == palette for handle, _, _ in caption["rows"]),
                f"MINI message glyph or palette differs: {name}")
        output[start:start + len(before)] = b"".join(struct.pack("<Iii", *row) for row in caption["rows"])
    return bytes(output)


ALL_CLEAR_TEXTURES = (104, 105)
ALL_CLEAR_TABLE = 0x1c5960
ALL_CLEAR_ROWS = ((771, -132, -25, 2), (773, -16, -25, 1), (0, 320, 296, 0))


def all_clear_region(sprites, number):
    expected = {771: (104, 116, 50, 0, 0), 773: (105, 149, 50, 0, 32)}
    index, width, height, u, v = expected[number]
    fields = struct.unpack_from("<16H", sprites.files["spritetbl.bin"], number * 32)
    require(fields[0] == index and fields[2:10] == (width, height, 0, 0, u, v, width, height),
            "MINI all-clear sprite geometry differs")
    return (u, v, u + width, v + height)


def all_clear_requested(sprites, edits):
    changed = set()
    for index, number in ((104, 771), (105, 773)):
        if index in edits:
            require(edits[index].size == sprites.image(index).size, "MINI all-clear PNG dimensions differ")
            box = all_clear_region(sprites, number)
            if edits[index].convert("RGBA").crop(box).tobytes() != sprites.image(index).crop(box).tobytes():
                changed.add(index)
    require(not changed or changed == set(ALL_CLEAR_TEXTURES),
            "MINI all-clear needs both 104_KOR and the lower clear caption in 105_KOR")
    return bool(changed)


def all_clear_layout(sprites):
    from mini_sprites import originals
    source = originals()
    numbers = (771, 773)
    old, new = {}, {}
    for number, index in zip(numbers, ALL_CLEAR_TEXTURES):
        box = all_clear_region(sprites, number)
        old[number] = source.image(index).crop(box).getchannel("A").getbbox()
        new[number] = sprites.image(index).crop(box).getchannel("A").getbbox()
        require(old[number] is not None and new[number] is not None, "MINI all-clear caption is blank")
    before = [[n << 8, x, y] for n, x, y, _ in ALL_CLEAR_ROWS[:2]]
    old_box = visible_bounds(before, old)
    gap = 12
    width = sum(new[n][2] - new[n][0] for n in numbers) + gap
    cursor = (old_box[0] + old_box[2] - width) / 2
    rows = []
    for (number, _, y, _), n in zip(ALL_CLEAR_ROWS, numbers):
        rows.append([number << 8, round(cursor - new[n][0]), y])
        cursor += new[n][2] - new[n][0] + gap
    box = visible_bounds(rows, new)
    require(abs(box[0] + box[2] - old_box[0] - old_box[2]) <= 1, "MINI all-clear centre moved")
    require(-320 <= box[0] < box[2] <= 320 and -46 <= box[1] < box[3] <= 54,
            "MINI all-clear exceeds its native screen band")
    return {"caption": "올 클리어!", "rows": rows, "visible_bounds": box,
            "source_visible_bounds": old_box, "parent_origin": [320, 296], "gap_pixels": gap}


def patch_all_clear(data, caption):
    elf, output = Elf(data), bytearray(data)
    start = elf.offset(ALL_CLEAR_TABLE)
    before = b"".join(struct.pack("<Ihh6H", n << 8, x, y, 0, 0, 0, 0, 0, count)
                      for n, x, y, count in ALL_CLEAR_ROWS)
    require(data[start:start + len(before)] == before, "Original MINI all-clear table differs")
    require(len(caption["rows"]) == 2, "Both MINI all-clear words are required")
    for slot, ((handle, x, y), (number, _, old_y, _)) in enumerate(zip(caption["rows"], ALL_CLEAR_ROWS)):
        require(handle == number << 8 and y == old_y and -320 <= x <= 320,
                "MINI all-clear row identity or range differs")
        struct.pack_into("<h", output, start + slot * 20 + 4, x)
    return bytes(output)
