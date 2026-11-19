"""Diagnostic titles and the shared Korean possessive caption slice."""

import struct

from elf_probe import Elf
from mini_headings import visible_bounds
from unpack_disc import require

TEXTURE = 143
GLYPHS = dict(zip((1060, 1061, 1062, 1063, 1064, 1065, 1066, 1076, 1077, 1078),
                  ("성", "격", "궁", "", "진", "합", "단", "", "", "")))
OLD_SUFFIX = (56, 128, 80, 24)
NEW_SUFFIX = (36, 128, 100, 24)
BODY = (1071, 1072, 1075, 1079)
NEUTRAL = (0, 1, 2, 3, 5, 7, 8, 11, 12, 14)
SKIP_PALETTE = (*NEUTRAL, 9, 13, 15)
# name, table, original glyphs, Korean spelling, new sequence
CASES = (
    ("personality", 0x1c4f28, (1065, 1066, 1065, 1064, 1063, 1062, 1061, 1060),
     "성격 진단", (1060, 1061, None, 1064, 1066)),
    ("compatibility", 0x1c4fe0, (1065, 1066, 1065, 1064, 1078, 1077, 1064, 1061, 1076),
     "궁합 진단", (1062, 1065, None, 1064, 1066)),
)
PLAYERS = (("player1", 0x1c50f8, 1073 << 8), ("player2", 0x1c5160, (1074 << 8) | 1))


def requested(sprites, edits):
    return TEXTURE in edits and edits[TEXTURE].convert("RGBA").tobytes() != sprites.image(TEXTURE).tobytes()


def prepare_table(sprites, table):
    at = 1075 * 32
    require(table[at:at + 32] == sprites.files["spritetbl.bin"][at:at + 32], "Diagnostic suffix descriptor preimage differs")
    f = list(struct.unpack_from("<16H", table, at))
    require(f[0] == TEXTURE and tuple(f[2:10]) == (80, 24, 0, 0, *OLD_SUFFIX), "Original diagnostic suffix geometry differs")
    f[2], f[6], f[8] = NEW_SUFFIX[2], NEW_SUFFIX[0], NEW_SUFFIX[2]
    out = bytearray(table)
    struct.pack_into("<16H", out, at, *f)
    return bytes(out)


def geometry(sprites, n):
    f = struct.unpack_from("<16H", sprites.files["spritetbl.bin"], n * 32)
    require(f[0] == TEXTURE and f[2:4] == f[8:10], "Diagnostic glyph geometry differs")
    u, v, w, h = f[6:10]
    box = sprites.image(TEXTURE, 1 if n == 1074 else 0).crop((u, v, u + w, v + h)).getchannel("A").getbbox()
    return None if box is None else tuple(value - f[4 + k % 2] for k, value in enumerate(box))


def source_tables(data):
    elf, result = Elf(data), {}
    for name, address, numbers, _, _ in CASES:
        at = elf.offset(address)
        rows = [list(struct.unpack_from("<Ihh6H", data, at + k * 20)) for k in range(len(numbers) + 1)]
        require([r[0] for r in rows[:-1]] == [n << 8 for n in numbers]
                and [r[-1] for r in rows[:-1]] == list(range(len(numbers), 0, -1))
                and rows[-1] == [0, 320, 46, 0, 0, 0, 0, 0, 0], "Original diagnostic title tree differs")
        result[name] = rows
    for name, address, tag in PLAYERS:
        at = elf.offset(address)
        rows = [list(struct.unpack_from("<Ihh6H", data, at + k * 20)) for k in range(3)]
        require([r[0] for r in rows[:2]] == [1075 << 8, tag] and [r[-1] for r in rows[:2]] == [2, 1]
                and not any(rows[2]), "Original diagnostic player caption tree differs")
        result[name] = rows
    return result


