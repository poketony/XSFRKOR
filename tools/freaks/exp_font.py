"""EXP's four 2-bit font planes; retain source text and reuse only unused kanji."""

from functools import lru_cache
import json
from pathlib import Path
import struct
import zlib

from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont

import exp_sb
import xs2_hangul

from build_paths import DATA, REPO
from unpack_disc import require


BLOCK32 = (0, 1, 4, 5, 16, 17, 20, 21, 2, 3, 6, 7, 18, 19, 22, 23,
           8, 9, 12, 13, 24, 25, 28, 29, 10, 11, 14, 15, 26, 27, 30, 31)
WORD32 = (0, 1, 4, 5, 8, 9, 12, 13, 2, 3, 6, 7, 10, 11, 14, 15)
BLOCK4 = (0, 2, 8, 10, 1, 3, 9, 11, 4, 6, 12, 14, 5, 7, 13, 15,
          16, 18, 24, 26, 17, 19, 25, 27, 20, 22, 28, 30, 21, 23, 29, 31)
HEADER = bytes.fromhex("000000000000000000000000013c005100bc0000000000080000000000000000")
BLOCK_BYTES, FILE_BYTES, GLYPHS = 0x3c000, 0x78240, 4096
KANJI_START, KANJI_END = 1024, 3989
CELL_WIDTH, CELL_HEIGHT = 20, 24
DEFAULT_FONT = DATA / "exp/exp-xs2-hangul.zlib"
DEFAULT_FONT_HASH = "fb876ffc9d8b03e526395302b993814edb069191f0fcb3e933089f58bc99cde3"


def kanji_index(code):
    require(len(code) == 2 and 0xb0 <= code[0] <= 0xcf and 0xa1 <= code[1] <= 0xfe,
            "Not an EXP level-one kanji code")
    index = 94 * code[0] + code[1] - 0x3d41
    require(KANJI_START <= index < KANJI_END, "EXP code is beyond level-one kanji")
    return index


def kanji_code(index):
    require(KANJI_START <= index < KANJI_END, "EXP index is not reusable kanji")
    row, column = divmod(index - KANJI_START, 94)
    return bytes((0xb0 + row, 0xa1 + column))


def protected_indices(texts):
    # Also reserve odd-alignment matches; never infer string boundaries here.
    return {94 * a + b - 0x3d41 for data in texts for a, b in zip(data, data[1:])
            if 0xb0 <= a <= 0xcf and 0xa1 <= b <= 0xfe and 94 * a + b - 0x3d41 < KANJI_END}


def mapping_for(characters, protected):
    characters = sorted(set(characters))
    require(all(len(c) == 1 and "\uac00" <= c <= "\ud7a3" for c in characters),
            "EXP additive font currently accepts complete Hangul syllables only")
    require(set(protected) <= set(range(KANJI_START, KANJI_END)), "EXP protected font indices are invalid")
    available = [index for index in range(KANJI_END - 1, KANJI_START - 1, -1) if index not in protected]
    require(len(characters) <= len(available),
            f"EXP font needs {len(characters)} Hangul cells but only {len(available)} are free; source glyphs preserved")
    return {char: kanji_code(index) for char, index in zip(characters, available)}


