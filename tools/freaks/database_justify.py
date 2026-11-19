"""Justify explicit DB body lines using DB-only transparent spacing glyphs."""

from dataclasses import dataclass
import re
import struct

from adv_format import encode_text, require, tokens
import database_text as db
from gothic import ASCII_SPACE_ADVANCE, Gothic, available_codes, metric_records


TAGS = re.compile(r"\[LINK:\d{3}\]|\[/LINK\]")


def gaps(line):
    visible, positions, cursor = [], [], 0
    for match in TAGS.finditer(line):
        visible.extend(line[cursor:match.start()])
        positions.extend(range(cursor, match.start()))
        cursor = match.end()
    visible.extend(line[cursor:])
    positions.extend(range(cursor, len(line)))
    nonspace = [i for i, char in enumerate(visible) if char != " "]
    if not nonspace:
        return []
    return [positions[i] for i in range(nonspace[0] + 1, nonspace[-1])
            if visible[i] == " " and visible[i + 1] != " "]


def chunks(extra):
    width = extra + ASCII_SPACE_ADVANCE
    count, last = divmod(width - 1, 20)
    return [(20, False)] * count + [(last + 1, True)]


@dataclass
class Justification:
    widths: dict
    mapping: dict
    font: bytes
    translations: dict
    restorations: dict
    measure_plain: object
    report: dict

    def measure(self, raw):
        return sum(self.widths[code] if code in self.widths else self.measure_plain(code)
                   for control, code in tokens(raw) if not control)

    def restore(self, raw):
        return b"".join(self.restorations.get(code, code) for _, code in tokens(raw))


def prepare(translations, mapping, font_data, measure):
    planned, widths, skipped = {}, set(), []
    for (index, kind), text in translations.items():
        if kind != "BODY" or not text:
            continue
        lines = text.split("\n")
        for number, line in enumerate(lines[:-1]):
            if not line or not lines[number + 1]:
                continue
            plain = TAGS.sub("", line)
            extra = db.BODY_WIDTH - measure(encode_text(plain, mapping))
            require(extra >= 0, f"[{index:03}-BODY] Line {number + 1} exceeds 600px")
            if not extra:
                continue
            positions = gaps(line)
            if not positions:
                skipped.append([index, number, "no word spaces"])
                continue
            amounts = [(i + 1) * extra // len(positions) - i * extra // len(positions)
                       for i in range(len(positions))]
            planned[index, number] = dict(zip(positions, amounts))
            widths.update(part for n in amounts if n for part in chunks(n))

    if widths:
        widths = {(n, True) for n in range(1, 21)} | {(20, False)}
    font = Gothic(font_data)
    codes = available_codes(font)
    require(len(widths) <= len(codes), "DB justification has no unused font codes")
    markers = {n: chr(0xe000 + i) for i, n in enumerate(sorted(widths))}
    require(not any(char in text for char in markers.values() for text in translations.values()),
            "DB source contains a private spacing marker")
    spacing = dict(zip(markers.values(), codes))
    code_widths = {spacing[markers[n]]: n[0] for n in markers}
    restorations = {spacing[markers[n]]: b" " if n[1] else b"" for n in markers}
    output_font = bytearray(font_data)
    if spacing:
        bitmap = font.bitmap(1)
        images = {code: bitmap[i * 200:(i + 1) * 200] for i, code in enumerate(font.codes(1))}
        images.update({code: bytes(200) for code in spacing.values()})
        full_codes = len(output_font)
        output_font.extend(b"".join(sorted(images)))
        full_pixels = len(output_font)
        output_font.extend(b"".join(images[code] for code in sorted(images)))
        for face in (font.faces[1], font.faces[3]):
            struct.pack_into("<3I", output_font, face.descriptor + 8, len(images), full_codes, full_pixels)
    for face in font.faces if code_widths else ():
        records = metric_records(font_data, face.metrics_offset)
        records.extend(code + bytes((0, n)) for code, n in sorted(code_widths.items()))
        at = len(output_font)
        output_font.extend(b"".join(records) + bytes(4))
        struct.pack_into("<I", output_font, face.descriptor + 4, at)
    output_font = bytes(output_font)
    Gothic(output_font)

    result = dict(translations)
    original = {markers[n]: " " if n[1] else "" for n in markers}
    for (index, kind), text in translations.items():
        if kind != "BODY" or not text:
            continue
        lines = text.split("\n")
        for number, line in enumerate(lines):
            additions = planned.get((index, number), {})
            lines[number] = "".join("".join(markers[n] for n in chunks(additions[i]))
                                     if additions.get(i) else char for i, char in enumerate(line))
        result[index, kind] = "\n".join(lines)
        require("".join(original.get(c, c) for c in result[index, kind]) == text,
                "DB justification changed text, tags or line breaks")
    report = {"width": db.BODY_WIDTH, "justified_lines": len(planned),
              "spacing_glyphs": len(spacing), "spacing_advances": sorted(set(code_widths.values())),
              "unexpanded_lines": skipped, "last_lines": "left aligned",
              "source_text_and_line_breaks": "unchanged", "scope": "DTB body only; no ELF code patch"}
    return Justification(code_widths, {**mapping, **spacing}, output_font, result, restorations, measure, report)
