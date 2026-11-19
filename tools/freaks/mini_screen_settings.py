"""Screen-settings Korean filter-label width; retain original controller and centre."""

import struct

from elf_probe import Elf
from unpack_disc import require

TEXTURE = 157
SOFT = 1214
OLD_UV = (428, 21, 60, 22)
NEW_UV = (428, 21, 80, 22)
OPTION_TABLE = 0x21c928
SOFT_X = 0x21c930
OPTIONS = ((1214 << 8, (1214 << 8) | 1, 323), ((1215 << 8) | 1, 1215 << 8, 400))
STATIC = ((1220, 168, 98), (1221, 148, 164), (1222, 149, 204), (1223, 149, 269),
          (1209, 191, 178), (1210, 191, 214), (1211, 149, 248), (1212, 191, 279), (1213, 150, 310))
WHITE = (0, 1, 2, 3, 4, 5, 6, 7, 8)
GREEN = (0, 8, 12, 13, 14, 15)


def requested(sprites, edits):
    return TEXTURE in edits and edits[TEXTURE].convert("RGBA").tobytes() != sprites.image(TEXTURE).tobytes()


def prepare_table(sprites, table):
    at = SOFT * 32
    require(table[at:at + 32] == sprites.files["spritetbl.bin"][at:at + 32], "Screen filter descriptor preimage differs")
    f = list(struct.unpack_from("<16H", table, at))
    require(f[0] == TEXTURE and tuple(f[2:10]) == (60, 22, 0, 0, *OLD_UV), "Original screen filter geometry differs")
    f[2], f[8] = NEW_UV[2], NEW_UV[2]
    out = bytearray(table)
    struct.pack_into("<16H", out, at, *f)
    return bytes(out)


def source_tables(data):
    e = Elf(data)
    rows = [struct.unpack_from("<Iii", data, e.offset(0x21c8b8) + k * 12) for k in range(9)]
    require(rows == [(n << 8, x, y) for n, x, y in STATIC], "Original screen instruction table differs")
    options = [struct.unpack_from("<3I", data, e.offset(OPTION_TABLE) + k * 12) for k in range(2)]
    require(options == list(OPTIONS), "Original screen filter table differs")
    return rows, options


def layout(sprites):
    import wwrom
    source_tables(wwrom.originals()[0])
    f = struct.unpack_from("<16H", sprites.files["spritetbl.bin"], SOFT * 32)
    require(f[2:10] == (80, 22, 0, 0, *NEW_UV), "Korean screen filter geometry differs")
    result = dict(soft_sprite=SOFT, old_width=60, width=80, old_x=323, x=313, centre=353,
                  caption="부드럽게", sharp_caption="선명하게", title="화면 설정",
                  title_glyph_count=4, unused_overlap_sprite=1216)
    patch_elf(wwrom.originals()[0], result)
    return result


def patch_elf(data, report):
    import wwrom
    source = wwrom.originals()[0]
    e, old, out = Elf(data), Elf(source), bytearray(data)
    source_tables(source)
    at, before = e.offset(OPTION_TABLE), old.offset(OPTION_TABLE)
    require(data[at:at + 24] == source[before:before + 24], "Screen filter table preimage differs")
    require(report["soft_sprite"] == SOFT and report["old_width"] == 60 and report["width"] == 80
            and report["old_x"] == 323 and report["x"] == 313
            and report["centre"] == report["x"] + report["width"] / 2 == 353, "Screen filter centre differs")
    struct.pack_into("<I", out, e.offset(SOFT_X), report["x"])
    return bytes(out)
