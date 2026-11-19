"""Run original DTB table getters, hyperlink lookup and paragraph formatter."""

import struct

from adv_format import require, text_end
from database_text import BODY_WIDTH, CATEGORY_HEADERS, COUNT, original_data, read_entries
from font_native import FontMachine
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A2, UC_MIPS_REG_A3, UC_MIPS_REG_PC,
                               UC_MIPS_REG_RA, UC_MIPS_REG_T1, UC_MIPS_REG_T2, UC_MIPS_REG_V0)


LINES = 0x3ad748
METADATA = 0x3ad108
SELECTION = 0x3aa228
BODY_REGION = 0x129277


def formatted_lines(raw, links, visible):
    result = bytearray()
    cursor = 0
    for start, length, target in links:
        result.extend(raw[cursor:start])
        label = raw[start:start + length]
        result.extend(b"\x03\x01" + label + b"\x03\0" if visible(target) else label)
        cursor = start + length
    result.extend(raw[cursor:])
    return bytes(result).split(b"\n")


class DatabaseMachine(FontMachine):
    def __init__(self, data, font_data):
        super().__init__(data, font_data)
        self.max_width = 0
        self.entries = read_entries(data, measure=self.measure)
        self.original_bodies = [entry.texts[2] for entry in read_entries(original_data())]
        self.visible = lambda target: True
        self.uc.hook_add(UC_HOOK_CODE, self.visibility, begin=0x123610, end=0x123610)

    def measure(self, raw):
        self.uc.mem_write(self.machine.SOURCE, raw + b"\0")
        self.call(0x1017c0, self.machine.SOURCE)
        return self.uc.reg_read(UC_MIPS_REG_V0)

    def body_canvas(self):
        at = self.elf.offset(BODY_REGION)
        expected = b"\x73" + struct.pack("<4H", 240, 24, BODY_WIDTH, 140)
        require(self.elf.data[at:at + 9] == expected, "DB body canvas command differs")
        events = []

        def capture(uc, address, size, _):
            events.append(tuple(uc.reg_read(r) for r in (UC_MIPS_REG_A2, UC_MIPS_REG_A3,
                                                         UC_MIPS_REG_T1, UC_MIPS_REG_T2)))
            uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

        scene = self.machine.OUTPUT
        self.uc.mem_write(scene, struct.pack("<I", BODY_REGION) + bytes(252))
        hook = self.uc.hook_add(UC_HOOK_CODE, capture, begin=0x102368, end=0x102368)
        try:
            self.call(0x113b80, scene)
        finally:
            self.uc.hook_del(hook)
        require(events == [(240, 24, BODY_WIDTH, 140)], "Native DB body canvas geometry differs")
        require(struct.unpack("<I", self.uc.mem_read(scene, 4))[0] == BODY_REGION + 9,
                "Native DB body canvas command length differs")
        return {"width": BODY_WIDTH, "height": 140, "command": f"{BODY_REGION:08X}"}

    def category_headers(self):
        counts = [0] * 4
        buffer, scene = 0x3a9da8, self.machine.OUTPUT
        self.uc.mem_write(self.elf.gp - 0x777c, struct.pack("<I", 1))
        for entry in self.entries:
            category = 3
            for index in range(3):
                self.call(0x123fd8, entry.index, index)
                if self.uc.reg_read(UC_MIPS_REG_V0):
                    category = index
                    break
            self.uc.mem_write(SELECTION, struct.pack("<3I", entry.index, 0, 0))
            self.uc.mem_write(buffer - 16, b"\xa5" * (1024 + 32))
            self.uc.mem_write(scene, bytes(256))
            self.call(0x123ae0, scene)
            address = CATEGORY_HEADERS[category][0]
            at = self.elf.offset(address)
            prefix = self.elf.data[at:text_end(self.elf.data, at)]
            expected = prefix + entry.texts[0] + b"\0"
            require(bytes(self.uc.mem_read(buffer, len(expected))) == expected,
                    f"Native DB category/title differs: {entry.index}")
            require(struct.unpack("<I", self.uc.mem_read(scene + 0xa0, 4))[0] == buffer,
                    "Native DB title scene pointer differs")
            require(bytes(self.uc.mem_read(buffer - 16, 16)) == b"\xa5" * 16
                    and bytes(self.uc.mem_read(buffer + len(expected), 16)) == b"\xa5" * 16,
                    "Native DB category/title crossed its output")
            counts[category] += 1
        return counts

    def visibility(self, uc, address, size, _):
        target = uc.reg_read(UC_MIPS_REG_A0)
        require(0 <= target < COUNT, "Native database link target outside table")
        uc.reg_write(UC_MIPS_REG_V0, int(self.visible(target)))
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def getters(self):
        self.call(0x123f90)
        require(self.uc.reg_read(UC_MIPS_REG_V0) == COUNT, "Native database count differs")
        checked = 0
        for entry in self.entries:
            for column, function in zip((2, 3, 4), (0x124030, 0x124050, 0x124070)):
                self.call(function, entry.index)
                require(self.uc.reg_read(UC_MIPS_REG_V0) == entry.words[column], "Native database text getter differs")
                checked += 1
        return checked

    def hyperlink_lookups(self):
        count = 0
        for entry in self.entries:
            starts = {start: (length, target) for start, length, target in entry.links}
            queries = {0, len(entry.texts[2])}
            for start, length, _ in entry.links:
                queries.update((start, start + 1, start + length))
            for offset in sorted(queries):
                output = self.machine.OUTPUT
                self.uc.mem_write(output - 4, b"\xa5" * 12)
                self.call(0x124090, entry.index, offset, output)
                expected = starts.get(offset)
                require(self.uc.reg_read(UC_MIPS_REG_V0) == (expected[0] if expected else 0),
                        "Native hyperlink byte offset/length differs")
                require(bytes(self.uc.mem_read(output, 4)) == (struct.pack("<I", expected[1]) if expected else b"\xa5" * 4),
                        "Native hyperlink target differs")
                require(bytes(self.uc.mem_read(output - 4, 4)) == b"\xa5" * 4
                        and bytes(self.uc.mem_read(output + 4, 4)) == b"\xa5" * 4, "Hyperlink result crossed canary")
                count += 1
        return count

    def paragraph(self, index, visible):
        self.visible = visible
        entry = self.entries[index]
        expected = formatted_lines(entry.texts[2], entry.links, visible)
        self.uc.mem_write(self.elf.gp - 0x777c, struct.pack("<I", 1))
        self.uc.mem_write(SELECTION, struct.pack("<3I", index, 0, 0))
        self.uc.mem_write(LINES - 16, b"\xa5" * (15 * 128 + 32))
        self.uc.mem_write(METADATA, b"\xa5" * (16 * 16))
        self.call(0x123280, count=2000000)
        actual_count = struct.unpack("<I", self.uc.mem_read(self.elf.gp - 0x776c, 4))[0]
        link_count = struct.unpack("<I", self.uc.mem_read(self.elf.gp - 0x7770, 4))[0]
        require(actual_count == len(expected), "Native paragraph line count differs")
        active = [(start, target) for start, _, target in entry.links if visible(target)]
        require(link_count == len(active), "Native paragraph highlighted-link count differs")
        for number, line in enumerate(expected):
            actual = bytes(self.uc.mem_read(LINES + number * 128, 128))
            require(actual == line + b"\0" + b"\xa5" * (127 - len(line)),
                    f"Native database paragraph bytes differ: {index}/{number}")
            pixels = self.measure(line)
            # Preserve the one original 620px line, but never allow new text beyond the actual canvas.
            budget = 620 if entry.texts[2] == self.original_bodies[index] else BODY_WIDTH
            require(pixels <= budget, f"Native DB body line exceeds {budget}px: {index}/{number}")
            self.max_width = max(self.max_width, pixels)
        for number, (start, target) in enumerate(active):
            line, _, _, result_target = struct.unpack("<4I", self.uc.mem_read(METADATA + number * 16, 16))
            require(line == entry.texts[2][:start].count(b"\n") and result_target == target,
                    "Native paragraph selection metadata differs")
        require(bytes(self.uc.mem_read(LINES - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(LINES + len(expected) * 128, (15 - len(expected)) * 128 + 16))
                == b"\xa5" * ((15 - len(expected)) * 128 + 16), "Native paragraph crossed line buffer canary")
        return len(expected)


def verify(data, font_data, paragraphs=True):
    machine = DatabaseMachine(data, font_data)
    canvas = machine.body_canvas()
    getters = machine.getters()
    headers = machine.category_headers()
    lookups = machine.hyperlink_lookups()
    lines = 0
    if paragraphs:
        for index in range(COUNT):
            for visible in (lambda target: True, lambda target: False):
                lines += machine.paragraph(index, visible)
    return {"native_getters": getters, "native_category_headers": headers, "native_link_queries": lookups,
            "native_paragraphs": COUNT * 2 if paragraphs else 0, "native_lines": lines,
            "max_body_width": machine.max_width, "body_canvas": canvas,
            "paragraph_measurement": "original GOTHIC width function", "io_and_gpu": "not exercised"}
