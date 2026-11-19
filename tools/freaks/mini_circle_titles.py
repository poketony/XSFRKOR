"""Private circular menu-title glyphs and bounded original layout edits."""

import struct

from elf_probe import Elf
from unpack_disc import require


TEXTURES = (86, 90, 179)
CELLS = {86: (678, 4, 7), 90: (693, 7, 11), 179: (1495, 6, 8)}
GLYPHS = dict(zip(range(678, 682), '아케이드'))
GLYPHS.update(zip(range(693, 700), '차근차근퍼즐!'))
GLYPHS.update(zip(range(1495, 1501), ('둘', '이', '서', '대', '전', '')))
ARCADE = ((678 << 8, 56, 54), (679 << 8, 101, 52), (680 << 8, 145, 54),
          (679 << 8, 189, 52), (681 << 8, 233, 54))
ARCADE_TABLE = 0x21a218
VERSUS_TABLE = 0x21c6c8
CODE = {0x15d72c: 0x24110004, 0x15d734: 0x24500030,
        0x15d890: 0x24110004, 0x15d898: 0x24500030,
        0x17fe24: 0x2411003e, 0x17fe2c: 0x24140005}


def requested(sprites, edits):
    return {i for i in TEXTURES if i in edits and edits[i].convert('RGBA').tobytes() != sprites.image(i).tobytes()}


def layout(sprites, indices):
    from mini_sprites import originals
    import wwrom
    require(set(indices) <= set(TEXTURES), 'Unknown circular MINI title')
    source = originals()
    result = []
    for index in sorted(indices):
        first, count, _ = CELLS[index]
        for n in range(first, first + count):
            at = n * 32
            f = struct.unpack_from('<16H', sprites.files['spritetbl.bin'], at)
            require(sprites.files['spritetbl.bin'][at:at + 32] == source.files['spritetbl.bin'][at:at + 32],
                    'Circular MINI title geometry changed')
            u, v, w, h = f[6:10]
            visible = sprites.image(index).crop((u, v, u + w, v + h)).getchannel('A').getbbox() is not None
            require(visible == bool(GLYPHS[n]), 'Circular MINI title letter/blank coverage differs')
        item = {'index': index, 'caption': {86: '아케이드', 90: '차근차근 퍼즐!', 179: '둘이서 대전'}[index],
                'source_glyph_count': 5 if index == 86 else count,
                'glyph_count': sum(bool(GLYPHS[n]) for n in range(first, first + count))}
        if index == 86:
            centre = (ARCADE[0][1] + ARCADE[-1][1]) / 2
            step = (ARCADE[-1][1] - ARCADE[0][1]) / (len(ARCADE) - 1)
            active = [(n << 8, round(centre + (k - 1.5) * step), ARCADE[k][2])
                      for k, n in enumerate(range(first, first + count))]
            item.update(rows=[list(r) for r in (*active, ARCADE[-1])], source_centre=centre,
                        centre=(active[0][1] + active[-1][1]) / 2)
        elif index == 179:
            start = round(62 + (6 - item['glyph_count']) * 53 / 2)
            item.update(start_x=start, stride=53, source_centre=62 + 5 * 53 / 2,
                        centre=start + (item['glyph_count'] - 1) * 53 / 2)
        else:
            # No original caller for these seven title sprites was identified.
            item['layout_changed'] = False
        result.append(item)
    patch_elf(wwrom.originals()[0], result)
    return result


def patch_elf(data, captions):
    elf, output = Elf(data), bytearray(data)
    require(len({r['index'] for r in captions}) == len(captions), 'Duplicate circular MINI title')
    for row in captions:
        index = row['index']
        require(index in TEXTURES, 'Unknown circular MINI title')
        changes = {}
        if index == 86:
            start = elf.offset(ARCADE_TABLE)
            original = b''.join(struct.pack('<Iii', *r) for r in ARCADE)
            require(data[start:start + 60] == original, 'Original arcade title table differs')
            require(row['glyph_count'] == 4 and len(row['rows']) == 5
                    and [r[0] for r in row['rows'][:4]] == [n << 8 for n in range(678, 682)]
                    and [r[2] for r in row['rows']] == [r[2] for r in ARCADE]
                    and row['source_centre'] == 144.5
                    and row['centre'] == (row['rows'][0][1] + row['rows'][3][1]) / 2
                    and abs(row['centre'] - row['source_centre']) <= 0.5
                    and all(24 <= r[1] <= 616 for r in row['rows']),
                    'Korean arcade title order/centre differs')
            output[start:start + 60] = b''.join(struct.pack('<Iii', *r) for r in row['rows'])
            changes = {0x15d72c: 3, 0x15d734: 36, 0x15d890: 3, 0x15d898: 36}
        elif index == 179:
            start = elf.offset(VERSUS_TABLE)
            require(data[start:start + 24] == struct.pack('<6I', *[n << 8 for n in range(1495, 1501)]),
                    'Original versus title order differs')
            require(row['glyph_count'] == 5 and row['stride'] == 53 and 0 <= row['start_x'] <= 640
                    and row['source_centre'] == 194.5 and row['centre'] == row['start_x'] + 2 * 53
                    and abs(row['centre'] - row['source_centre']) <= 0.5, 'Korean versus title centre/count differs')
            changes = {0x17fe24: row['start_x'], 0x17fe2c: 4}
        else:
            require(row['glyph_count'] == 7 and not row['layout_changed'], 'Puzzle title slot count changed')
        for address, immediate in changes.items():
            at = elf.offset(address)
            require(struct.unpack_from('<I', data, at)[0] == CODE[address], 'Original circular MINI title instruction differs')
            struct.pack_into('<I', output, at, CODE[address] & 0xffff0000 | immediate)
    return bytes(output)
