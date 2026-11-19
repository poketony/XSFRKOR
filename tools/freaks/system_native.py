"""Original MIPS table getters and fixed-row system-message formatting."""

import struct

from adv_format import require, text_end, tokens
from font_native import FontMachine
from system_text import COMPONENTS, buffer_shape, catalogue
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_PC, UC_MIPS_REG_RA, UC_MIPS_REG_V0


class SystemMachine(FontMachine):
    def __init__(self, original, data, component, font_data):
        super().__init__(data, font_data)
        self.settings = COMPONENTS[component]
        self.component = component
        self.items = catalogue(original, component)
        self.base, self.rows = buffer_shape(original, component)
        self.captured = []
        self.max_width = 0
        address = self.settings["option_draw"]
        self.option_hook = self.uc.hook_add(UC_HOOK_CODE, self.capture_option, begin=address, end=address)

    def capture_option(self, uc, address, size, _):
        self.captured.append(uc.reg_read(UC_MIPS_REG_A0))
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def pointer(self, item):
        values = {struct.unpack("<I", self.uc.mem_read(address, 4))[0] for address in item.references}
        require(len(values) == 1, "Shared system message aliases disagree")
        return values.pop()

    def tables(self):
        checked = 0
        table, count = self.settings["message"]
        for index in range(count):
            expected = struct.unpack("<I", self.uc.mem_read(table + index * 4, 4))[0]
            self.call(self.settings["getter"], index)
            require(self.uc.reg_read(UC_MIPS_REG_V0) == expected, "Native system-message table lookup differs")
            checked += 1
        table, count = self.settings["option"]
        for index in range(count):
            self.uc.mem_write(self.elf.gp + self.settings["option_gp"], bytes((index,)))
            self.call(self.settings["option_entry"])
            expected = struct.unpack("<I", self.uc.mem_read(table + index * 4, 4))[0]
            require(self.captured[-1] == expected, "Native option description table lookup differs")
            checked += 1
        return checked

    def bios_title(self):
        source = self.settings["bios"]
        at = self.elf.offset(source)
        raw = self.elf.data[at:text_end(self.elf.data, at)]
        output = self.machine.OUTPUT
        self.uc.mem_write(output - 16, b"\xa5" * (len(raw) + 33))
        self.call(self.settings["bios_copy"], output, source)
        require(bytes(self.uc.mem_read(output, len(raw) + 1)) == raw + b"\0",
                "Native memory-card title copy differs")
        require(self.uc.reg_read(UC_MIPS_REG_V0) == 32, "Memory-card title split field differs")
        require(bytes(self.uc.mem_read(output - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(output + len(raw) + 1, 16)) == b"\xa5" * 16,
                "Memory-card title copy crossed output canary")

    def format(self, item):
        return self.format_at(self.pointer(item), item.kind)

    def format_at(self, source, kind, budget=None):
        block = bytes(self.uc.mem_read(source, 2048))
        raw = block[:text_end(block, 0)]
        expected = bytearray()
        for _, token in tokens(raw):
            if token not in (b"\x0b", b"\x0c", b"\x0d") and token[0] != 0x0e:
                expected.extend(token)
        lines = bytes(expected).split(b"\n")
        require(len(lines) <= self.rows, "Expected system paragraph exceeds native buffer")
        region = self.rows * 128 + 24
        self.uc.mem_write(self.base - 16, b"\xa5" * (region + 32))
        output = self.machine.OUTPUT
        self.uc.mem_write(output - 16, b"\xa5" * 40)
        cursor = source
        for _ in range(len(raw) + 2):
            self.call(self.settings["parser"], cursor, output, output + 4, count=2000000)
            destination = self.uc.reg_read(UC_MIPS_REG_V0)
            mode = struct.unpack("<I", self.uc.mem_read(output, 4))[0]
            require(cursor <= destination <= source + len(raw), "Native system parser cursor left the input")
            if mode == 0:
                break
            require(destination > cursor, "Native system parser made no progress")
            cursor = destination
        else:
            raise ValueError("Native system parser did not terminate")
        require(destination == source + len(raw), "Native system parser did not consume the full string")
        for index, line in enumerate(lines):
            actual = bytes(self.uc.mem_read(self.base + index * 128, 128))
            require(actual == line + b"\0" + b"\xa5" * (127 - len(line)), "Native system formatted line differs")
            self.uc.mem_write(self.machine.SOURCE, line + b"\0")
            self.call(0x1017c0, self.machine.SOURCE)
            pixels = self.uc.reg_read(UC_MIPS_REG_V0)
            limit = budget if budget is not None else (500 if kind == "message" else (460 if self.component == "LNCH" else 440))
            require(pixels <= limit, "Native GOTHIC system line exceeds screen width budget")
            self.max_width = max(self.max_width, pixels)
        for index in range(len(lines), self.rows):
            require(bytes(self.uc.mem_read(self.base + index * 128, 128)) == b"\0" + b"\xa5" * 127,
                    "Native system parser touched an unused line")
        require(bytes(self.uc.mem_read(self.base - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(self.base + region, 16)) == b"\xa5" * 16,
                "System message parser crossed buffer canary")
        require(bytes(self.uc.mem_read(output - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(output + 8, 16)) == b"\xa5" * 16,
                "System parser result crossed canary")
        return len(lines)


def verify(original, data, component, font_data):
    machine = SystemMachine(original, data, component, font_data)
    tables = machine.tables()
    machine.bios_title()
    paragraphs = lines = 0
    for item in machine.items:
        if item.kind != "bios":
            lines += machine.format(item)
            paragraphs += 1
    return {"native_table_lookups": tables, "native_paragraphs": paragraphs, "native_lines": lines,
            "row_budget": machine.rows, "line_bytes": 128, "buffer_canaries": "pass", "native_bios_title": "pass",
            "native_width_max": machine.max_width,
            "option_gpu_submission": "captured, not rendered", "pcsx2": "not exercised"}
