"""Original GOTHIC pointer fixup, glyph lookup and packed-pixel blitter probes."""

import struct

from adv_format import require
from elf_probe import signed16
from gothic import Gothic, DECODED_LIMIT
from verify_codecs import Native
from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2,
                               UC_MIPS_REG_A3, UC_MIPS_REG_T0, UC_MIPS_REG_S0,
                               UC_MIPS_REG_S1, UC_MIPS_REG_GP, UC_MIPS_REG_SP,
                               UC_MIPS_REG_RA, UC_MIPS_REG_PC)


STATE = 0x1400000
CANVAS = STATE + 0x100


class FontMachine:
    def __init__(self, elf_data, font_data):
        self.machine = Native(elf_data)
        self.uc = self.machine.uc
        self.elf = self.machine.elf
        self.font = Gothic(font_data)

        def word(address):
            return struct.unpack_from("<I", elf_data, self.elf.offset(address))[0]

        upper = (word(0x10191c) & 0xffff) << 16
        self.base = upper + signed16(word(0x101924))
        require(word(0x101920) == 0x3c070020, "Original 2MiB font buffer argument differs")
        require(self.elf.refs(self.base + DECODED_LIMIT), "Next BSS allocation boundary not found")
        self.uc.mem_write(self.base - 16, b"\xa5" * 16)
        self.uc.mem_write(self.base, font_data)
        self.uc.mem_write(self.base + len(font_data), b"\xa5" * 16)
        self.uc.reg_write(UC_MIPS_REG_GP, self.elf.gp)
        self.uc.reg_write(UC_MIPS_REG_S0, self.base)
        self.uc.reg_write(UC_MIPS_REG_S1, upper)
        self.uc.emu_start(0x101938, 0x101a00, count=10000)
        require(self.uc.reg_read(UC_MIPS_REG_PC) == 0x101a00, "Font pointer fixup did not finish")
        for face in self.font.faces:
            actual = bytes(self.uc.mem_read(self.base + face.descriptor + 4, 16))
            expected = struct.pack("<4I", self.base + face.metrics_offset, face.count,
                                   self.base + face.codes_offset, self.base + face.pixels_offset)
            require(actual == expected, "Native font pointer fixup differs")
        require(bytes(self.uc.mem_read(self.base - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(self.base + len(font_data), 16)) == b"\xa5" * 16,
                "Font pointer fixup crossed buffer boundary")

    def call(self, entry, *args, count=200000):
        self.uc.reg_write(UC_MIPS_REG_GP, self.elf.gp)
        self.uc.reg_write(UC_MIPS_REG_SP, self.machine.STACK)
        self.uc.reg_write(UC_MIPS_REG_RA, self.machine.STOP)
        for register, value in zip((UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3), args):
            self.uc.reg_write(register, value)
        self.uc.emu_start(entry, self.machine.STOP, count=count)
        require(self.uc.reg_read(UC_MIPS_REG_PC) == self.machine.STOP, "Native font function did not return")
        require(self.uc.reg_read(UC_MIPS_REG_SP) == self.machine.STACK, "Native font stack differs")

    def lookup(self, code, style=0):
        require(len(code) in (1, 2) and style in (0, 1), "Bad glyph lookup argument")
        self.uc.mem_write(self.machine.SOURCE, code + b"\0")
        state = bytearray(64)
        struct.pack_into("<5I", state, 0, 1, style, 0, 0, self.machine.SOURCE)
        self.uc.mem_write(STATE, bytes(state))
        self.call(0x101ad8, STATE)
        self.call(0x101b90, STATE)
        full, mapped, index, left, advance = struct.unpack("<5I", self.uc.mem_read(STATE + 0x18, 20))
        face = style * 2 + full
        require(index < self.font.faces[face].count, "Native glyph was not found")
        return {"face": face, "code": mapped, "index": index, "left": left, "advance": advance}

    def pixels(self, face, index):
        f = self.font.faces[face]
        require(0 <= index < f.count, "Native glyph index outside font")
        count = f.width * f.height
        destination = self.machine.OUTPUT
        self.uc.mem_write(destination - 16, b"\xa5" * (count + 32))
        self.uc.mem_write(CANVAS, struct.pack("<4I", f.width, f.height, f.width, f.height))
        self.uc.reg_write(UC_MIPS_REG_T0, 0)
        self.call(0x102030, CANVAS, destination, self.base + f.pixels_offset + index * count // 2, f.width)
        require(bytes(self.uc.mem_read(destination - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(destination + count, 16)) == b"\xa5" * 16,
                "Native glyph blitter crossed output boundary")
        return bytes(self.uc.mem_read(destination, count))
