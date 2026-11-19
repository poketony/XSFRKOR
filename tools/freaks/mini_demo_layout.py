"""Five scrolling demo captions, assembled from the game's real message table."""

from functools import cache
import struct

from PIL import Image

from elf_probe import Elf
import mini_sprite_layout as legacy
from unpack_disc import require
import wwrom


EDITORS = {290: 0, 291: 1, 292: 2, 296: 3, 297: 4}
SOURCES = frozenset((290, 291, 292, 293, 296, 297))
CONTINUATION = 293


@cache
def messages():
    data, _ = wwrom.originals()
    elf = Elf(data)
    result = {}
    for editor, message in EDITORS.items():
        pointer, count, _ = struct.unpack_from("<3I", data, elf.offset(0x1cfc30 + message * 12))
        require(count == (7, 6, 8, 6, 6)[message], "MINI demo caption table changed")
        rows = []
        for n in range(count):
            gap, normal, alternate = struct.unpack_from("<3I", data, elf.offset(pointer + n * 12))
            require(normal & 255 == 0 and alternate == normal + 1,
                    "MINI demo palette variants select different sprites")
            rows.append((gap, normal >> 8))
        result[editor] = tuple(rows)
    return result


def pieces(sprites, editor):
    require(editor in EDITORS, "Unknown MINI demo caption editor")
    result, x = [], 0
    for gap, number in messages()[editor]:
        fields = struct.unpack_from("<16H", sprites.files["spritetbl.bin"], number * 32)
        source, (u, v, width, height) = fields[0], fields[6:10]
        require(source in SOURCES and fields[2:6] == (width, height, 0, 0),
                "MINI demo sprite scale/origin changed")
        texture = sprites.textures[source]
        require(u + width <= texture.width and v + height <= texture.height and height == 64,
                "MINI demo UV is outside its source texture")
        x += gap
        result.append((source, u, v, width, height, x, 0))
        x += width
    return result, (x, 64)


def image(sprites, editor):
    rows, size = pieces(sprites, editor)
    result = Image.new("RGBA", size)
    sources = {i: sprites.image(i) for i in {r[0] for r in rows}}
    for source, u, v, width, height, x, y in rows:
        result.paste(sources[source].crop((u, v, u + width, v + height)), (x, y))
    return result


def affected_editors(sprites, changed_sources):
    selected = set(changed_sources) & SOURCES
    return {editor for editor in EDITORS if any(
        struct.unpack_from("<H", sprites.files["spritetbl.bin"], number * 32)[0] in selected
        for _, number in messages()[editor])}


