"""The 640x448 tutorial screen stored across MINI textures 159 and 160."""

import struct

from PIL import Image

from unpack_disc import require


EDITOR = 159
CONTINUATION = 160
SIZE = (640, 448)
SPRITES = ((1224, 159, 0, 0), (1225, 159, 512, 0), (1226, 159, 512, 64),
           (1227, 159, 512, 128), (1228, 159, 512, 192),
           (1229, 160, 512, 256), (1230, 160, 512, 384))


def pieces(sprites):
    require((sprites.textures[159].width, sprites.textures[159].height) == (512, 512)
            and (sprites.textures[160].width, sprites.textures[160].height) == (256, 128),
            "MINI tutorial source dimensions changed")
    result = []
    for sprite, source, x, y in SPRITES:
        fields = struct.unpack_from("<16H", sprites.files["spritetbl.bin"], sprite * 32)
        u, v, width, height = fields[6:10]
        item = sprites.textures[source]
        require(fields[0] == source and u + width <= item.width and v + height <= item.height
                and x + width <= SIZE[0] and y + height <= SIZE[1], "MINI tutorial UV/placement changed")
        result.append((source, u, v, width, height, x, y))
    require(sum(w * h for _, _, _, w, h, _, _ in result) == SIZE[0] * SIZE[1],
            "MINI tutorial pieces do not cover the editor canvas")
    return result


def image(sprites):
    sources = {i: sprites.image(i) for i in (EDITOR, CONTINUATION)}
    result = Image.new("RGBA", SIZE)
    for source, u, v, width, height, x, y in pieces(sprites):
        result.paste(sources[source].crop((u, v, u + width, v + height)), (x, y))
    return result


def changed(before, after):
    return before != after and (before[3] != 0 or after[3] != 0)


def to_textures(sprites, edits):
    result = dict(edits)
    if EDITOR not in edits:
        return result
    edited = edits[EDITOR].convert("RGBA")
    if edited.size == (512, 512):
        return result
    require(edited.size == SIZE, "MINI_SPR_159_KOR must remain 640x448 (legacy 512x512 also accepted)")
    split = {i: sprites.image(i) for i in (EDITOR, CONTINUATION)}
    for source, u, v, width, height, x, y in pieces(sprites):
        split[source].paste(edited.crop((x, y, x + width, y + height)), (u, v))
    if CONTINUATION in edits:
        legacy = edits[CONTINUATION].convert("RGBA")
        require(legacy.size == split[CONTINUATION].size, "Legacy MINI_SPR_160_KOR must remain 256x128")
        original = sprites.image(CONTINUATION)
        merged = []
        for old, full, extra in zip(original.getdata(), split[CONTINUATION].getdata(), legacy.getdata()):
            require(not (changed(old, full) and changed(old, extra) and changed(full, extra)),
                    "MINI_SPR_159_KOR and MINI_SPR_160_KOR change the same pixel differently; use one editor")
            merged.append(extra if changed(old, extra) else full)
        split[CONTINUATION].putdata(merged)
    result.update(split)
    return result
