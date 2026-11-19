"""Use EXP OV01's real typed name/description helpers on the rebuilt battle data."""

import struct

import exp_batdat as batdat
from exp_sb_native import OVL_SHA256
from exp_txd import original_elf

from build_paths import REPO
from unpack_disc import require
from verify_codecs import Native
from unicorn import mips_const as mips


GETTERS = {6: 0xa9acc8, 8: 0xa9ad50, 7: 0xa9adc8, 9: 0xa9ae50, 10: 0xa9aec8,
           11: 0xa9af40, 12: 0xa9afb8, 13: 0xa9b030, 14: 0xa9b0a8, 18: 0xa9b120}
BASE = 0x617680


class BattleMachine:
    def __init__(self, data):
        self.data = data
        self.table = batdat.BattleData(data)
        overlay = (REPO / 'unpacked/EXP/OV01.OVL').read_bytes()
        require(batdat.sb.checksum(overlay) == OVL_SHA256, 'Original EXP battle overlay differs')
        self.machine = Native(original_elf())
        self.uc = self.machine.uc
        self.uc.mem_write(0xa80000, overlay[0x1000:0x6bc78])
        self.uc.mem_write(BASE - 16, b'\xa5' * (len(data) + 32))
        self.uc.mem_write(BASE, data)

    def verify(self):
        saved = {getattr(mips, 'UC_MIPS_REG_' + name): 0x480000 + index * 16
                 for index, name in enumerate(('S0', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'FP'))}
        saved[mips.UC_MIPS_REG_GP] = self.machine.elf.gp
        count = 0
        for entry in self.table.entries:
            if entry.section in (0, 1):
                function, identity, selector = 0xa9a8b0, entry.record + (1 if entry.section == 0 else 16), 0
            else:
                function, identity = GETTERS[entry.section], entry.record + 1
                selector = 1 if entry.field in ('desc', 'text') else 0
            for register, value in saved.items():
                self.uc.reg_write(register, value)
            for register, value in ((mips.UC_MIPS_REG_A0, identity), (mips.UC_MIPS_REG_A1, selector),
                                    (mips.UC_MIPS_REG_SP, self.machine.STACK), (mips.UC_MIPS_REG_RA, self.machine.STOP)):
                self.uc.reg_write(register, value)
            self.uc.emu_start(function, self.machine.STOP, count=10000)
            require(self.uc.reg_read(mips.UC_MIPS_REG_PC) == self.machine.STOP
                    and self.uc.reg_read(mips.UC_MIPS_REG_SP) == self.machine.STACK, 'EXP battle getter did not return')
            require(all(self.uc.reg_read(register) == value for register, value in saved.items()),
                    'EXP battle getter changed a saved register')
            pointer = self.uc.reg_read(mips.UC_MIPS_REG_V0) & 0xffffffff
            require(pointer == BASE + entry.string_at, f'Native EXP battle text pointer differs: {entry.pointer_at:08X}')
            require(bytes(self.uc.mem_read(pointer, len(entry.raw) + 1)) == entry.raw + b'\0',
                    'Native EXP battle text bytes differ')
            count += 1
        require(bytes(self.uc.mem_read(BASE, len(self.data))) == self.data
                and bytes(self.uc.mem_read(BASE - 16, 16)) == b'\xa5' * 16
                and bytes(self.uc.mem_read(BASE + len(self.data), 16)) == b'\xa5' * 16,
                'Native EXP battle getters modified source or crossed its end')
        return {'native_text_getters': count, 'native_functions': 11, 'source_and_canaries_unchanged': True,
                'scope': 'Real OV01 typed name/description/message helpers, not battle effects, I/O or GPU'}
