"""Resolve audited MINI UI sources with original code, then check emitted layout."""

import struct

from mini_font_native import MiniFontMachine, STATE
from unpack_disc import require
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import UC_MIPS_REG_0, UC_MIPS_REG_GP, UC_MIPS_REG_SP, UC_MIPS_REG_PC


class TextMachine(MiniFontMachine):
    CONTROL = 0x1600000
    HANDLE = 0x1601000

    def __init__(self, elf_data):
        super().__init__(elf_data)
        self.resolving = False
        self.uc.hook_add(UC_HOOK_CODE, self.stop_source, begin=0x113ad0, end=0x113ad0)

    def stop_source(self, uc, address, size, user):
        if self.resolving:
            uc.emu_stop()

    def source(self, kind, row=0):
        self.uc.mem_write(self.HANDLE, struct.pack("<I", STATE))
        if kind == "diagnostic":
            group, line = divmod(row, 5)
            self.uc.mem_write(self.CONTROL + 0x70, bytes((group,)))
            entry, registers = 0x13d1a4, {16: line, 17: self.CONTROL}
        elif kind == "no_words":
            entry, registers = 0x1162dc, {2: self.HANDLE}
        else:
            require(kind == "random_stage", "Unknown native MINI text kind")
            self.uc.mem_write(self.CONTROL + 0xd4, struct.pack("<I", self.HANDLE))
            entry, registers = 0x167bdc, {16: self.CONTROL}
        for register, value in registers.items():
            self.uc.reg_write(UC_MIPS_REG_0 + register, value)
        self.uc.reg_write(UC_MIPS_REG_GP, self.elf.gp)
        self.uc.reg_write(UC_MIPS_REG_SP, self.STACK)
        self.resolving = True
        try:
            self.uc.emu_start(entry, self.STOP, count=1000)
        finally:
            self.resolving = False
        require(self.uc.reg_read(UC_MIPS_REG_PC) == 0x113ad0, "MINI source path did not reach text preparation")
        pointer, target, width, height = (self.uc.reg_read(UC_MIPS_REG_0 + r) & 0xffffffff for r in (4, 5, 6, 7))
        require(target == (0x24d708 if kind == "diagnostic" else STATE),
                "MINI source path selected an unexpected text state")
        raw = bytes(self.uc.mem_read(pointer, 256)).split(b"\0", 1)[0]
        require(len(raw) < 256, "MINI UI source is not terminated")
        return pointer, raw, width, height


def verify(elf_data, items, positions, replacements):
    native = TextMachine(elf_data)
    layouts = []
    for item in items:
        pointer, raw, width, height = native.source(item.kind, item.row)
        require(pointer == positions[item.address] and raw == replacements.get(item.address, item.raw),
                "Native MINI UI source differs from the translated allocation")
        require(width == height == (20 if item.kind == "no_words" else 16), "Native MINI UI glyph size differs")
        if item.kind == "random_stage":
            layout = native.centered(raw, width=width, height=height)
            left = 320 - len(item.raw) * 4
            require(all(left <= x and x + w <= 640 - left and y == 263 and h == 16
                        for x, y, w, h in layout["rectangles"]),
                    "MINI random-stage text exceeds its original horizontal envelope")
        else:
            box = (0, 0, 256, 16) if item.kind == "diagnostic" else (347, 216, 200, 40)
            layout = native.layout(raw, box, width, height)
            require(layout["fits"], f"MINI UI text does not fit: {item.address:08X}")
        layouts.append({"address": f"{item.address:08X}", "characters": layout["characters"],
                        "drawn": layout["drawn"]})
    for row in range(5):
        pointer, raw, width, height = native.source("diagnostic", row)
        require(pointer == 0x217260 and not raw and width == height == 16,
                "MINI empty default diagnostic changed")
    return {"texts": len(items), "empty_defaults": 5, "original_source_paths": "pass",
            "original_preparation_and_layout": "pass", "layouts": layouts,
            "scope": "Original source-address fragments, formatter and draw rectangles; cache/GPU calls captured"}
