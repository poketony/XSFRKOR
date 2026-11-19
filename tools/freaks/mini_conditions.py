"""Korean condition fragments and bounded format tables; no rule/code changes."""

from collections import Counter
import struct

from elf_probe import Elf
from unpack_disc import require


TEXTURES = (59, 61)
WORDS = dict(zip(range(466, 479), (
    "모두", "의", "칸", "을", "채워!", "단어", "더", "만들어!", "에", "블록", "써!", "포함", "를")))
WORDS.update(zip(range(503, 510), ("개", "글자", "이상", "연쇄", "동시", "회", "연쇄!")))
BASES = (0x1cb0c8, 0x1cb758)
ORIGINAL = (
    (466, 467, 468, 469, 470), (-6, 469, 466, 470),
    (471, 469, 472, -1, 503, 478),
    (-2, 504, 467, 471, 469, 472, -1, 503, 478),
    (-2, 504, 505, 467, 471, 469, 472, -1, 503, 478),
    (-2, 506, 469, 472, -1, 508, 478),
    (-2, 504, 467, 471, 469, 507, 474, -1, 503, 478),
    (-2, 504, 505, 467, 471, 469, 507, 474, -1, 503, 478),
    (475, 469, 472, -1, 503, 476), (-3, 469, 472, -1, 503, 478),
    (-5, 477, 471, 469, 472, -1, 503, 478),
    *((478,),) * 9, (1090, 467, 1091, 469, 478),
)
ORIGINAL_SHORT = (
    ORIGINAL[0], ORIGINAL[1], (471, 469, -1, 503),
    (-2, 504, 469, -1, 503), (-2, 504, 505, 469, -1, 503),
    (-2, 506, 469, -1, 508),
    (-2, 504, 469, 507, 474, -1, 503),
    (-2, 504, 505, 469, 507, 474, -1, 503),
    (475, 469, -1, 503), (-3, 469, -1, 503),
    (-5, 477, 471, 469, -1, 503), *((478,),) * 10,
)
KOREAN = (
    (468, 469, 466, 470), (-6, 469, 466, 470),
    (471, 478, -1, 503, 472, 473),
    (-2, 504, 471, 478, -1, 503, 472, 473),
    (-2, 504, 505, 471, 478, -1, 503, 472, 473),
    (-2, 506, 478, -1, 508, 472, 473),
    (-2, 504, 471, 478, 507, 474, -1, 503, 473),
    (-2, 504, 505, 471, 478, 507, 474, -1, 503, 473),
    (475, 469, -1, 503, 472, 476), (-3, 471, 478, -1, 503, 472, 473),
    (-5, 477, 471, 478, -1, 503, 472, 473),
    *((473,),) * 9, (1090, 467, 1091, 469, 473),
)
KOREAN_SHORT = (
    KOREAN[0], KOREAN[1], (471, -1, 503),
    (-2, 504, 471, -1, 503), (-2, 504, 505, 471, -1, 503),
    (-2, 506, -1, 508),
    (-2, 504, 471, 507, 474, -1, 503),
    (-2, 504, 505, 471, 507, 474, -1, 503),
    (475, -1, 503), (-3, -1, 503), (-5, 477, 471, -1, 503), *((473,),) * 10,
)
FIXED_BOXES = (
    (0x216990, (468 << 8, (468 << 8) | 1, 165, 308, 56, 32)),
    (0x2169a8, ((751 << 8) | 3, (751 << 8) | 4, 223, 304, 32, 40)),
    (0x2169c0, (466 << 8, (466 << 8) | 1, 255, 308, 80, 32)),
    (0x2169d8, ((752 << 8) | 3, (752 << 8) | 4, 335, 304, 142, 40)),
    (0x21aef8, (468 << 8, (468 << 8) | 1, 165, 256, 56, 32)),
    (0x21af10, ((751 << 8) | 3, (751 << 8) | 4, 223, 252, 32, 40)),
    (0x21af28, (466 << 8, (466 << 8) | 1, 255, 256, 80, 32)),
    (0x21af40, ((752 << 8) | 3, (752 << 8) | 4, 335, 252, 142, 40)),
)


def packed(tokens):
    require(len(tokens) < 20, "MINI condition format exceeds its original capacity")
    return struct.pack('<20i', *[n << 8 if n >= 0 else n for n in (*tokens, -4)],
                       *([0] * (19 - len(tokens))))


def requested(sprites, edits):
    chosen = {i for i in TEXTURES if i in edits and edits[i].convert('RGBA').tobytes() != sprites.image(i).tobytes()}
    require(not chosen or chosen == set(TEXTURES), "MINI conditions need both 059_KOR and 061_KOR")
    return bool(chosen)