def layout(sprites):
    from mini_sprites import originals
    import wwrom
    source = originals()
    old = source_tables(wwrom.originals()[0])
    for n in GLYPHS:
        at = n * 32
        require(sprites.files["spritetbl.bin"][at:at + 32] == source.files["spritetbl.bin"][at:at + 32],
                "Diagnostic title descriptor changed")
    f = struct.unpack_from("<16H", sprites.files["spritetbl.bin"], 1075 * 32)
    require(f[2:10] == (100, 24, 0, 0, *NEW_SUFFIX), "Translated diagnostic suffix geometry differs")
    shapes = {n: geometry(sprites, n) for n in GLYPHS}
    require(all(bool(GLYPHS[n]) == (shapes[n] is not None) for n in GLYPHS), "Diagnostic title letter/blank differs")
    titles = []
    for name, address, numbers, text, sequence in CASES:
        require("".join(" " if n is None else GLYPHS[n] for n in sequence) == text, "Diagnostic title spelling differs")
        rows = [[1063 << 8, r[1], r[2]] for r in old[name][:-1]]
        before = visible_bounds([r[:3] for r in old[name][:-1]], {n: geometry(source, n) for n in numbers})
        cursor, active = 0, []
        for n in sequence:
            if n is None:
                cursor += 14
            else:
                left, _, right, _ = shapes[n]
                active.append((n, cursor - left))
                cursor += right - left + 3
        origin = (before[0] + before[2] - cursor + 3) / 2
        ordered = sorted(range(len(numbers)), key=lambda k: old[name][k][1])
        slots = [ordered[round(k * (len(ordered) - 1) / (len(active) - 1))] for k in range(len(active))]
        for (n, x), slot in zip(active, slots):
            rows[slot] = [n << 8, round(origin + x), old[name][slot][2]]
        after = visible_bounds(rows, shapes)
        require(abs(before[0] + before[2] - after[0] - after[2]) <= 1
                and -320 <= after[0] < after[2] <= 320, "Diagnostic title centre/bounds differ")
        titles.append(dict(name=name, address=address, caption=text, rows=rows, slots=slots,
                           source_visible_bounds=before, visible_bounds=after))
    players = []
    suffix = geometry(sprites, 1075)
    require(suffix is not None, "Diagnostic possessive caption is empty")
    for name, address, tag in PLAYERS:
        row, label = old[name][:2]
        tag_shape = geometry(source, tag >> 8)
        x = label[1] + tag_shape[2] + 2 - suffix[0]
        require(0 <= x and x + 100 <= 304, "Diagnostic player caption exceeds its panel")
        players.append(dict(name=name, address=address, tag=tag, x=x, y=row[2],
                            old_x=row[1], gap_pixels=2, caption=("1P" if name == "player1" else "2P") + "의 성격은"))
    result = dict(titles=titles, player_captions=players, old_suffix=list(OLD_SUFFIX), new_suffix=list(NEW_SUFFIX))
    patch_tables(wwrom.originals()[0], result)
    return result


def patch_tables(data, captions):
    import wwrom
    source = wwrom.originals()[0]
    elf, original, out = Elf(data), Elf(source), bytearray(data)
    tables = source_tables(source)
    require(captions["old_suffix"] == list(OLD_SUFFIX) and captions["new_suffix"] == list(NEW_SUFFIX), "Diagnostic caption split differs")
    require(len(captions["titles"]) == 2 and {r["name"] for r in captions["titles"]} == {c[0] for c in CASES},
            "Diagnostic title table set differs")
    for name, address, numbers, text, sequence in CASES:
        caption = next(r for r in captions["titles"] if r["name"] == name)
        rows = caption["rows"]
        at, before = elf.offset(address), original.offset(address)
        size = (len(numbers) + 1) * 20
        require(data[at:at + size] == source[before:before + size] and caption["address"] == address
                and caption["caption"] == text and len(rows) == len(numbers), "Diagnostic title identity/preimage differs")
        require([h >> 8 for h, _, _ in sorted(rows, key=lambda r: r[1]) if h >> 8 != 1063]
                == [n for n in sequence if n is not None], "Diagnostic title order differs")
        for k, (h, x, y) in enumerate(rows):
            require(h & 255 == 0 and h >> 8 in GLYPHS and y == tables[name][k][2] and -320 <= x <= 320,
                    "Diagnostic title row position/identity differs")
            struct.pack_into("<Ih", out, at + k * 20, h, x)
    require(len(captions["player_captions"]) == 2
            and {r["name"] for r in captions["player_captions"]} == {c[0] for c in PLAYERS}, "Diagnostic player table set differs")
    for name, address, tag in PLAYERS:
        row = next(r for r in captions["player_captions"] if r["name"] == name)
        at, before = elf.offset(address), original.offset(address)
        require(data[at:at + 60] == source[before:before + 60] and row["address"] == address and row["tag"] == tag
                and row["y"] == tables[name][0][2] and row["old_x"] == tables[name][0][1]
                and row["gap_pixels"] == 2 and 0 <= row["x"] <= 204, "Diagnostic player caption preimage/position differs")
        struct.pack_into("<h", out, at + 4, row["x"])
    return bytes(out)
