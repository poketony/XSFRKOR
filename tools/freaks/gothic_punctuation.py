"""Tight punctuation for the ADV/DB font variants; other glyphs stay unchanged."""

from functools import cache
import struct

from adv_format import require, tokens
from gothic import ASCII_SPACE_ADVANCE, Gothic, metric_records


PUNCTUATION = ("\u2018\u2019\u201c\u201d\u300c\u300d\u300e\u300f"
               "\uff08\uff09\uff3b\uff3d\u3008\u3009\u300a\u300b"
               "\uff01\uff1f\u3001\u3002\uff0c\uff0e\uff1a\uff1b"
               "\u30fb\u2025\u2026\u301c\uff5e\uff0d")
ASCII = "'\".,!?():;[]-/~"


@cache
def half_codes():
    from font_tool import original_font
    return tuple(Gothic(original_font()).codes(0))


@cache
def policy():
    from font_tool import original_font
    font = Gothic(original_font())
    half = font.codes(0)
    codes = {c.encode("cp932") for c in PUNCTUATION} | {half[ord(c)] for c in ASCII}
    result = {}
    for code in sorted(codes):
        glyphs = []
        for face in (0, 1):
            f = font.faces[face]
            for index, mapped in enumerate(font.codes(face)):
                if mapped != code:
                    continue
                pixels = font.glyph(face, index)
                columns = [i % f.width for i, value in enumerate(pixels) if value]
                require(columns, f"DB punctuation has no visible glyph: {code.hex()}")
                left, right = min(columns), max(columns) + 1
                glyphs.append((face, index, left, right))
        require(glyphs, f"DB punctuation code is not in GOTHIC: {code.hex()}")
        advance = max(right - left for _, _, left, right in glyphs) + 2
        if advance >= min(font.faces[face].width for face, *_ in glyphs):
            continue
        result[code] = {"advance": max(4, advance), "glyphs": glyphs}
    return result


def dialogue_space_advance():
    return policy()["\u300c".encode("cp932")]["advance"]


def width(raw, *, ideographic_space_advance=20):
    half = half_codes()
    metrics = policy()
    total = 0
    for control, code in tokens(raw):
        if control:
            continue
        if code == b" ":
            total += ASCII_SPACE_ADVANCE
        elif code == b"\x81\x40":
            total += ideographic_space_advance
        else:
            mapped = half[code[0]] if len(code) == 1 else code
            total += metrics.get(mapped, {}).get("advance", 16 if len(code) == 1 else 20)
    return total


def dialogue_width(raw):
    from adv_spacing import preserve_runs
    return width(preserve_runs(raw), ideographic_space_advance=dialogue_space_advance())


def adjust(raw, *, dialogue=False):
    if dialogue:
        from adv_spacing import install_layout_space
        raw = install_layout_space(raw)
    font = Gothic(raw)
    output = bytearray(raw)
    metrics = dict(policy())
    if dialogue:
        metrics[b"\x81\x40"] = {"advance": dialogue_space_advance(), "glyphs": []}
    touched = set()
    for code, row in metrics.items():
        for face, index, left, right in row["glyphs"]:
            f = font.faces[face]
            require(font.codes(face)[index] == code, "DB punctuation glyph map changed")
            at = f.pixels_offset + index * f.width * f.height // 2
            if at in touched:
                continue
            touched.add(at)
            pixels = font.glyph(face, index)
            shifted = bytearray(len(pixels))
            for y in range(f.height):
                start = y * f.width
                shifted[start + 1:start + 1 + right - left] = pixels[start + left:start + right]
            require(sum(shifted) == sum(pixels), "Tightening DB punctuation clipped a glyph")
            output[at:at + len(pixels) // 2] = bytes(shifted[i] | shifted[i + 1] << 4
                                                    for i in range(0, len(shifted), 2))
    for f in font.faces:
        records = [r for r in metric_records(raw, f.metrics_offset) if r[:2] not in metrics]
        records.extend(code + bytes((0, row["advance"])) for code, row in sorted(metrics.items()))
        at = len(output)
        output.extend(b"".join(records) + bytes(4))
        struct.pack_into("<I", output, f.descriptor + 4, at)
    rebuilt = Gothic(bytes(output))
    for face in (0, 1):
        for index, code in enumerate(font.codes(face)):
            if code not in metrics:
                require(rebuilt.glyph(face, index) == font.glyph(face, index), "Unrelated DB glyph changed")
    return bytes(output), {"scope": "ADV/DTB GOTHIC; Hangul/Latin glyphs and LNCH/MINI/EXP fonts unchanged",
                           "ideographic_space_px": dialogue_space_advance() if dialogue else 20,
                           "advances": {code.hex().upper(): row["advance"] for code, row in metrics.items()}}
