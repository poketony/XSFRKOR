"""Keep the award panels' fixed rims separate from their flashing lettering."""

import struct

from unpack_disc import require


TEXTURE = 301
SPRITES = (2043, 2044)


def award_indices(sprites, image, indices):
    palette = sprites.palette(TEXTURE)
    require(image.size == (512, 256) and palette[1] == palette[4] and palette[3] == palette[7],
            "MINI award banner palette/dimensions differ")
    targets = [(r * a, g * a, b * a, a * 255) for r, g, b, a in palette]
    result, report = bytearray(indices), []
    for number in SPRITES:
        fields = struct.unpack_from("<16H", sprites.files["spritetbl.bin"], number * 32)
        require(fields[0] == TEXTURE, "MINI award sprite belongs to another texture")
        u, v, width, height = fields[6:10]
        colors = list(image.crop((u, v, u + width, v + height)).getdata())
        ink = {p for p, color in enumerate(colors) if color[3] and color != palette[1]}
        require(ink, "MINI award banner has no rim")
        left, right = min(p % width for p in ink), max(p % width for p in ink)
        top, bottom = min(p // width for p in ink), max(p // width for p in ink)
        rim = {p for p in ink if p % width in (left, right) or p // width in (top, bottom)}
        pending = list(rim)
        # The outer connected ink is the rim; disconnected interior ink is text.
        # Never reuse the original Japanese glyph mask for equal-colour pixels.
        while pending:
            p = pending.pop()
            x, y = p % width, p // width
            for yy in range(max(0, y - 1), min(height, y + 2)):
                for xx in range(max(0, x - 1), min(width, x + 2)):
                    neighbor = yy * width + xx
                    if neighbor in ink and neighbor not in rim:
                        rim.add(neighbor)
                        pending.append(neighbor)
        nearest, changed = {}, 0
        for p, color in enumerate(colors):
            is_text = p in ink and p not in rim
            key = color, is_text
            if key not in nearest:
                r, g, b, a = color
                sample = r * a, g * a, b * a, a * 255
                allowed = (0, 4, 5, 6, 7) if is_text else (0, 1, 2, 3)
                nearest[key] = min(allowed, key=lambda i: sum((x - y) ** 2
                                                            for x, y in zip(sample, targets[i])))
            at = (v + p // width) * image.width + u + p % width
            changed += result[at] != nearest[key]
            result[at] = nearest[key]
        report.append({"sprite": number, "box": [u, v, u + width, v + height],
                       "rim_pixels": len(rim), "text_pixels": len(ink - rim),
                       "remapped_pixels": changed})
    return result, report
