"""Freaks' shared GOTHIC font: four descriptors, sorted code map, packed pixels."""

from dataclasses import dataclass
import struct

from adv_format import require, tokens


DECODED_LIMIT = 0x200000
STORED_LIMIT = 0x100000
ASCII_SPACE_ADVANCE = 10


def fixed_width(data):
    return sum(ASCII_SPACE_ADVANCE if raw == b" " else 20 if len(raw) == 2 else 16
               for control, raw in tokens(data) if not control)


@dataclass(frozen=True)
class Face:
    descriptor: int
    width: int
    height: int
    flags: int
    metrics_offset: int
    count: int
    codes_offset: int
    pixels_offset: int


def metric_records(data, offset):
    records = []
    while offset + 4 <= len(data):
        record = data[offset:offset + 4]
        offset += 4
        if record[:2] == b"\0\0":
            return records
        records.append(record)
    raise ValueError("Font metric table has no terminator")


class Gothic:
    def __init__(self, data):
        require(len(data) >= 1176 and len(data) <= DECODED_LIMIT, "Font buffer limit/header differs")
        require(struct.unpack_from("<I", data, 1024)[0] == 1, "Unexpected font family count")
        self.data = data
        self.faces = []
        for descriptor in struct.unpack_from("<4I", data, 1080):
            require(1096 <= descriptor <= 1156 and descriptor + 20 <= len(data), "Bad font descriptor")
            word, metrics, count, codes, pixels = struct.unpack_from("<5I", data, descriptor)
            width, height = word & 255, word >> 8 & 255
            require(width in (16, 20) and height == 20 and count > 0, "Unexpected glyph size")
            require(codes >= 1176 and codes + count * 2 <= len(data), "Font code map outside file")
            require(pixels >= 1176 and pixels + count * width * height // 2 <= len(data),
                    "Font pixels outside file")
            require(1028 <= metrics < len(data), "Font metrics outside file")
            metric_records(data, metrics)
            self.faces.append(Face(descriptor, width, height, word >> 16, metrics, count, codes, pixels))
        require(tuple(f.descriptor for f in self.faces) == (1096, 1116, 1136, 1156),
                "Font descriptor layout differs")
        require(self.codes(1) == self.codes(3) and self.bitmap(1) == self.bitmap(3)
                and self.codes(0) == self.codes(2) and self.bitmap(0) == self.bitmap(2),
                "Expected shared fixed/proportional glyph data")
        codes = self.codes(1)
        require(codes == sorted(set(codes)), "Full-width map must be unique and big-endian sorted")
        self.display_codes = frozenset(self.codes(0) + codes)

    def codes(self, face):
        f = self.faces[face]
        return [self.data[f.codes_offset + i * 2:f.codes_offset + i * 2 + 2] for i in range(f.count)]

    def bitmap(self, face):
        f = self.faces[face]
        return self.data[f.pixels_offset:f.pixels_offset + f.count * f.width * f.height // 2]

    def glyph(self, face, index):
        f = self.faces[face]
        require(0 <= index < f.count, "Glyph index outside face")
        size = f.width * f.height // 2
        at = f.pixels_offset + index * size
        raw = self.data[at:at + size]
        return bytes(v for byte in raw for v in (byte & 15, byte >> 4))

    def rgba_glyph(self, face, index, bank=0):
        require(0 <= bank < 16, "Palette bank outside font")
        result = bytearray()
        for value in self.glyph(face, index):
            logical = value + bank * 16
            stored = (logical & ~0x18) | (logical & 8) << 1 | (logical & 16) >> 1
            at = stored * 4
            rgba = self.data[at:at + 4]
            result.extend(rgba[:3] + bytes((min(255, rgba[3] * 2),)))
        return bytes(result)


def available_codes(font):
    used = set(font.codes(0)) | set(font.codes(1))
    private, undefined = [], []
    for lead in (*range(0x81, 0xa0), *range(0xe0, 0xfd)):
        for trail in (*range(0x40, 0x7f), *range(0x80, 0xfd)):
            code = bytes((lead, trail))
            if code in used:
                continue
            try:
                char = code.decode("cp932")
            except UnicodeDecodeError:
                undefined.append(code)
                continue
            if 0xe000 <= ord(char) <= 0xf8ff:
                private.append(code)
    return private + undefined


def make_mapping(font, characters):
    characters = sorted(set(characters))
    require(all(len(c) == 1 and (0x1100 <= ord(c) <= 0x11ff or 0x3130 <= ord(c) <= 0x318f
                               or 0xac00 <= ord(c) <= 0xd7a3) for c in characters),
            "Only Hangul syllables/Jamo may be assigned new font codes")
    codes = available_codes(font)
    require(len(characters) <= len(codes), f"Font code budget exceeded: {len(characters)} > {len(codes)}")
    return dict(zip(characters, codes))


def pack_pixels(pixels):
    require(len(pixels) == 400 and all(v <= 15 for v in pixels), "Expected 20x20 4-bit glyph")
    return bytes(pixels[i] | pixels[i + 1] << 4 for i in range(0, len(pixels), 2))


def add_glyphs(data, mapping, glyphs):
    font = Gothic(data)
    require(set(mapping) == set(glyphs), "Glyphs and font mapping disagree")
    if not mapping:
        return data
    require(len(set(mapping.values())) == len(mapping), "Duplicate added font code")
    require(set(mapping.values()) <= set(available_codes(font)), "Font code overwrites an existing/defined character")
    old_codes = font.codes(1)
    old_bitmap = font.bitmap(1)
    images = {code: old_bitmap[i * 200:(i + 1) * 200] for i, code in enumerate(old_codes)}
    for char, code in mapping.items():
        images[code] = pack_pixels(glyphs[char])
    codes = sorted(images)
    output = bytearray(data[:1176])

    def append(block):
        offset = len(output)
        output.extend(block)
        return offset

    half_codes = append(b"".join(font.codes(0)))
    full_codes = append(b"".join(codes))
    half_pixels = append(font.bitmap(0))
    full_pixels = append(b"".join(images[code] for code in codes))
    half_metrics = append(b"".join(metric_records(data, font.faces[2].metrics_offset)) + b"\0" * 4)
    metrics = metric_records(data, font.faces[3].metrics_offset)
    metrics.extend(code + b"\0\x14" for code in mapping.values())
    full_metrics = append(b"".join(metrics) + b"\0" * 4)
    for index, face in enumerate(font.faces):
        if index % 2:
            fields = (face.metrics_offset if index == 1 else full_metrics, len(codes), full_codes, full_pixels)
        else:
            fields = (face.metrics_offset if index == 0 else half_metrics, face.count, half_codes, half_pixels)
        struct.pack_into("<4I", output, face.descriptor + 4, *fields)
    require(len(output) <= DECODED_LIMIT, f"Font decoded buffer exceeded: {len(output)} > {DECODED_LIMIT}")
    rebuilt = Gothic(bytes(output))
    indices = {code: i for i, code in enumerate(codes)}
    for i, code in enumerate(old_codes):
        require(rebuilt.glyph(1, indices[code]) == font.glyph(1, i), "Original glyph changed")
    require(rebuilt.bitmap(0) == font.bitmap(0), "Original half-width glyphs changed")
    return bytes(output)
