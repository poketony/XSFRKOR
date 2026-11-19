"""Original MINI WIX getter and WWROM-backed 256-byte word-record reader."""

import struct

from mini_dictionary import Dictionary, NAMES
import mini_validation_cache
from elf_probe import Elf
from unpack_contents import digest
from unpack_disc import require
from verify_codecs import Native
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import (UC_MIPS_REG_0, UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2,
                               UC_MIPS_REG_A3, UC_MIPS_REG_GP, UC_MIPS_REG_SP,
                               UC_MIPS_REG_RA, UC_MIPS_REG_PC, UC_MIPS_REG_V0)


class DictionaryMachine(Native):
    WDT = 0x400000
    DIX = 0x1400000
    WIX = 0x1500000
    HANDLE = 0x1600000

    def __init__(self, elf_data, dictionary):
        super().__init__(elf_data)
        require(isinstance(dictionary, Dictionary), "Expected checked MINI dictionary")
        self.dictionary = dictionary
        require(len(dictionary.index) < 1 << 20 and len(dictionary.files["dicdata.wix"]) < 1 << 20,
                "Native dictionary probe table buffers exceeded")
        self.uc.mem_write(self.WDT, dictionary.data)
        self.uc.mem_write(self.DIX, dictionary.index)
        self.uc.mem_write(self.WIX, dictionary.files["dicdata.wix"])
        self.uc.mem_write(0x1c7404, struct.pack("<2I", self.DIX, self.WIX))
        self.uc.mem_write(self.HANDLE, struct.pack("<I", self.WDT))
        self.uc.mem_write(0x224578 + 17 * 36, struct.pack("<I", self.HANDLE))
        self.uc.mem_write(0x1bfb58 + 17 * 20 + 12, struct.pack("<I", len(dictionary.data)))

    def call(self, entry, *args):
        self.uc.reg_write(UC_MIPS_REG_GP, self.elf.gp)
        self.uc.reg_write(UC_MIPS_REG_SP, self.STACK)
        self.uc.reg_write(UC_MIPS_REG_RA, self.STOP)
        for register, value in zip((UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3), args):
            self.uc.reg_write(register, value)
        self.uc.emu_start(entry, self.STOP, count=10000)
        require(self.uc.reg_read(UC_MIPS_REG_PC) == self.STOP, "Native MINI dictionary function did not return")
        require(self.uc.reg_read(UC_MIPS_REG_SP) == self.STACK, "Native MINI dictionary stack differs")
        return self.uc.reg_read(UC_MIPS_REG_V0) & 0xffffffff

    def read(self, word_id):
        require(1 <= word_id <= len(self.dictionary.words), "Native dictionary ID outside corpus")
        pointer = self.call(0x115250, word_id, 0)
        require(pointer == self.WIX + (word_id - 1) * 4, "Native WIX entry pointer differs")
        word = self.dictionary.words[word_id - 1]
        target = self.OUTPUT + 1
        self.uc.mem_write(target - 16, b"\xa5" * 288)
        require(self.call(0x115070, pointer, target) == target, "Native MINI word read returned wrong buffer")
        expected = self.dictionary.data[word.offset:word.offset + 256]
        require(bytes(self.uc.mem_read(target, len(expected))) == expected, "Native MINI word bytes differ")
        require(bytes(self.uc.mem_read(target + len(expected), 256 - len(expected)))
                == b"\xa5" * (256 - len(expected)), "Native last word read crossed WDT end")
        require(bytes(self.uc.mem_read(target - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(target + 256, 16)) == b"\xa5" * 16,
                "Native word reader crossed output boundary")
        return expected[:len(word.raw)]

    def verify(self):
        inputs = (self.elf.data, *(self.dictionary.files[name] for name in NAMES))
        return mini_validation_cache.checked("dictionary-records", inputs, self._verify)

    def _verify(self):
        for word in self.dictionary.words:
            require(self.read(word.word_id) == word.raw, "Native MINI dictionary record differs")
        for address, expected in ((self.WDT, self.dictionary.data), (self.DIX, self.dictionary.index),
                                  (self.WIX, self.dictionary.files["dicdata.wix"])):
            require(digest(bytes(self.uc.mem_read(address, len(expected)))) == digest(expected),
                    "Native MINI dictionary source changed")
        return {"wix_getters": len(self.dictionary.words), "word_reads": len(self.dictionary.words),
                "original_partial_read_and_byte_copy": True, "canaries_and_source_hashes": "pass",
                "scope": "Original 0x115250, 0x115070, 0x109EB8 and byte-copy code; decoded WDT handle modelled"}


def verify_definition_layout(elf_data, dictionary, word_ids):
    word_ids = tuple(word_ids)
    inputs = (elf_data, *(dictionary.files[name] for name in NAMES), mini_validation_cache.packed(word_ids))
    result = mini_validation_cache.checked("definition-layout", inputs,
                                           lambda: _verify_definition_layout(elf_data, dictionary, word_ids))
    return {**result, "panel": tuple(result["panel"])}


def _verify_definition_layout(elf_data, dictionary, word_ids):
    from mini_font_native import MiniFontMachine
    native = MiniFontMachine(elf_data)
    box = struct.unpack_from("<4h", elf_data, native.elf.offset(0x214608))
    require(box in ((350, 234, 180, 40), (340, 234, 200, 40)), "Unexpected MINI definition panel")
    count, half_cell_edges, skipped = 0, [], 0
    for word_id in word_ids:
        require(1 <= word_id <= len(dictionary.words), "Unknown MINI layout word ID")
        layout = native.layout(dictionary.words[word_id - 1].definition, box, definition=True)
        # After a 10px space, the original renderer may place one 20px glyph
        # across the nominal right edge. It still lies inside the book paper.
        require(layout["complete"] and all(
            box[0] <= x and x + width <= box[0] + box[2] + 10
            and box[1] <= y and y + height <= box[1] + box[3]
            for x, y, width, height in layout["rectangles"]),
            f"MINI definition does not fit its original panel: {word_id}")
        if not layout["fits"]:
            half_cell_edges.append(word_id)
        skipped += len(layout["skipped_leading_spaces"])
        count += 1
    return {"definitions": count, "panel": box, "original_renderer_layout": "pass",
            "second_line_leading_spaces_skipped": skipped,
            "half_cell_overhang_within_existing_paper": half_cell_edges,
            "scope": "Original text preparation/wrapping and emitted rectangles; GPU/cache calls captured"}


def verify_final_definition_layout(elf_data, dictionary):
    inputs = (elf_data, *(dictionary.files[name] for name in NAMES))
    return mini_validation_cache.checked("final-definition-layout", inputs,
                                         lambda: _verify_final_definition_layout(elf_data, dictionary))


def popup_geometry(elf_data):
    from mini_font_native import MiniFontMachine
    native = MiniFontMachine(elf_data)
    actor = native.SOURCE
    native.uc.mem_write(actor, bytes(0xab0))
    native.uc.mem_write(actor + 0x1000, struct.pack("<I", actor))
    native.uc.reg_write(UC_MIPS_REG_0 + 17, actor)
    native.uc.reg_write(UC_MIPS_REG_A2, actor + 0x1000)
    hook = native.uc.hook_add(UC_HOOK_CODE, lambda uc, *_: uc.reg_write(UC_MIPS_REG_PC, native.STOP),
                             begin=0x126128, end=0x126128)
    try:
        native.call(0x1260e4)
    finally:
        native.uc.hook_del(hook)
    native.call(0x126368, actor, 0, 0)
    return {"title": list(struct.unpack("<4h", native.uc.mem_read(actor + 0x940, 8))),
            "reading": list(struct.unpack("<4h", native.uc.mem_read(actor + 0x948, 8))),
            "definition": list(struct.unpack("<4h", native.uc.mem_read(actor + 0x950, 8))),
            "book": list(struct.unpack("<2I", native.uc.mem_read(actor + 0xa90, 8)))}


def verify_popup_definition_layout(elf_data, dictionary, word_ids):
    word_ids = tuple(word_ids)
    inputs = (elf_data, *(dictionary.files[name] for name in NAMES), mini_validation_cache.packed(word_ids))

    def verify():
        from mini_font_native import MiniFontMachine
        geometry = popup_geometry(elf_data)
        box = geometry["definition"]
        require(box == [20, 49, 160, 32] and geometry["title"] == [28, 15, 144, 16]
                and geometry["reading"] == [20, 31, 288, 16] and geometry["book"] == [0, 0],
                "Word-popup geometry/title/book position changed unexpectedly")
        native, overhang, skipped = MiniFontMachine(elf_data), [], 0
        probe = native.layout(b'A' * 20 + b' B C', box, width=16, height=16, popup=True)
        require(probe['skipped_leading_spaces'] == [20]
                and probe['rectangles'][20][:2] == (box[0], box[1] + 16),
                "Word-popup second-line leading-space hook is missing")
        for word_id in word_ids:
            require(1 <= word_id <= len(dictionary.words), "Unknown word-popup dictionary ID")
            layout = native.layout(dictionary.words[word_id - 1].definition, box, width=16, height=16, popup=True)
            require(layout["complete"] and all(box[0] <= x and x + w <= box[0] + box[2] + 8
                    and box[1] <= y and y + h <= box[1] + box[3] for x, y, w, h in layout["rectangles"]),
                    f"MINI word-popup definition exceeds 160x32px: word ID {word_id}")
            if not layout["fits"]:
                overhang.append(word_id)
            skipped += len(layout["skipped_leading_spaces"])
        return {"definitions": len(word_ids), "geometry": geometry, "font_pixels": 16,
                "all_characters_drawn": True, "half_cell_overhang_within_paper": overhang,
                "second_line_leading_spaces_skipped": skipped,
                "scope": "Actual popup initializer, position setter, draw callback and 16px definition renderer; no GPU"}

    return mini_validation_cache.checked("word-popup-layout", inputs, verify)


def _verify_final_definition_layout(elf_data, dictionary):
    from mini_font_native import MiniFontMachine
    elf = Elf(elf_data)
    at = elf.offset(0x113e70)
    original = 3 << 26 | 0x10c630 >> 2
    hook, delay = struct.unpack_from('<2I', elf_data, at)
    require(hook >> 26 == 3 and hook != original and delay == 0x24040002,
            'Final dictionary leading-space hook is missing')
    reference = bytearray(elf_data)
    struct.pack_into('<I', reference, at, original)
    native, before = MiniFontMachine(elf_data), None
    box = struct.unpack_from('<4h', elf_data, elf.offset(0x214608))
    require(box in ((350, 234, 180, 40), (340, 234, 200, 40)), 'Unexpected final dictionary panel')

    def overflow(rectangles):
        x, y, width, height = box
        return tuple(max([0] + values) for values in (
            [x - a for a, b, c, d in rectangles], [y - b for a, b, c, d in rectangles],
            [a + c - x - width for a, b, c, d in rectangles],
            [b + d - y - height for a, b, c, d in rectangles]))

    skipped, existing_overflows, existing_truncations = 0, [], []
    for word in dictionary.words:
        after = native.layout(word.definition, box, definition=True)
        extent = overflow(after['rectangles'])
        if not after['complete'] or any(a > b for a, b in zip(extent, (0, 0, 10, 0))):
            if before is None:
                before = MiniFontMachine(bytes(reference))
            old = before.layout(word.definition, box, definition=True)
            lost = set(range(after['characters'])) - set(after['draw_positions']) - set(after['skipped_leading_spaces'])
            old_lost = set(range(old['characters'])) - set(old['draw_positions'])
            require(lost <= old_lost, f'Final dictionary lost a previously visible character: {word.word_id}')
            require(all(a <= b for a, b in zip(extent, overflow(old['rectangles']))),
                    f'Final dictionary overflow became worse: {word.word_id}')
            previous = dict(zip(old['draw_positions'], old['rectangles']))
            for position, rectangle in zip(after['draw_positions'], after['rectangles']):
                if position in previous:
                    a, b, c, d = previous[position]
                    require(rectangle[0] <= a and rectangle[1:] == (b, c, d),
                            f'Final dictionary moved an existing glyph incorrectly: {word.word_id}')
            if any(extent):
                existing_overflows.append(word.word_id)
            if not after['complete']:
                existing_truncations.append(word.word_id)
        skipped += len(after['skipped_leading_spaces'])
    return {'definitions': len(dictionary.words), 'panel': list(box),
            'second_line_leading_spaces_skipped': skipped, 'newly_missing_characters': 0,
            'preexisting_overflows': existing_overflows, 'preexisting_truncations': existing_truncations,
            'reference': 'Same final ELF with only the ASCII allocation hook restored',
            'scope': 'All final definitions; no new omission/overflow. Existing layout defects recorded, not edited.'}
