"""Read EXP SBs with the original overlay's fetch, length and address helpers."""

import hashlib
import struct


from build_paths import DATA, REPO
from unpack_disc import require
from verify_codecs import Native
from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_GP,
                               UC_MIPS_REG_SP, UC_MIPS_REG_RA, UC_MIPS_REG_PC,
                               UC_MIPS_REG_V0, UC_MIPS_REG_S0, UC_MIPS_REG_S1,
                               UC_MIPS_REG_S2, UC_MIPS_REG_S3, UC_MIPS_REG_S7)


OVL_SHA256 = "29f9dc02cff813e5adb1b71ab61154fb0438d6adb9ceaa5bd89f3b2932d132a1"
STATE, DRIVER = 0x1400000, 0x1401000
FETCH, OPERAND, LENGTH, DATA, RESOLVE = 0xa9cda8, 0xa9cdc8, 0xa9cf38, 0xa9ce00, 0xa9e6a0


def immediate(opcode, rs, rt, value):
    return opcode << 26 | rs << 21 | rt << 16 | value & 0xffff


def addu(rd, rs, rt):
    return rs << 21 | rt << 16 | rd << 11 | 0x21


def call(target):
    return 3 << 26 | target >> 2


def scanner():
    # The driver only loops over original helpers. No SB opcode handler is run.
    words = [addu(4, 16, 0), addu(5, 17, 0), call(FETCH), 0,
             immediate(0x2b, 19, 2, 0), addu(20, 2, 0),
             addu(4, 16, 0), immediate(9, 17, 5, 1), call(OPERAND), 0,
             immediate(0x2b, 19, 2, 4), addu(5, 2, 0), addu(4, 20, 0), call(LENGTH), 0,
             immediate(0x2b, 19, 17, 8), immediate(0x2b, 19, 2, 12),
             addu(17, 17, 2), immediate(9, 19, 19, 16),
             17 << 21 | 18 << 16 | 8 << 11 | 0x2b]
    words.extend((immediate(5, 8, 0, -len(words) - 1), 0, addu(2, 19, 0), 23 << 21 | 8, 0))
    return struct.pack(f"<{len(words)}I", *words)


class SbMachine:
    def __init__(self):
        overlay = (REPO / "unpacked/EXP/OV01.OVL").read_bytes()
        require(hashlib.sha256(overlay).hexdigest() == OVL_SHA256, "Original EXP SB overlay differs")
        self.machine = Native((REPO / "unpacked/EXP/EXP.ELF").read_bytes())
        self.uc = self.machine.uc
        self.uc.mem_write(0xa80000, overlay[0x1000:0x6bc78])
        self.uc.mem_write(DRIVER, scanner())
        # bat.msgPut converts its third argument through the string helper.
        require(struct.unpack_from("<I", overlay, 0xa9f378 - 0xa7f000)[0] == call(RESOLVE),
                "Original EXP bat.msgPut string conversion differs")

    def load(self, data):
        require(len(data) < 4 << 20, "EXP native SB probe buffer exceeded")
        self.data = data
        self.uc.mem_write(self.machine.SOURCE - 16, b"\xa5" * (len(data) + 32))
        self.uc.mem_write(self.machine.SOURCE, data)
        self.uc.mem_write(STATE, bytes(64))
        self.uc.mem_write(STATE + 0x14, struct.pack("<I", self.machine.SOURCE))

    def invoke(self, entry, a0, a1=0):
        for register, value in ((UC_MIPS_REG_GP, self.machine.elf.gp), (UC_MIPS_REG_SP, self.machine.STACK),
                                (UC_MIPS_REG_RA, self.machine.STOP), (UC_MIPS_REG_A0, a0), (UC_MIPS_REG_A1, a1)):
            self.uc.reg_write(register, value)
        self.uc.emu_start(entry, self.machine.STOP, count=100000)
        require(self.uc.reg_read(UC_MIPS_REG_PC) == self.machine.STOP
                and self.uc.reg_read(UC_MIPS_REG_SP) == self.machine.STACK, "EXP SB helper did not return")
        return self.uc.reg_read(UC_MIPS_REG_V0)

    def scan(self, data):
        self.load(data)
        code, end = struct.unpack_from("<II", data, 0x14)
        limit = (end - code) // 2
        require(limit > 0 and limit * 16 < 4 << 20, "EXP native SB instruction buffer exceeded")
        output = self.machine.OUTPUT
        self.uc.mem_write(output - 16, b"\xa5" * (limit * 16 + 32))
        for register, value in ((UC_MIPS_REG_GP, self.machine.elf.gp), (UC_MIPS_REG_SP, self.machine.STACK),
                                (UC_MIPS_REG_S0, STATE), (UC_MIPS_REG_S1, 0), (UC_MIPS_REG_S2, limit),
                                (UC_MIPS_REG_S3, output), (UC_MIPS_REG_S7, self.machine.STOP)):
            self.uc.reg_write(register, value)
        self.uc.emu_start(DRIVER, self.machine.STOP, count=limit * 100, timeout=60_000_000)
        require(self.uc.reg_read(UC_MIPS_REG_PC) == self.machine.STOP
                and self.uc.reg_read(UC_MIPS_REG_SP) == self.machine.STACK, "EXP native SB scan did not return")
        size = self.uc.reg_read(UC_MIPS_REG_V0) - output
        require(0 < size <= limit * 16 and size % 16 == 0
                and self.uc.reg_read(UC_MIPS_REG_S1) == limit, "EXP native SB scan crossed code end")
        require(bytes(self.uc.mem_read(output - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(output + size, limit * 16 - size + 16)) == b"\xa5" * (limit * 16 - size + 16),
                "EXP native SB scanner crossed result boundary")
        self.check_source()
        return tuple(struct.iter_unpack("<4I", self.uc.mem_read(output, size)))

    def string(self, site, *, data_reference=False):
        code, base = struct.unpack_from("<II", self.data, 0x14)
        if data_reference:
            value = self.invoke(DATA, STATE, (site - base) // 4)
        else:
            value = self.invoke(OPERAND, STATE, (site - code) // 2)
        pointer = self.invoke(RESOLVE, STATE, value)
        at = pointer - self.machine.SOURCE
        require(0 <= at < len(self.data), "EXP native string address outside SB")
        end = self.data.find(b"\0", at)
        require(end >= 0, "EXP native string has no terminator")
        return at, bytes(self.uc.mem_read(pointer, end - at))

    def check_source(self):
        require(bytes(self.uc.mem_read(self.machine.SOURCE, len(self.data))) == self.data
                and bytes(self.uc.mem_read(self.machine.SOURCE - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(self.machine.SOURCE + len(self.data), 16)) == b"\xa5" * 16,
                "EXP native SB probe modified its input")
