"""Original MINI EUC index, text preparation and glyph UV/plane selection."""

import struct

from unpack_disc import require
from verify_codecs import Native
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import (UC_MIPS_REG_0, UC_MIPS_REG_A0, UC_MIPS_REG_A1,
                               UC_MIPS_REG_A2, UC_MIPS_REG_A3, UC_MIPS_REG_GP,
                               UC_MIPS_REG_SP, UC_MIPS_REG_RA, UC_MIPS_REG_PC,
                               UC_MIPS_REG_V0)


STATE = 0x1400000


class MiniFontMachine(Native):
    def __init__(self, elf_data):
        super().__init__(elf_data)
        for address, expected in ((0x113b18, 0x02901818), (0x113dbc, 0x00561018),
                                  (0x113dc0, 0x70761818), (0x113e28, 0x00562018),
                                  (0x113e3c, 0x00562018), (0x113ef4, 0x00d63018),
                                  (0x113ef8, 0x00561018), (0x113f54, 0x00562018),
                                  (0x113f70, 0x00961018)):
            actual = struct.unpack_from("<I", elf_data, self.elf.offset(address))[0]
            require(actual == expected, "MINI text multiply instruction differs")
            self.uc.hook_add(UC_HOOK_CODE, self.multiply, begin=address, end=address)
        self.uc.hook_add(UC_HOOK_CODE, self.stop_glyph, begin=0x18a590, end=0x18a590)
        self.layout_active = False
        self.layout_trace = self.definition_active = False
        self.uc.hook_add(UC_HOOK_CODE, self.layout_step, begin=0x113cd8, end=0x113cd8)
        self.uc.hook_add(UC_HOOK_CODE, self.definition_return, begin=0x1165f8, end=0x1165f8)
        self.uc.hook_add(UC_HOOK_CODE, self.popup_background, begin=0x10b1e8, end=0x10b1e8)
        for address in (0x10c630, 0x10c680):
            self.uc.hook_add(UC_HOOK_CODE, self.primitive, begin=address, end=address)

    def multiply(self, uc, address, size, user):
        word = struct.unpack_from("<I", self.elf.data, self.elf.offset(address))[0]
        left, right = ((uc.reg_read(UC_MIPS_REG_0 + r) & 0xffffffff)
                       for r in (word >> 21 & 31, word >> 16 & 31))
        signed = lambda v: v if v < 0x80000000 else v - 0x100000000
        low = signed(left) * signed(right) & 0xffffffff
        value = low if low < 0x80000000 else low | 0xffffffff00000000
        # These fragments consume the R5900-specific destination register only.
        uc.reg_write(UC_MIPS_REG_0 + (word >> 11 & 31), value)
        uc.reg_write(UC_MIPS_REG_PC, address + 4)

    def stop_glyph(self, uc, address, size, user):
        if self.layout_active:
            pointer = uc.reg_read(UC_MIPS_REG_A1)
            require(pointer == self.OUTPUT, "Unexpected MINI primitive pointer")
            require(bytes(uc.mem_read(pointer - 16, 16)) == b"\xa5" * 16
                    and bytes(uc.mem_read(pointer + 64, 16)) == b"\xa5" * 16,
                    "MINI draw crossed primitive boundary")
            raw = bytes(uc.mem_read(pointer, 64))
            self.rectangles.append(struct.unpack_from("<4i", raw, 0x1c)[:2]
                                   + struct.unpack_from("<2H", raw, 0x24))
            if self.layout_trace:
                pointer = uc.reg_read(UC_MIPS_REG_0 + 20)
                self.draw_positions.append((pointer - STATE - 0x104) // 2)
            uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))
            return
        self.glyph_reached = True
        uc.emu_stop()

    def layout_step(self, uc, address, size, user):
        if self.layout_trace:
            position = (uc.reg_read(UC_MIPS_REG_0 + 20) - STATE - 0x104) // 2
            self.layout_cursors[position] = tuple(uc.reg_read(UC_MIPS_REG_0 + r) for r in (19, 21))

    def definition_return(self, uc, address, size, user):
        if self.definition_active:
            uc.reg_write(UC_MIPS_REG_PC, self.STOP)

    def popup_background(self, uc, address, size, user):
        if self.layout_active:
            uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def primitive(self, uc, address, size, user):
        require(self.layout_active and uc.reg_read(UC_MIPS_REG_A0) == 2,
                "Unexpected MINI renderer cache call")
        if address == 0x10c630:
            uc.mem_write(self.OUTPUT - 16, b"\xa5" * 96)
            uc.reg_write(UC_MIPS_REG_V0, self.OUTPUT)
        else:
            uc.reg_write(UC_MIPS_REG_V0, 0)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def call(self, address, *args):
        self.uc.reg_write(UC_MIPS_REG_GP, self.elf.gp)
        self.uc.reg_write(UC_MIPS_REG_SP, self.STACK)
        self.uc.reg_write(UC_MIPS_REG_RA, self.STOP)
        for register, value in zip((UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3), args):
            self.uc.reg_write(register, value)
        self.uc.emu_start(address, self.STOP, count=100000)
        require(self.uc.reg_read(UC_MIPS_REG_PC) == self.STOP, "Native MINI font function did not return")
        require(self.uc.reg_read(UC_MIPS_REG_SP) == self.STACK, "Native MINI font stack changed")
        return self.uc.reg_read(UC_MIPS_REG_V0) & 0xffffffff

    def index(self, code):
        self.uc.mem_write(self.SOURCE, code + b"\0\0")
        value = self.call(0x114328, self.SOURCE)
        return -1 if value == 0xffffffff else value

    def prepare(self, raw, width=20, height=20):
        require(b"\0" not in raw, "MINI text must not contain NUL")
        # Select strlen's byte path instead of the unsupported R5900 SIMD path.
        source = self.SOURCE + 1
        self.uc.mem_write(source, raw + b"\0")
        self.uc.mem_write(STATE - 16, b"\xa5" * (0x314 + 32))
        result = self.call(0x113ad0, source, STATE, width, height)
        require(result == STATE, "Native MINI text rejected length")
        count, half_width, actual_width, actual_height = struct.unpack("<4I", self.uc.mem_read(STATE + 0x304, 16))
        indices = struct.unpack(f"<{count}H", self.uc.mem_read(STATE + 0x104, count * 2))
        require(bytes(self.uc.mem_read(STATE, len(raw))) == raw, "Native MINI text bytes differ")
        require(bytes(self.uc.mem_read(STATE - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(STATE + 0x314, 16)) == b"\xa5" * 16,
                "Native MINI text crossed state boundary")
        require(half_width == len(raw) * width // 2 and (actual_width, actual_height) == (width, height),
                "Native MINI text dimensions differ")
        return indices

    def glyph_layout(self, index):
        require(0 <= index < 7520, "MINI glyph index outside full-width font")
        self.uc.mem_write(STATE, b"\0" * 0x314)
        self.uc.mem_write(STATE + 0x104, struct.pack("<H", index))
        self.uc.mem_write(STATE + 0x30c, struct.pack("<2I", 16, 16))
        self.uc.mem_write(self.OUTPUT - 16, b"\xa5" * 96)
        stack = self.STACK - 256
        self.uc.mem_write(stack, b"\0" * 64)
        self.uc.mem_write(stack, struct.pack("<f", 1.0))
        self.uc.mem_write(stack + 8, struct.pack("<Q", 2))
        self.uc.mem_write(stack + 0x20, struct.pack("<Q", 1 << 37))
        registers = {16: self.OUTPUT, 17: STATE, 19: 0, 20: STATE + 0x104,
                     21: 0, 22: 1, 30: 16}
        for register, value in registers.items():
            self.uc.reg_write(UC_MIPS_REG_0 + register, value)
        self.uc.reg_write(UC_MIPS_REG_GP, self.elf.gp)
        self.uc.reg_write(UC_MIPS_REG_SP, stack)
        self.glyph_reached = False
        self.uc.emu_start(0x113cfc, self.STOP, count=1000)
        require(self.glyph_reached, "Native MINI glyph fragment did not reach draw call")
        raw = bytes(self.uc.mem_read(self.OUTPUT, 64))
        uv = struct.unpack_from("<4H", raw, 0x28)
        descriptor = struct.unpack_from("<Q", raw, 0x30)[0]
        page, local = divmod(index, 512)
        expected_uv = ((local // 2 % 16) * 16, (local // 2 // 16) * 16, 16, 16)
        require(uv == expected_uv and (descriptor >> 37 & 0x3fff) == 1 + (index & 1),
                "Native MINI glyph UV/plane selection differs")
        require(descriptor & 0x3fff == (8 + page * 4) * 32 + 0x1a40,
                "Native MINI font page address differs")
        require(bytes(self.uc.mem_read(self.OUTPUT - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(self.OUTPUT + 64, 16)) == b"\xa5" * 16,
                "Native MINI primitive crossed boundary")
        return {"index": index, "page": page, "uv": uv, "plane": index & 1}

    def layout(self, raw, box=(350, 234, 180, 40), width=20, height=20, *, definition=False, popup=False):
        require(not (definition and popup), "Select only one dictionary caller")
        indices = self.prepare(raw, width, height)
        require(all(i < 7520 + 95 for i in indices), "MINI layout has an unsupported glyph")
        before = bytes(self.uc.mem_read(STATE, 0x314))
        box_address = STATE + 4096
        self.uc.mem_write(box_address - 16, b"\xa5" * 40)
        self.uc.mem_write(box_address, struct.pack("<4h", *box))
        self.rectangles = []
        self.draw_positions, self.layout_cursors = [], {}
        self.uc.reg_write(UC_MIPS_REG_0 + 8, 1)
        self.layout_active = True
        self.layout_trace = True
        self.definition_active = definition
        try:
            if popup:
                actor, callback = STATE - 0x62c, box_address + 0x100
                self.uc.mem_write(actor, bytes(0x62c))
                self.uc.mem_write(actor + 0x940, bytes(0x1c))
                self.uc.mem_write(actor + 0x950, struct.pack("<4hI", *box, 10))
                self.uc.mem_write(actor + 0x96c, struct.pack("<I", 1))
                self.uc.mem_write(actor + 0xaa4, struct.pack("<I", 1))
                self.uc.mem_write(callback, bytes(0x40))
                self.uc.mem_write(callback + 0x34, struct.pack("<2I", actor, 1))
                self.call(0x126668, callback)
            else:
                self.call(0x1165f0 if definition else 0x113bf0, STATE, box_address, 0, 10)
        finally:
            self.layout_active = False
            self.layout_trace = self.definition_active = False
        require(bytes(self.uc.mem_read(STATE, 0x314)) == before, "MINI draw changed prepared text")
        require(bytes(self.uc.mem_read(box_address - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(box_address + 8, 16)) == b"\xa5" * 16,
                "MINI draw crossed box boundary")
        x, y, w, h = box
        contained = all(x <= left and y <= top and left + width <= x + w and top + height <= y + h
                        for left, top, width, height in self.rectangles)
        missing = set(range(len(indices))) - set(self.draw_positions)
        skipped = sorted(p for p in missing if (definition or popup) and indices[p] == 0x1d60
                         and self.layout_cursors.get(p) == (x, y + height))
        complete = len(self.rectangles) + len(skipped) == len(indices) and missing == set(skipped)
        return {"characters": len(indices), "drawn": len(self.rectangles),
                "complete": complete, "fits": complete and contained, "rectangles": self.rectangles,
                "skipped_leading_spaces": skipped, "draw_positions": self.draw_positions}

    def centered(self, raw, x=320, y=271, width=16, height=16):
        indices = self.prepare(raw, width, height)
        require(all(i < 7520 + 95 for i in indices), "MINI centered text has an unsupported glyph")
        before = bytes(self.uc.mem_read(STATE, 0x314))
        self.rectangles = []
        self.uc.reg_write(UC_MIPS_REG_0 + 8, 17)
        self.layout_active = True
        try:
            self.call(0x113fe8, STATE, x, y, 0)
        finally:
            self.layout_active = False
        require(bytes(self.uc.mem_read(STATE, 0x314)) == before, "MINI centered draw changed prepared text")
        require(len(self.rectangles) == len(indices), "MINI centered draw omitted a glyph")
        return {"characters": len(indices), "drawn": len(self.rectangles), "rectangles": self.rectangles}
