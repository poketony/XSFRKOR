"""Build the approved fixed Hangul GOTHIC cells; never touch the original."""

import argparse
import json
from pathlib import Path
import struct

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from fontTools.ttLib import TTFont

from adv_format import require
from adv_format import tokens
from gothic import ASCII_SPACE_ADVANCE, Gothic, STORED_LIMIT, metric_records, pack_pixels
from lzss import compress, decompress
from unpack_contents import digest

from build_paths import REPO, WORK
import xs2_hangul


FONT_HASH = "4932f1f7459ba6ca408eda389c64020db498a1d1f344da0ecba13ce6f55b7ed5"
DEFAULT_FONT = Path.home() / "AppData/Local/Microsoft/Windows/Fonts/a\uace0\ub51516.ttf"


def original_font():
    data = (REPO / "decompressed/ADV/DAT00/GOTHIC.DAT/0000.bin").read_bytes()
    require(digest(data) == FONT_HASH, "Original GOTHIC decoded hash differs")
    return data


def translation_characters():
    from adv_text_tool import ORIGINALS, load_original, parse_editor
    texts = []
    for name in ORIGINALS:
        data = load_original(name)
        editor = WORK / "text" / (Path(name).stem + ".txt")
        translations = parse_editor(editor.read_text(encoding="utf-8"), data, name)
        texts.extend(translations.values())
    from database_text import EDITOR, original_data, parse_editor as parse_database
    if EDITOR.exists():
        texts.extend(parse_database(EDITOR.read_text(encoding="utf-8"), original_data()).values())
    import system_text
    for component in system_text.COMPONENTS:
        editor = system_text.editor_path(component)
        if editor.exists():
            source = system_text.original_data(component)
            texts.extend(system_text.parse_editor(editor.read_text(encoding="utf-8"), source, component).values())
    import ui_labels
    for component in system_text.COMPONENTS:
        editor = ui_labels.editor_path(component)
        if editor.exists():
            source = system_text.original_data(component)
            texts.extend(ui_labels.parse_editor(editor.read_text(encoding="utf-8"), source, component).values())
    return missing_characters(texts)


def missing_characters(texts):
    characters = set()
    for text in texts:
        for char in text:
            try:
                char.encode("cp932")
            except UnicodeEncodeError:
                characters.add(char)
    return characters


def render_glyphs(characters, font_path, *, size=18):
    font = ImageFont.truetype(str(font_path), size)
    characters = sorted(characters)
    if not characters:
        return {}
    with TTFont(font_path, fontNumber=0) as source_font:
        cmap = source_font.getBestCmap()
        require(all(ord(char) in cmap for char in characters), "Chosen font lacks a required Hangul glyph")
    boxes = [font.getbbox(char, anchor="ls") for char in characters]
    origin_x = 1 - min(box[0] for box in boxes)
    baseline = 1 - min(box[1] for box in boxes)
    require(max(box[3] for box in boxes) + baseline <= 19,
            "Chosen font exceeds the 18px inner height; select a compatible font")
    result = {}
    for char, box in zip(characters, boxes):
        require(1 <= box[0] + origin_x and box[2] + origin_x <= 19,
                "Chosen font exceeds 18px inner width")
        mask = Image.new("L", (20, 20))
        ImageDraw.Draw(mask).text((origin_x, baseline), char, font=font, fill=255, anchor="ls")
        require(mask.getbbox() is not None, f"Font has no visible glyph for {char!r}")
        outline = mask.filter(ImageFilter.MaxFilter(3))
        pixels = []
        for foreground, edge in zip(mask.tobytes(), outline.tobytes()):
            if foreground:
                pixels.append(6 + round(foreground * 9 / 255))
            elif edge:
                pixels.append(max(1, round(edge * 5 / 255)))
            else:
                pixels.append(0)
        result[char] = bytes(pixels)
    return result


def font_container(data):
    stored = compress(data)
    require(len(stored) + 12 <= STORED_LIMIT,
            f"Font compressed stack buffer exceeded: {len(stored) + 12} > {STORED_LIMIT}")
    require(decompress(stored) == data, "Font compression roundtrip differs")
    return struct.pack("<3I", 1, 12, len(stored)) + stored


def adjust_shared_typography(data):
    font = Gothic(data)
    output = bytearray(data)
    space_code = b"\xf0\x40"
    require(space_code not in font.display_codes, "ASCII-space metric code is already in use")
    require(font.codes(0)[0x20] == b"\x81\x40", "Original ASCII-space mapping differs")
    require(not metric_records(data, font.faces[0].metrics_offset), "Fixed half-width metrics differ")

    # A separate metric key keeps ASCII space distinct from ideographic space.
    at = font.faces[0].codes_offset + 0x20 * 2
    output[at:at + 2] = space_code
    struct.pack_into("<I", output, font.faces[0].descriptor + 4, len(output))
    output.extend(space_code + bytes((0, ASCII_SPACE_ADVANCE)) + bytes(4))
    records = metric_records(data, font.faces[2].metrics_offset)
    struct.pack_into("<I", output, font.faces[2].descriptor + 4, len(output))
    output.extend(b"".join(records) + space_code + bytes((0, ASCII_SPACE_ADVANCE)) + bytes(4))

    at = font.faces[0].pixels_offset + 0x7e * 160
    glyph = data[at:at + 160]
    shift = 6 * 8
    require(not any(glyph[-shift:]), "Lowering ASCII tilde would clip pixels")
    output[at:at + 160] = bytes(shift) + glyph[:-shift]
    Gothic(bytes(output))
    return bytes(output)