@lru_cache(maxsize=1)
def upload_words():
    inverse = [-1] * (BLOCK_BYTES // 4)
    for y in range(192):
        for x in range(320):
            page = (y >> 5) * 5 + (x >> 6)
            block = BLOCK32[((x & 63) >> 3) + ((y & 31) >> 3) * 8]
            word = WORD32[(x & 7) + (y & 1) * 8]
            at = page * 2048 + block * 64 + ((y & 7) >> 1) * 16 + word
            require(inverse[at] == -1, "EXP GS upload has overlapping words")
            inverse[at] = y * 320 + x
    require(-1 not in inverse, "EXP GS upload has a hole")
    return tuple(inverse)


def pixel_location(index, dx, dy):
    require(0 <= index < GLYPHS and 0 <= dx < CELL_WIDTH and 0 <= dy < CELL_HEIGHT,
            "EXP font pixel is outside a glyph")
    sheet, local = divmod(index, 1024)
    row, column = divmod(local, 32)
    x, y = column * CELL_WIDTH + dx, row * CELL_HEIGHT + dy
    dbp, y = (y // 128) * 160, y % 128
    bx, by = x & 31, y & 15
    col, cy = by >> 2, by & 3
    word = (bx & 1) + ((bx & 6) << 1) + ((cy & 1) << 1)
    word ^= ((cy >> 1) ^ (col & 1)) * 8
    byte = (bx >> 3) * 2 + (cy >> 1)
    block = BLOCK4[((x & 127) >> 5) + ((y & 127) >> 4) * 4]
    gs = dbp * 64 + (x >> 7) * 2048 + block * 64 + col * 16 + word
    offset = (sheet // 2) * (BLOCK_BYTES + 32) + 32 + upload_words()[gs] * 4 + (byte >> 1)
    shift = (byte & 1) * 4 + (sheet & 1) * 2
    return offset, shift


class Font:
    def __init__(self, data):
        require(len(data) == FILE_BYTES and data[:32] == HEADER
                and data[BLOCK_BYTES + 32:BLOCK_BYTES + 64] == HEADER, "EXP font layout differs")
        self.data = data

    def glyph(self, index):
        result = bytearray()
        for y in range(CELL_HEIGHT):
            for x in range(CELL_WIDTH):
                offset, shift = pixel_location(index, x, y)
                result.append(self.data[offset] >> shift & 3)
        return bytes(result)

    def replace(self, glyphs):
        output = bytearray(self.data)
        for index, pixels in glyphs.items():
            require(KANJI_START <= index < KANJI_END and len(pixels) == CELL_WIDTH * CELL_HEIGHT
                    and all(value < 4 for value in pixels), "Invalid EXP replacement glyph")
            for n, value in enumerate(pixels):
                offset, shift = pixel_location(index, n % CELL_WIDTH, n // CELL_WIDTH)
                output[offset] = output[offset] & ~(3 << shift) | value << shift
        return bytes(output)

    def region(self, sheet, x, y, width, height):
        require(0 <= sheet < 4 and 0 <= x <= 640 - width and 0 <= y <= 768 - height
                and width > 0 and height > 0, "EXP atlas region is outside its plane")
        pixels = bytearray()
        for dy in range(height):
            for dx in range(width):
                px, py = x + dx, y + dy
                index = sheet * 1024 + py // 24 * 32 + px // 20
                offset, shift = pixel_location(index, px % 20, py % 24)
                pixels.append((self.data[offset] >> shift & 3) * 85)
        return Image.frombytes("L", (width, height), bytes(pixels))


def original_fonts():
    report = json.loads((DATA / "contents-report.json").read_text(encoding="utf-8"))
    result = []
    for name in ("font0.tex", "font1.tex"):
        row = next(row for row in report["members"] if row["source"] == "EXP/XENOSAGA.00~02" and row["path"] == name)
        data = (REPO / row["output"]["path"]).read_bytes()
        require(exp_sb.checksum(data) == row["output"]["sha256"], f"Original EXP font differs: {name}")
        Font(data)
        result.append(data)
    return tuple(result)


def non_sb_text_sources(exclude=()):
    """Keep all currently unedited text resources, including their non-text bytes."""
    report = json.loads((DATA / "contents-report.json").read_text(encoding="utf-8"))
    protected, known = [], set()
    for row in report["members"]:
        if row["source"] != "EXP/XENOSAGA.00~02":
            continue
        name = row["path"]
        if not name.endswith((".txt", ".txd")) and name != "yamamoto/batdat.bin":
            continue
        data = (REPO / row["output"]["path"]).read_bytes()
        require(exp_sb.checksum(data) == row["output"]["sha256"], f"Original EXP text source differs: {name}")
        known.add(name)
        if name.endswith(".txt") and not name.startswith("movie/"):
            text = data.decode("cp932")
            # Only kanji cells are reusable; unsupported punctuation stays reserved.
            data = b"".join(char.encode("euc_jp", errors="ignore") for char in text)
        if name not in exclude:
            protected.append(data)
    require(set(exclude) <= known, "Unknown EXP text source exclusion")
    inventory = json.loads((DATA / "disc-inventory.json").read_text(encoding="utf-8"))
    for name in ("EXP.ELF", "OV01.OVL", "OV02.OVL"):
        row = next(row for row in inventory["files"] if row["path"] == "EXP/" + name)
        data = (REPO / "unpacked/EXP" / name).read_bytes()
        require(exp_sb.checksum(data) == row["sha256"], f"Original EXP executable differs: {name}")
        if name.endswith(".OVL"):
            protected.append(data)
        else:
            header = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
            for index in range(header[12]):
                section = struct.unpack_from("<10I", data, header[6] + index * header[11])
                if section[2] & 2 and not section[2] & 4 and section[1] != 8:
                    protected.append(data[section[4]:section[4] + section[5]])
    return protected


@lru_cache(maxsize=1)
def ported_glyphs():
    data = DEFAULT_FONT.read_bytes()
    require(exp_sb.checksum(data) == DEFAULT_FONT_HASH, "Ported XS2 Hangul snapshot changed")
    raw = zlib.decompress(data)
    require(len(raw) == 2350 * 480 and max(raw) <= 3, "Ported XS2 Hangul pixel layout differs")
    return {char: raw[index * 480:(index + 1) * 480] for index, (char, _) in enumerate(xs2_hangul.rows())}


def render_glyphs(characters, font_path):
    if font_path is not None and Path(font_path) == DEFAULT_FONT:
        glyphs = ported_glyphs()
        require(set(characters) <= glyphs.keys(), "XS2 font snapshot supports the approved 2350 Hangul syllables")
        return {char: glyphs[char] for char in sorted(characters)}
    require(font_path is not None and Path(font_path).is_file(), "EXP Hangul needs a Korean TTF/OTF")
    with TTFont(font_path, fontNumber=0) as face:
        cmap = face.getBestCmap()
        require(all(ord(char) in cmap for char in characters), "Chosen font lacks an EXP Hangul glyph")
    font = ImageFont.truetype(str(font_path), 18)
    glyphs = {}
    for char in sorted(characters):
        # Padding exposes overflow; never resize an individual glyph to make it fit.
        image = Image.new("L", (60, 64))
        ImageDraw.Draw(image).text((21, 39), char, font=font, fill=255, anchor="ls")
        bounds = image.getbbox()
        require(bounds is not None and bounds[0] >= 20 and bounds[1] >= 20
                and bounds[2] <= 39 and bounds[3] <= 44, f"EXP glyph exceeds the 19x24 visible cell: {char}")
        pixels = bytes((value * 3 + 127) // 255 for value in image.crop((20, 20, 40, 44)).tobytes())
        require(any(pixels) and not any(pixels[19::20]), f"EXP glyph has empty or clipped ink: {char}")
        glyphs[char] = pixels
    return glyphs


def prepare_font(characters, protected, font_path):
    mapping = mapping_for(characters, protected)
    originals = original_fonts()
    if not mapping:
        return {}, (), None
    glyphs = render_glyphs(mapping, font_path)
    by_index = {kanji_index(mapping[char]): pixels for char, pixels in glyphs.items()}
    outputs = tuple(Font(data).replace(by_index) for data in originals)
    report = {"characters": len(mapping), "retained_kanji_cells": len(protected),
              "free_kanji_cells": KANJI_END - KANJI_START - len(protected),
              "mapping": {char: value.hex().upper() for char, value in mapping.items()},
              "font_sha256": exp_sb.checksum(Path(font_path).read_bytes()),
              "font_size": 20 if Path(font_path) == DEFAULT_FONT else 18,
              "font_source": "XS2 approved Pretendard SemiBold pixels" if Path(font_path) == DEFAULT_FONT else "TTF/OTF",
              "baseline": 19, "cell": [20, 24], "visible_width": 19,
              "new_font_sizes": [len(data) for data in outputs], "executable_modified": False}
    return mapping, outputs, report
