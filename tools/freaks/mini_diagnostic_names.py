"""Geometry for the translated name strips in the MINI diagnosis screens."""

from dataclasses import dataclass
import re
import struct

from PIL import Image, ImageDraw

from elf_probe import Elf

from build_paths import DATA, WORK
from unpack_disc import require


SHEETS = tuple(range(129, 140))
NAMES = DATA / "mini/DiagnosticNames.md"
DETACHED = {1020, 1037}
DETACHED_TREND_HANDLE = 0x1cc444
YELLOW = (248, 184, 0, 255)


@dataclass(frozen=True)
class Strip:
    sprite: int
    original: str
    name: str
    x: int
    y: int
    pitch: int
    height: int
    extra: int = 0

    @property
    def visible_name(self):
        return self.name.replace(" ", "")

    @property
    def width(self):
        return (len(self.visible_name) - 1) * self.pitch + self.height + self.extra

    @property
    def source_width(self):
        return (len(self.name) - 1) * self.pitch + self.height + self.extra

    @property
    def pivot(self):
        if self.sprite == 1018:  # The last S follows this strip at the original pitch.
            return (self.width + self.pitch) // 2
        if self.sprite == 1020:
            return (7 * self.pitch - 6) // 2 - 6 * self.pitch - 1
        return self.width // 2

    @property
    def box(self):
        return self.x, self.y, self.x + self.width, self.y + self.height


def fields(sprites, sprite):
    return struct.unpack_from("<16h", sprites.files["spritetbl.bin"], sprite * 32)


def layout(sprites):
    rows = {i: [] for i in SHEETS}
    for index, original, name in re.findall(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|",
                                           NAMES.read_text(encoding="utf-8"), re.M):
        index = int(index)
        if index in rows:
            rows[index].append((original, name.split(" (", 1)[0]))
    rows[134] = [(j, k) for j, k in rows[134] if j != "\u7b4b\u8089"]  # Art outside every native rectangle.
    result, small, large = {}, {}, {}
    for index, entries in rows.items():
        texture = sprites.textures[index]
        ids = sorted((i for i in range(texture.sprite_start, texture.sprite_start + texture.sprites)
                      if i not in DETACHED), key=lambda i: fields(sprites, i)[7:5:-1])
        require(len(entries) == len(ids), f"MINI diagnostic translation rows differ: {index}")
        strips = []
        for sprite, (original, name) in zip(ids, entries):
            require(name.strip() and all(c == " " or not c.isspace() for c in name),
                    f"Invalid MINI diagnostic name: {name!r}")
            names = small if index < 134 else large
            require(original not in names, f"Duplicate MINI diagnostic name: {original}")
            names[original] = name
            _, _, w, h, _, _, x, y, uw, uh, *_ = fields(sprites, sprite)
            require((w, h) == (uw, uh) and h == (26 if index < 134 else 34),
                    f"Unexpected MINI diagnosis sprite geometry: {sprite}")
            pitch = h + 6
            if sprite == 1018:
                require(name == "KOS-MOS", "MINI diagnosis KOS-MOS split expects its original spelling")
                name = name[:-1]
            if sprite in (1012, 1014) and len(name) == 7:
                pitch = 36  # Seven full-size tiles fit with narrower gutters.
            if sprite in (1025, 1028, 1033):
                x, y = {1025: (120, 0), 1028: (0, 144), 1033: (160, 36)}[sprite]
            strips.append(Strip(sprite, original, name, x, y, pitch, h))
        if index == 137:
            strips.append(Strip(1020, "KOS-MOS", "S", 200, 216, 40, 34, 6))
        for n, s in enumerate(strips):
            require(0 <= s.x <= s.x + s.source_width <= texture.width and 0 <= s.y <= s.y + s.height <= texture.height,
                    f"MINI diagnostic name does not fit its atlas: {s.name}")
            require(not any(s.x < t.x + t.source_width and t.x < s.x + s.source_width
                            and s.y < t.y + t.height and t.y < s.y + s.height for t in strips[:n]),
                    f"Overlapping MINI diagnosis sprite: {s.name}")
        result[index] = strips
    require(small == large and len(small) == 45, "MINI diagnostic small/large name lists disagree")
    return result


def template(sprites, index, strips):
    texture = sprites.textures[index]
    image = Image.new("RGBA", (texture.width, texture.height))
    draw = ImageDraw.Draw(image)
    for strip in strips:
        for i in range(len(strip.name)):
            x = strip.x + i * strip.pitch
            draw.rectangle((x + 1, strip.y + 1, x + strip.height - 2, strip.y + strip.height - 2),
                           fill=YELLOW)
    return image


def prepare(sprites, edits):
    indices = [i for i in SHEETS if i in edits and edits[i].tobytes() != sprites.image(i).tobytes()]
    original_table = sprites.files["spritetbl.bin"]
    if not indices:
        return edits, original_table, []
    rows = layout(sprites)
    table = bytearray(original_table)
    textures = dict(edits)
    report = []
    for index in indices:
        source, edited = sprites.image(index), edits[index]
        require(edited.size == source.size, f"MINI_SPR_{index:03}_KOR.png: keep the original canvas size")
        alpha = edited.getchannel("A")
        for s in rows[index]:
            for i in range(len(s.name)):
                x = s.x + i * s.pitch
                require(alpha.getpixel((x + s.height // 2, s.y + s.height // 2)),
                        f"MINI_SPR_{index:03}_KOR.png: missing tile {i + 1} of {s.name}; use the diagnosis layout")
                if i:
                    require(not alpha.crop((x - 1, s.y, x + 1, s.y + s.height)).getbbox(),
                            f"MINI_SPR_{index:03}_KOR.png: merged tiles in {s.name}; use the diagnosis layout")
            if " " in s.name:
                packed = Image.new("RGBA", (s.width, s.height))
                target = 0
                for i, letter in enumerate(s.name):
                    if letter == " ":
                        continue
                    x = s.x + i * s.pitch
                    width = s.height + (s.extra if i == len(s.name) - 1 else 0)
                    packed.paste(edited.crop((x, s.y, x + width, s.y + s.height)), (target, 0))
                    target += s.pitch
                source.paste(packed, s.box)
            else:
                source.paste(edited.crop(s.box), s.box)
            struct.pack_into("<h", table, s.sprite * 32 + 4, s.width)
            struct.pack_into("<h", table, s.sprite * 32 + 8, s.pivot)
            struct.pack_into("<3h", table, s.sprite * 32 + 12, s.x, s.y, s.width)
            report.append({"sheet": index, "sprite": s.sprite, "name": s.name, "tiles": len(s.visible_name),
                           "source_tiles": len(s.name), "skipped_spaces": s.name.count(" "),
                           "x": s.x, "y": s.y, "width": s.width, "pitch": s.pitch})
        textures[index] = source
    return textures, bytes(table), report


def patch_elf(data, report):
    if not any(row["sheet"] == 139 for row in report):
        return data
    offset = Elf(data).offset(DETACHED_TREND_HANDLE)
    require(struct.unpack_from("<2i", data, offset - 4) == (1035 << 8, 1037 << 8),
            "Unexpected MINI diagnosis split sprite handles")
    return data[:offset] + b"\xff" * 4 + data[offset + 4:]


if __name__ == "__main__":
    import io

    from font_tool import write_generated
    from mini_sprites import originals

    sprites = originals()
    for index, strips in layout(sprites).items():
        buffer = io.BytesIO()
        template(sprites, index, strips).save(buffer, "PNG")
        write_generated(WORK / "diagnostic-name-layout" / f"MINI_SPR_{index:03}.png", buffer.getvalue())
