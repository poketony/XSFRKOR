"""MINI's 16x16 paired two-bit font planes and compact EUC-JP indices."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont

from unpack_contents import digest

from build_paths import REPO
from unpack_disc import require


FONT_HASH = "c300e72d6fc3140b23c3ae3aa1d360b47c665f05d7b308616e0a1d7d6c8e4456"
COUNT = 7520
FONT_SIZE = 491520
LOADED_SIZE = COUNT * 64
DEFAULT_FONT = Path.home() / "AppData/Local/Microsoft/Windows/Fonts/a\uace0\ub51514.ttf"


def glyph_index(code):
    if len(code) != 2:
        return -1
    lead, trail = code
    if not 0xa1 <= trail <= 0xfe:
        return -1
    if 0xa1 <= lead <= 0xa6:
        row = lead - 0xa1
    elif 0xb0 <= lead <= 0xf3:
        row = lead - 0xaa
    elif 0xf9 <= lead <= 0xfe:
        row = lead - 0xaf
    else:
        return -1
    return row * 94 + trail - 0xa1


def original_font():
    data = (REPO / "decompressed/MINI/WWROM.DAT/font/zenkaku.fnt").read_bytes()
    require(digest(data) == FONT_HASH, "Original MINI font changed")
    return data


def pixel_location(index, x, y):
    require(0 <= index < COUNT and 0 <= x < 16 and 0 <= y < 16, "MINI glyph coordinate outside font")
    page, local = divmod(index, 512)
    row, column = divmod(local // 2, 16)
    offset = page * 32768 + (row * 16 + y) * 128 + column * 8 + x // 2
    shift = (x & 1) * 4 + (index & 1) * 2
    require(offset < LOADED_SIZE, "MINI glyph references unloaded padding")
    return offset, shift


class MiniFont:
    def __init__(self, data):
        require(len(data) == FONT_SIZE, "MINI full-width font size differs")
        self.data = data

    def glyph(self, index):
        output = bytearray()
        for y in range(16):
            for x in range(16):
                offset, shift = pixel_location(index, x, y)
                output.append(self.data[offset] >> shift & 3)
        return bytes(output)

    def available_codes(self):
        result = []
        for lead in (*range(0xf9, 0xff), *range(0xa1, 0xa7)):
            for trail in range(0xa1, 0xff):
                code = bytes((lead, trail))
                try:
                    code.decode("euc_jp")
                except UnicodeDecodeError:
                    if not any(self.glyph(glyph_index(code))):
                        result.append(code)
        return result


def make_mapping(font, characters):
    chars = sorted(set(characters))
    require(all(len(c) == 1 and (0x1100 <= ord(c) <= 0x11ff or 0x3130 <= ord(c) <= 0x318f
                               or 0xac00 <= ord(c) <= 0xd7a3) for c in chars),
            "Only Hangul syllables/Jamo may use MINI font slots")
    codes = font.available_codes()
    require(len(chars) <= len(codes), f"MINI font code budget exceeded: {len(chars)} > {len(codes)}")
    return dict(zip(chars, codes))


def add_glyphs(data, mapping, glyphs):
    font = MiniFont(data)
    require(set(mapping) == set(glyphs), "MINI glyphs and mapping differ")
    if not mapping:
        return data
    require(len(set(mapping.values())) == len(mapping)
            and set(mapping.values()) <= set(font.available_codes()), "MINI mapping overwrites an existing glyph")
    output = bytearray(data)
    changed = set()
    for char, code in mapping.items():
        pixels = glyphs[char]
        require(len(pixels) == 256 and all(v <= 3 for v in pixels), "Expected 16x16 two-bit MINI glyph")
        index = glyph_index(code)
        changed.add(index)
        for y in range(16):
            for x in range(16):
                offset, shift = pixel_location(index, x, y)
                output[offset] = output[offset] & ~(3 << shift) | pixels[y * 16 + x] << shift
    output = bytes(output)
    rebuilt = MiniFont(output)
    for index in range(COUNT):
        if index not in changed:
            require(rebuilt.glyph(index) == font.glyph(index), "Unedited MINI glyph plane changed")
    require(output[LOADED_SIZE:] == data[LOADED_SIZE:], "MINI unloaded padding changed")
    return output


def replace_fixed_hangul(data, mapping, glyphs):
    require(set(mapping) == set(glyphs) and len(mapping) == 2350, "Expected all fixed Hangul cells")
    require(set(mapping.values()) == {bytes((a, b)) for a in range(0xb0, 0xc9) for b in range(0xa1, 0xff)},
            "MINI replacement escapes approved kanji rows")
    font = MiniFont(data)
    output = bytearray(data)
    changed = set()
    for char, code in mapping.items():
        index = glyph_index(code)
        changed.add(index)
        pixels = glyphs[char]
        require(len(pixels) == 256 and all(v <= 3 for v in pixels), "Invalid MINI Hangul pixels")
        for y in range(16):
            for x in range(16):
                offset, shift = pixel_location(index, x, y)
                output[offset] = output[offset] & ~(3 << shift) | pixels[y * 16 + x] << shift
    rebuilt = MiniFont(bytes(output))
    for index in range(COUNT):
        if index not in changed:
            require(rebuilt.glyph(index) == font.glyph(index), "Unapproved MINI glyph changed")
    require(output[LOADED_SIZE:] == data[LOADED_SIZE:], "MINI padding changed")
    return bytes(output)


def render_glyphs(characters, font_path):
    chars = sorted(set(characters))
    if not chars:
        return {}
    require(font_path is not None and font_path.is_file(), "MINI Hangul needs a Korean TTF/OTF")
    with TTFont(font_path, fontNumber=0) as source:
        cmap = source.getBestCmap()
        require(all(ord(c) in cmap for c in chars), "Chosen font lacks a MINI Hangul glyph")
    face = ImageFont.truetype(str(font_path), 14)
    boxes = [face.getbbox(c, anchor="ls") for c in chars]
    origin_x = 1 - min(b[0] for b in boxes)
    baseline = 1 - min(b[1] for b in boxes)
    require(max(b[3] for b in boxes) + baseline <= 15, "Chosen MINI font exceeds 14px inner height")
    result = {}
    for char, box in zip(chars, boxes):
        require(1 <= box[0] + origin_x and box[2] + origin_x <= 15,
                "Chosen MINI font exceeds 14px inner width")
        tile = Image.new("L", (16, 16))
        ImageDraw.Draw(tile).text((origin_x, baseline), char, font=face, fill=255, anchor="ls")
        pixels = bytes(round(v * 3 / 255) for v in tile.tobytes())
        require(any(pixels), "Chosen font produces a blank MINI glyph")
        result[char] = pixels
    return result