def trim_table(source, rebuilt, changed_sources, table):
    selected = affected_editors(source, changed_sources)
    if not selected:
        return table, []
    images = {i: rebuilt.image(i) for i in SOURCES}
    originals = {i: source.image(i) for i in SOURCES}
    changed = set()
    for editor in selected:
        for _, number in messages()[editor]:
            f = struct.unpack_from('<16H', source.files['spritetbl.bin'], number * 32)
            u, v, w, h = f[6:10]
            box = (u, v, u + w, v + h)
            if images[f[0]].crop(box).tobytes() != originals[f[0]].crop(box).tobytes():
                changed.add(editor)
                break
    plans = {}
    for editor in sorted(changed):
        runs, current = [], []
        for gap, number in messages()[editor]:
            if gap and current:
                runs.append(current)
                current = []
            current.append(number)
        runs.append(current)
        for numbers in runs:
            fields = [struct.unpack_from('<16H', source.files['spritetbl.bin'], n * 32) for n in numbers]
            alpha = Image.new('L', (sum(f[8] for f in fields), 64))
            cursor = 0
            for f in fields:
                u, v, w, h = f[6:10]
                alpha.paste(images[f[0]].crop((u, v, u + w, v + h)).getchannel('A'), (cursor, 0))
                cursor += w
            box = alpha.getbbox()
            # Retain a transparent texel at newly cropped UV edges for filtering.
            left, right = (max(0, box[0] - 1), min(alpha.width, box[2] + 1)) if box else (0, 0)
            cursor = 0
            for number, f in zip(numbers, fields):
                u, v, w, h = f[6:10]
                lo, hi = max(0, left - cursor), min(w, right - cursor)
                # Keep a harmless transparent texel for a completely empty end
                # piece; never introduce zero-width sprites or remove word spaces.
                cropped = (u + lo, v, hi - lo, h) if lo < hi else (u, v, 1, h)
                ink = images[f[0]].crop((u, v, u + w, v + h)).getchannel('A').getbbox()
                require(ink is None or cropped[0] - u <= ink[0] < ink[2] <= cropped[0] - u + cropped[2],
                        'MINI demo crop would cut visible ink')
                require(number not in plans or plans[number] == cropped, 'MINI demo shared crop conflicts')
                plans[number] = cropped
                cursor += w
    result, report = bytearray(table), []
    for number, cropped in sorted(plans.items()):
        at = number * 32
        require(table[at:at + 32] == source.files['spritetbl.bin'][at:at + 32],
                'MINI demo sprite geometry was already changed')
        f = struct.unpack_from('<16H', table, at)
        old = f[6:10]
        require(f[2:6] == (old[2], old[3], 0, 0) and cropped[1] == old[1] and cropped[3] == 64
                and old[0] <= cropped[0] < cropped[0] + cropped[2] <= old[0] + old[2],
                'MINI demo crop geometry differs')
        if cropped == old:
            continue
        struct.pack_into('<H', result, at + 4, cropped[2])
        struct.pack_into('<H', result, at + 12, cropped[0])
        struct.pack_into('<H', result, at + 16, cropped[2])
        report.append({'sprite': number, 'source_rect': list(old), 'native_rect': list(cropped)})
    return bytes(result), report


def to_textures(sprites, edits):
    result = {i: image for i, image in edits.items() if i not in SOURCES}
    original = {i: sprites.image(i) for i in SOURCES}
    changes = {}
    for editor in sorted(set(edits) & SOURCES):
        edited = edits[editor].convert("RGBA")
        if editor in EDITORS:
            native_size = pieces(sprites, editor)[1]
            require(edited.size in (native_size, original[editor].size, legacy.layout(sprites, editor)[1]),
                    f"MINI demo {editor:03} must remain {native_size[0]}x{native_size[1]}; "
                    "the old per-texture strip and atlas are also accepted")
        if editor in EDITORS and edited.size == pieces(sprites, editor)[1]:
            rows, size = pieces(sprites, editor)
            before = image(sprites, editor)
        else:
            # Old per-texture strips and untouched atlas dimensions remain valid.
            edited = legacy.to_texture(sprites, editor, edited)
            before, size = original[editor], original[editor].size
            rows = [(editor, 0, 0, *size, 0, 0)]
        old, new = list(before.getdata()), list(edited.getdata())
        dirty = bytearray(a != b and (a[3] != 0 or b[3] != 0) for a, b in zip(old, new))
        for source, u, v, width, height, x, y in rows:
            for row in range(height):
                offset = (y + row) * size[0] + x
                for column in range(width):
                    at = offset + column
                    if not dirty[at]:
                        continue
                    target = (source, (v + row) * original[source].width + u + column)
                    previous = changes.get(target)
                    require(previous is None or previous[0] == new[at],
                            f"MINI demo shared pixels conflict between {previous[1] if previous else ''} "
                            f"and {editor:03}; use the same translation for shared text")
                    changes[target] = (new[at], f"{editor:03}")
                    dirty[at] = 0
        require(not any(dirty),
                f"MINI demo {editor:03}: paint in a scrolling gap would not appear in game")
    for source in {i for i, _ in changes}:
        pixels = list(original[source].getdata())
        for (index, position), (color, _) in changes.items():
            if index == source:
                pixels[position] = color
        rebuilt = Image.new("RGBA", original[source].size)
        rebuilt.putdata(pixels)
        result[source] = rebuilt
    return result