def trim_table(source, rebuilt, table):
    result, fragments = bytearray(table), []
    for number in WORDS:
        at = number * 32
        require(table[at:at + 32] == source.files['spritetbl.bin'][at:at + 32], "MINI condition source geometry differs")
        f = struct.unpack_from('<16H', table, at)
        u, v, w, h = f[6:10]
        require(f[0] in TEXTURES and f[2:6] == (w, h, 0, 0), "MINI condition has an unexpected pivot/scale")
        alpha = rebuilt.image(f[0]).crop((u, v, u + w, v + h)).getchannel('A')
        bounds = alpha.getbbox()
        require(bounds is not None, f"MINI condition fragment {number} is empty")
        # Tight particles join the preceding noun; word endings keep extra space.
        right_pad = 6 if number in (466, 469, 472, 474, 477, 478, 503, 504, 505, 508) else 1
        left, right = max(0, bounds[0] - 1), min(w, bounds[2] + right_pad)
        width = right - left
        struct.pack_into('<H', result, at + 4, width)
        struct.pack_into('<H', result, at + 12, u + left)
        struct.pack_into('<H', result, at + 16, width)
        fragments.append({'sprite': number, 'text': WORDS[number], 'old_width': w, 'width': width,
                          'u': u + left, 'left_crop': left, 'right_crop': w - right})
    fixed, groups = [], []
    widths = {r['sprite']: r['width'] for r in fragments}
    for start in (0, 4):
        rows = FIXED_BOXES[start:start + 4]
        old_edges, new_edges, cursor, placed = [], [], 0, []
        for slot, (address, original) in enumerate(rows):
            normal, alternate, x, y, width, height = original
            old_left, old_right = ink_edges(source, source.files['spritetbl.bin'], normal >> 8, width)
            old_edges.append((x + old_left, x + old_right))
            new_width = widths.get(normal >> 8, width)
            left, right = ink_edges(rebuilt, result, normal >> 8, new_width)
            placed.append((address, original, new_width, cursor - left))
            cursor += right - left + (2 if slot == 0 else 10 if slot < 3 else 0)
        old_left, old_right = min(p[0] for p in old_edges), max(p[1] for p in old_edges)
        origin = (old_left + old_right - cursor) / 2
        for address, original, width, offset in placed:
            normal, alternate, x, y, _, height = original
            new_x = round(origin + offset)
            left, right = ink_edges(rebuilt, result, normal >> 8, width)
            new_edges.append((new_x + left, new_x + right))
            fixed.append({'address': address, 'before': list(original),
                          'after': [normal, alternate, new_x, y, width, height]})
        groups.append({'address': rows[0][0], 'before_bounds': [old_left, old_right],
                       'after_bounds': [min(p[0] for p in new_edges), max(p[1] for p in new_edges)]})
    return bytes(result), {'fragments': fragments, 'fixed_boxes': fixed, 'fixed_groups': groups}


def ink_edges(sprites, table, number, width):
    f = struct.unpack_from('<16H', table, number * 32)
    require(f[4:6] == (0, 0), 'MINI shared condition has an unexpected pivot')
    u, v, w, h = f[6:10]
    bounds = sprites.image(f[0]).crop((u, v, u + w, v + h)).getchannel('A').getbbox()
    require(bounds is not None, 'MINI shared condition label is empty')
    return bounds[0] * width / w, bounds[2] * width / w


def patch_tables(data, report):
    elf, output = Elf(data), bytearray(data)
    for base, old, new in zip(BASES, (ORIGINAL, ORIGINAL_SHORT), (KOREAN, KOREAN_SHORT)):
        require(len(old) == len(new) == 21, "MINI condition type count differs")
        for kind, (before, after) in enumerate(zip(old, new)):
            require(Counter(n for n in before if n < 0) == Counter(n for n in after if n < 0),
                    "MINI condition dynamic fields changed")
            require(all(n < 0 or n in WORDS or n in (1090, 1091) for n in after),
                    "MINI condition introduced a new texture dependency")
            start = elf.offset(base + kind * 80)
            require(data[start:start + 80] == packed(before), "Original MINI condition format differs")
            output[start:start + 80] = packed(after)
    require(len(report['fixed_boxes']) == len(FIXED_BOXES), "MINI condition shared boxes are missing")
    for row, (address, original) in zip(report['fixed_boxes'], FIXED_BOXES):
        start = elf.offset(address)
        require(row['address'] == address and row['before'] == list(original)
                and data[start:start + 24] == struct.pack('<6I', *original), "MINI shared condition box differs")
        new = row['after']
        require(new[:2] == list(original[:2]) and (new[3], new[5]) == (original[3], original[5])
                and 0 < new[4] <= original[4] and 0 <= new[2] < new[2] + new[4] <= 640,
                "MINI shared condition bounds/height changed")
        output[start:start + 24] = struct.pack('<6I', *new)
    return bytes(output)
