"""Trace EXP's real text parser, atlas selection and glyph advance, without GS I/O."""

import json
import struct


from build_paths import DATA, REPO
from unpack_disc import file_hash, require
from verify_codecs import Native
from unicorn import UC_HOOK_CODE
from unicorn import mips_const as mips


SCRATCH, FONT, TEXT = 0x70000000, 0x1000000, 0x1200000
GLOBAL = 0x8c4100


class FontMachine:
    def __init__(self, font_data):
        inventory = json.loads((DATA / "disc-inventory.json").read_text(encoding="utf-8"))
        record = next(row for row in inventory["files"] if row["path"] == "EXP/EXP.ELF")
        source = REPO / "unpacked/EXP/EXP.ELF"
        require(file_hash(source) == record["sha256"], "Original EXP font executable differs")
        require(len(font_data) == 492096, "EXP font size differs")
        self.machine = Native(source.read_bytes())
        self.uc = self.machine.uc
        self.uc.mem_map(SCRATCH, 0x4000)
        self.uc.mem_write(FONT, font_data)
        self.uc.mem_write(GLOBAL, struct.pack("<I", FONT) + b"\x09\0\0\0\x1e\0\0\0\x04\x03\0\0")
        self.font_data = font_data
        self.saved = {getattr(mips, "UC_MIPS_REG_" + name): 0x400000 + index * 16
                      for index, name in enumerate(("S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "FP"))}
        self.saved[mips.UC_MIPS_REG_GP] = self.machine.elf.gp
        self.uc.hook_add(UC_HOOK_CODE, self.instruction)
        self.glyphs = []

    def reg(self, name):
        return self.uc.reg_read(getattr(mips, "UC_MIPS_REG_" + name))

    def instruction(self, uc, address, size, unused):
        if address == 0x12bd60:
            uc.reg_write(mips.UC_MIPS_REG_PC, self.reg("RA"))
            return
        word = struct.unpack("<I", uc.mem_read(address, 4))[0]
        opcode, rd, function = word >> 26, word >> 11 & 31, word & 63
        if opcode in (0, 0x1c) and function in (0x18, 0x19) and rd:
            # R5900 MULT writes rd too; MULT1's second accumulator is not read here.
            rs, rt = word >> 21 & 31, word >> 16 & 31
            a, b = (uc.reg_read(mips.UC_MIPS_REG_0 + r) & 0xffffffff for r in (rs, rt))
            if function == 0x18:
                a, b = (v - 0x100000000 if v & 0x80000000 else v for v in (a, b))
            value = a * b
            low = value & 0xffffffff
            uc.reg_write(mips.UC_MIPS_REG_0 + rd, low | (0xffffffff00000000 if low & 0x80000000 else 0))
            if opcode == 0:
                uc.reg_write(mips.UC_MIPS_REG_LO, low)
                uc.reg_write(mips.UC_MIPS_REG_HI, value >> 32 & 0xffffffff)
            uc.reg_write(mips.UC_MIPS_REG_PC, address + 4)
        elif address == 0x1971d0:
            pointer = struct.unpack("<I", uc.mem_read(self.machine.STACK - 0x90 + 0x30, 4))[0]
            x, y = struct.unpack("<hh", uc.mem_read(SCRATCH + 8, 4))
            self.glyphs.append({"source_end": pointer - TEXT, "x": x, "y": y,
                                "atlas_x": self.reg("A0") // 16, "atlas_y": self.reg("A1") // 16,
                                "width": self.reg("A2") >> 8, "height": self.reg("A2") & 255,
                                "flags": self.reg("A3"), "sheet": self.reg("A3") & 3})
        elif address == 0x19732c:
            self.glyphs[-1]["next_x"] = struct.unpack("<h", uc.mem_read(SCRATCH + 8, 2))[0]
            # X advance has run; omit only the subsequent GS packet assembly.
            uc.reg_write(mips.UC_MIPS_REG_PC, 0x1978fc)

    def render(self, text):
        require(len(text) < 65536 and b"\0" not in text, "EXP font probe text is too large or contains NUL")
        self.glyphs = []
        self.uc.mem_write(SCRATCH, bytes(0x100))
        self.uc.mem_write(SCRATCH + 0x18, b"\x06")
        self.uc.mem_write(SCRATCH + 0x2c, struct.pack("<hh", 16, 16))
        self.uc.mem_write(TEXT - 16, b"\xa5" * (len(text) + 34))
        self.uc.mem_write(TEXT, text + b"\0\0")
        for register, value in self.saved.items():
            self.uc.reg_write(register, value)
        for name, value in (("A0", SCRATCH), ("A1", TEXT), ("SP", self.machine.STACK), ("RA", self.machine.STOP)):
            self.uc.reg_write(getattr(mips, "UC_MIPS_REG_" + name), value)
        self.uc.emu_start(0x197ba0, self.machine.STOP, count=5000000)
        require(self.reg("PC") == self.machine.STOP and self.reg("SP") == self.machine.STACK,
                "EXP native text parser did not return")
        require(all(self.uc.reg_read(register) == value for register, value in self.saved.items()),
                "EXP native text parser changed a saved register")
        require(bytes(self.uc.mem_read(TEXT, len(text) + 2)) == text + b"\0\0"
                and bytes(self.uc.mem_read(TEXT - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(TEXT + len(text) + 2, 16)) == b"\xa5" * 16,
                "EXP native text parser modified its source")
        require(bytes(self.uc.mem_read(FONT, len(self.font_data))) == self.font_data,
                "EXP native text parser modified its font")
        return self.glyphs