def validate_display_text(data, font):
    for control, raw in tokens(data):
        if not control and len(raw) == 2:
            require(raw != b"\xf0\x40", "Reserved ASCII-space metric key must not appear as text")
            require(raw in font.display_codes, f"Character code {raw.hex().upper()} has no glyph in GOTHIC")


def prepare_font(characters, font_path, *, size=18):
    if font_path is None:
        font_path, size = DEFAULT_FONT, 17
    original = original_font()
    mapping = xs2_hangul.mapping(characters, "cp932")
    require(font_path is not None and font_path.is_file(), "Hangul needs --font pointing to a Korean TTF/OTF")
    all_codes = xs2_hangul.mapping((k for k, _ in xs2_hangul.rows()), "cp932")
    glyphs = render_glyphs(all_codes, font_path, size=size)
    font = Gothic(original)
    lookup = {code: i for i, code in enumerate(font.codes(1))}
    require(set(all_codes.values()) <= lookup.keys(), "Mapped GOTHIC kanji cell is missing")
    output = bytearray(original)
    for char, code in all_codes.items():
        at = font.faces[1].pixels_offset + lookup[code] * 200
        output[at:at + 200] = pack_pixels(glyphs[char])
    hangul_only = bytes(output)
    rebuilt = Gothic(hangul_only)
    require(rebuilt.codes(1) == font.codes(1) and rebuilt.bitmap(0) == font.bitmap(0),
            "GOTHIC code map or half-width glyphs changed")
    targets = set(all_codes.values())
    for i, code in enumerate(font.codes(1)):
        if code not in targets:
            require(rebuilt.glyph(1, i) == font.glyph(1, i), "Unapproved GOTHIC glyph changed")
    decoded = adjust_shared_typography(hangul_only)
    container = font_container(decoded)
    report = {"font_file": str(font_path.resolve()), "font_sha256": digest(font_path.read_bytes()), "raster_size": size,
              "original_decoded_sha256": FONT_HASH, "characters": len(mapping),
              "decoded_size": len(decoded), "decoded_sha256": digest(decoded),
              "stored_size": len(container), "stored_sha256": digest(container),
              "installed_hangul_glyphs": 2350, "hangul_order": "EUC-KR / XS2 fixed kanji table",
              "typography": {"fixed_ascii_space_px": ASCII_SPACE_ADVANCE, "ascii_space_metric_key": "F040",
                             "proportional_ascii_space_px": ASCII_SPACE_ADVANCE,
                             "proportional_space_preserved": False, "ideographic_space_preserved": True,
                             "ascii_tilde_shift_y": 6},
              "mapping": {c: v.hex().upper() for c, v in mapping.items()}}
    for component in ("LNCH", "ADV", "DTB"):
        source = REPO / "decompressed" / component / "DAT00/GOTHIC.DAT/0000.bin"
        require(digest(source.read_bytes()) == FONT_HASH, f"Shared font differs: {component}")
    return mapping, decoded, container, report


def write_generated(path, data):
    destination = path.resolve()
    require(destination == REPO.resolve() / "build.json"
            or any(destination.is_relative_to(root.resolve()) for root in (WORK, REPO / "graphics", REPO / "rebuilt")),
            "Generated output escapes graphics/rebuilt or the root build.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("xb") as stream:
        stream.write(data)
    require(digest(partial.read_bytes()) == digest(data), "Generated file write differs")
    partial.replace(path)


def preview(data, mapping, text, path):
    font = Gothic(data)
    codes = {code: i for i, code in enumerate(font.codes(1))}
    line = Image.new("RGBA", (len(text) * 20, 20))
    for index, char in enumerate(text):
        code = mapping[char] if char in mapping else char.encode("cp932")
        require(code in codes, f"Preview character missing from full-width font: {char!r}")
        tile = Image.frombytes("RGBA", (20, 20), font.rgba_glyph(1, codes[code]))
        line.alpha_composite(tile, (index * 20, 0))
    # Keep a native-size row and an enlarged row without resampling the glyphs.
    image = Image.new("RGBA", (max(360, line.width * 3 + 16), 112), (36, 44, 48, 255))
    image.alpha_composite(line, (8, 8))
    image.alpha_composite(line.resize((line.width * 3, 60), Image.Resampling.NEAREST), (8, 40))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, required=True, help="A local Korean TTF/OTF; not copied or redistributed")
    args = parser.parse_args()
    require(args.font.is_file(), "Font file does not exist")
    characters = translation_characters()
    mapping, decoded, container, report = prepare_font(characters, args.font)
    if container is not None:
        for component in ("LNCH", "ADV", "DTB"):
            write_generated(WORK / "rebuilt" / component / "DAT00/GOTHIC.DAT.new", container)
        write_generated(WORK / "font/GOTHIC.bin", decoded)
        write_generated(WORK / "font/mapping.json", (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"[PASS] 2350 fixed Hangul cells; {len(mapping)} used by translations; other glyphs preserved")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"[ERROR] {error}")
