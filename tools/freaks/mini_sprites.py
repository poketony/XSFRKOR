"""MINI sprite texture/CLUT/source tables, independent of TM++ and GOTHIC."""

import argparse
from dataclasses import dataclass
import io
import json
import struct

from PIL import Image, ImageDraw

from font_tool import write_generated
from mini_banner_roles import award_indices
from tmpp import clut_index
from ulz import compress, decompress
from unpack_contents import digest

from build_paths import DATA, REPO, WORK
from unpack_disc import require


NAMES = ("textureimg.bin", "clutimg.bin", "timid2src.bin", "spritetbl.bin",
         "timid2dst_common.bin", "timid2dst_obj.bin", "timid2dst_bg.bin", "timid2dst_ext.bin")


@dataclass(frozen=True)
class Texture:
    index: int
    offset: int
    end: int
    flags: int
    width: int
    height: int
    palette_start: int
    palettes: int
    sprite_start: int
    sprites: int
    pixels: bytes
    displacement_bits: int = 0

    @property
    def colors(self):
        return 256 if self.flags & 0x40000000 else 16

    def indices(self):
        if self.colors == 256:
            return self.pixels
        return bytes(v for b in self.pixels for v in (b & 15, b >> 4))


class Sprites:
    def __init__(self, files):
        require(set(files) == set(NAMES), "Expected all eight MINI sprite files")
        self.files = files
        source, data, colors, sprites = (files[n] for n in ("timid2src.bin", "textureimg.bin", "clutimg.bin", "spritetbl.bin"))
        require(len(source) == 302 * 16 and len(sprites) == 2060 * 32 and len(colors) == 84224,
                "MINI sprite table/palette sizes differ")
        require(all(a in (0, 64, 128) for a in colors[3::4]), "Unsupported MINI palette alpha")
        self.textures, self.original_uv_overruns = [], []
        sprite_cursor, palette_cursor = 0, 0
        for index in range(302):
            value, width, height, palette, count, first, number = struct.unpack_from("<I6H", source, index * 16)
            offset, flags = value & 0x3fffffff, value & 0xc0000000
            end = struct.unpack_from("<I", source, (index + 1) * 16)[0] & 0x3fffffff if index < 301 else len(data)
            require(0 <= offset < end <= len(data) and (index or offset == 0), "MINI texture offsets are not ordered")
            require(0 < width <= 1024 and 0 < height <= 512 and width * height <= 1 << 20,
                    "MINI texture exceeds the original loader's temporary buffer")
            require(first == sprite_cursor and palette * 64 == palette_cursor and count > 0 and number > 0,
                    "MINI palette/sprite ownership ranges differ")
            size = width * height // (1 if flags & 0x40000000 else 2)
            raw = data[offset:end]
            bits = 0
            if flags & 0x80000000:
                require(len(raw) <= width * height, "MINI compressed texture exceeds native read length")
                raw, streams = decompress(raw)
                bits = streams["displacement_bits"]
            require(len(raw) == size, "MINI texture decoded size differs")
            item = Texture(index, offset, end, flags, width, height, palette, count, first, number, raw, bits)
            self.textures.append(item)
            sprite_cursor += number
            palette_cursor += count * (1024 if item.colors == 256 else 64)
            require(sprite_cursor <= 2060 and palette_cursor <= len(colors), "MINI sprite/palette range exceeds file")
            for sprite in range(first, first + number):
                fields = struct.unpack_from("<16H", sprites, sprite * 32)
                require(fields[0] == index and fields[1] == 0, "MINI sprite belongs to another texture")
                u, v, w, h = fields[6:10]
                if u + w > width or v + h > height:
                    self.original_uv_overruns.append(sprite)
        require(sprite_cursor == 2060 and palette_cursor == len(colors), "MINI sprite/palette trailing bytes")
        self.groups = []
        for kind, (name, count) in enumerate(zip(NAMES[4:], (1, 27, 62, 6))):
            raw = files[name]
            offsets = struct.unpack_from(f"<{count}I", raw)
            require(offsets[0] == count * 4, "MINI destination group directory differs")
            for group, (start, end) in enumerate(zip(offsets, (*offsets[1:], len(raw)))):
                require(start + 4 <= end <= len(raw), "MINI destination group outside file")
                size = struct.unpack_from("<I", raw, start)[0]
                require(start + 4 + size * 4 == end, "MINI destination group length differs")
                ids = struct.unpack_from(f"<{size}I", raw, start + 4)
                require(all(i < 302 for i in ids), "MINI destination has an unknown texture ID")
                self.groups.append((kind, group, ids))
        require({i for _, _, ids in self.groups for i in ids} == set(range(302)), "MINI texture has no destination group")

    def palette(self, index, variant=0):
        item = self.textures[index]
        require(0 <= variant < item.palettes, "MINI palette variant outside texture")
        raw = self.files["clutimg.bin"]
        base = item.palette_start * 64 + variant * item.colors * 4
        result = []
        for color in range(item.colors):
            at = base + (clut_index(color) if item.colors == 256 else color) * 4
            r, g, b, alpha = raw[at:at + 4]
            result.append((r, g, b, min(255, alpha * 2)))
        return result

    def image(self, index, variant=0):
        item = self.textures[index]
        palette = self.palette(index, variant)
        rgba = bytes(c for pixel in item.indices() for c in palette[pixel])
        return Image.frombytes("RGBA", (item.width, item.height), rgba)

    def rebuild(self, edits):
        require(isinstance(edits, dict) and all(type(i) is int and 0 <= i < 302 for i in edits),
                "Unknown MINI texture ID")
        changes, stored, expected_pixels = {}, {}, {}
        for index, image in edits.items():
            item = self.textures[index]
            require(image.size == (item.width, item.height),
                    f"MINI PNG canvas must remain {item.width}x{item.height}: {index:03}")
            explicit = image.info.get("mini_palette_indices")
            reference = image.info.get("mini_palette_reference_indices")
            image = image.convert("RGBA")
            palette = self.palette(index)
            old_indices = item.indices()
            old_pixels = self.image(index).tobytes()
            if reference is not None:
                require(isinstance(reference, bytes) and len(reference) == item.width * item.height
                        and max(reference) < item.colors, "MINI palette reference has invalid dimensions/range")
            seed = explicit if explicit is not None else reference if reference is not None else old_indices
            cached = reference is not None and image.info.get("mini_palette_reference_rgba_sha256") == digest(image.tobytes())
            if image.tobytes() == old_pixels and seed == old_indices:
                continue
            nearest = {}
            targets = [(r * a, g * a, b * a, a * 255) for r, g, b, a in palette]
            if explicit is not None:
                require(isinstance(explicit, bytes) and len(explicit) == item.width * item.height
                        and max(explicit) < item.colors, "Explicit MINI palette indices have invalid dimensions/range")
                require(bytes(c for pixel in explicit for c in palette[pixel]) == image.tobytes(),
                        "Indexed MINI PNG must preserve its original palette entries and transparency")
            indices = bytearray(seed)
            for position, color in (() if explicit is not None or cached else enumerate(image.getdata())):
                old = palette[indices[position]]
                if color == old or color[3] == old[3] == 0:
                    continue
                if color not in nearest:
                    r, g, b, a = color
                    sample = (r * a, g * a, b * a, a * 255)
                    nearest[color] = min(range(item.colors), key=lambda i: sum((x - y) ** 2 for x, y in zip(sample, targets[i])))
                indices[position] = nearest[color]
            award_roles = None
            if index == 301:
                indices, award_roles = award_indices(self, image, indices)
            raw = bytes(indices) if item.colors == 256 else bytes(indices[i] | indices[i + 1] << 4 for i in range(0, len(indices), 2))
            if raw == item.pixels:
                continue
            encoded = compress(raw, item.displacement_bits) if item.displacement_bits else raw
            flags = item.flags
            if len(encoded) > item.width * item.height:
                # The native loader reads at most width*height bytes before decoding.
                encoded, flags = raw, flags & ~0x80000000
            stored[index] = encoded, flags
            expected_pixels[index] = raw
            expected = bytes(c for i in indices for c in palette[i])
            errors = [abs(a - b) for a, b in zip(image.tobytes(), expected)]
            changes[index] = {"index": index, "method": "explicit-original-palette-indices" if explicit is not None
                              else "verified-rgba-palette-cache" if cached
                              else "rgba-with-palette-reference" if reference is not None
                              else "original-palette-nearest-rgba",
                              "max_error": max(errors), "mean_error": sum(errors) / len(errors),
                              "changed_pixels": sum(a != b for a, b in zip(old_indices, indices)),
                              "inner_compressed": bool(flags & 0x80000000)}
            if award_roles is not None:
                changes[index]["award_palette_roles"] = award_roles
        if not changes:
            return self.files, {"changed": [], "textures": 302, "positions": [], "palette_and_sprite_tables_preserved": True}
        data, source, positions = bytearray(), bytearray(self.files["timid2src.bin"]), []
        for item in self.textures:
            raw, flags = stored.get(item.index, (self.files["textureimg.bin"][item.offset:item.end], item.flags))
            offset = len(data)
            require(offset < 1 << 30, "MINI texture offsets exceed native 30-bit field")
            struct.pack_into("<I", source, item.index * 16, flags | offset)
            data.extend(raw)
            positions.append({"index": item.index, "old_offset": item.offset, "offset": offset, "stored_bytes": len(raw)})
        files = dict(self.files, **{"textureimg.bin": bytes(data), "timid2src.bin": bytes(source)})
        rebuilt = Sprites(files)
        for before, after in zip(self.textures, rebuilt.textures):
            require(after.pixels == expected_pixels.get(before.index, before.pixels)
                    and before.colors == after.colors, "Rebuilt MINI pixels/bit depth differ from quantization")
            require(self.files["timid2src.bin"][before.index * 16 + 4:before.index * 16 + 16]
                    == files["timid2src.bin"][before.index * 16 + 4:before.index * 16 + 16],
                    "MINI texture dimensions/palette/sprite range changed")
            if before.index not in changes:
                require(before.pixels == after.pixels
                        and self.files["textureimg.bin"][before.offset:before.end] == files["textureimg.bin"][after.offset:after.end],
                        "Unedited MINI texture stream changed")
        require(all(files[n] == self.files[n] for n in NAMES if n not in ("textureimg.bin", "timid2src.bin")),
                "MINI palette/sprite/destination tables changed")
        return files, {"changed": list(changes.values()), "textures": 302, "positions": positions,
                       "old_texture_bytes": len(self.files["textureimg.bin"]), "texture_bytes": len(data),
                       "palette_and_sprite_tables_preserved": True}

    def review(self, start, count=48):
        require(0 <= start < 302 and 0 < count <= 48, "MINI review page outside catalogue")
        image = Image.new("RGB", (960, ((min(count, 302 - start) + 5) // 6) * 184), (32, 36, 40))
        draw = ImageDraw.Draw(image)
        for slot, index in enumerate(range(start, min(start + count, 302))):
            item = self.textures[index]
            variants = [self.palette(index, v) for v in range(item.palettes)]
            palette = [max((p[c] for p in variants), key=lambda v: (v[3], sum(v[:3]))) for c in range(item.colors)]
            rgba = bytes(c for pixel in item.indices() for c in palette[pixel])
            tile = Image.frombytes("RGBA", (item.width, item.height), rgba)
            tile.thumbnail((156, 156))
            x, y = slot % 6 * 160, slot // 6 * 184
            image.paste(tile, (x + 2, y + 22), tile)
            draw.text((x + 2, y + 2), f"{index:03} {item.width}x{item.height} P{item.palettes}", fill="white")
        return image


def originals():
    report = json.loads((DATA / "contents-report.json").read_text(encoding="utf-8"))
    hashes = {r["name"].removeprefix("spr/"): r["output"]["sha256"] for r in report["members"]
              if r["source"] == "MINI/WWROM.DAT" and r["name"].startswith("spr/")}
    files = {}
    for name in NAMES:
        raw = (REPO / "decompressed/MINI/WWROM.DAT/spr" / name).read_bytes()
        require(digest(raw) == hashes[name], f"Original MINI sprite member changed: {name}")
        files[name] = raw
    return Sprites(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("review",))
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--index", type=int)
    args = parser.parse_args()
    sprites = originals()
    require(args.index is None or 0 <= args.index < 302, "MINI texture index outside catalogue")
    image = sprites.review(args.start) if args.index is None else sprites.image(args.index)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    path = WORK / "evidence/mini-sprite-review.png"
    write_generated(path, buffer.getvalue())
    detail = "palette-union overview" if args.index is None else f"texture {args.index}, original palette 0"
    print(f"[REVIEW] {path}; {detail}; inspection only")
    print(f"[SOURCE] {len(sprites.textures)} textures; original UV overruns: {sprites.original_uv_overruns}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"[ERROR] {error}")
