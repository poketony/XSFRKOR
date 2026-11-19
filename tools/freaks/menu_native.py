"""Narrow native menu-command runner for graphics load/relocation validation."""

import struct

from adv_format import require
from elf_probe import signed16
from tmpp import Tmpp
from unpack_contents import digest
from verify_codecs import Native
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import (UC_MIPS_REG_0, UC_MIPS_REG_A0, UC_MIPS_REG_A1,
                               UC_MIPS_REG_A2, UC_MIPS_REG_A3, UC_MIPS_REG_GP,
                               UC_MIPS_REG_T0,
                               UC_MIPS_REG_SP, UC_MIPS_REG_RA, UC_MIPS_REG_PC,
                               UC_MIPS_REG_V0, UC_MIPS_REG_HI, UC_MIPS_REG_LO)


COMPONENTS = {"LNCH": ("SLPS_253.53", 0), "ADV": ("ADV/ADV.ELF", 0x160),
              "DTB": ("DTB/DTB.ELF", -0x128)}
STATE = 0x1400000
ALLOWED = {0x0b, 0x0e, 0x16, 0x17, 0x22, 0x23, 0x42, 0x4f, 0x6d, 0x83, 0x84, 0x87, 0x88, 0x89}


class MenuVm:
    def __init__(self, data, component, files=None, *, state=STATE, read_limit=0x180000):
        self.native = Native(data)
        self.uc = self.native.uc
        self.elf = self.native.elf
        self.component = component
        require(state in (STATE, 0x2000000), "Unaudited menu probe state address")
        require(read_limit in (0x180000, 0x600000), "Unaudited menu read limit")
        self.state = state
        self.read_limit = read_limit
        self.delta = COMPONENTS[component][1]
        self.files = {name.casefold(): value for name, value in (files or {}).items()}
        self.events = []
        self.expected_size = None
        self.uc.mem_write(self.state, bytes(256))

        def word(address):
            return struct.unpack_from("<I", data, self.elf.offset(address))[0]

        dispatch = 0x112500 + self.delta
        table = ((word(dispatch + 0x3c) & 0xffff) << 16) + signed16(word(dispatch + 0x44))
        self.handlers = struct.unpack_from("<144I", data, self.elf.offset(table))
        self.read_full = 0x116ce0 + self.delta
        self.read_partial = 0x116c78 + self.delta
        self.busy = 0x116cc8 + self.delta
        self.finished = 0x116d30 + self.delta
        self.upload = self.jump_target(word(self.handlers[0x6d] + 0x78))
        self.upload_callback = self.jump_target(word(self.handlers[0x6d] + 0x90))
        self.mul = self.handlers[0x22] + 0x3c
        require(word(self.mul) == 0x00671818, "Menu indexed-pointer multiply differs")
        getter = 0x116c70 + self.delta
        self.uc.mem_write(self.elf.gp + signed16(word(getter + 4)), struct.pack("<I", self.native.SOURCE))
        getter = 0x10a530 + self.delta
        self.work_cursor = self.elf.gp + signed16(word(getter))
        self.work_base = self.elf.gp + signed16(word(getter + 4))
        self.uc.mem_write(self.work_cursor, struct.pack("<I", 0x10000))
        self.uc.mem_write(self.work_base, struct.pack("<I", self.native.SOURCE))
        for address in (self.mul, self.read_full, self.read_partial, self.busy, self.finished,
                        0x102610, self.upload, self.upload_callback):
            self.uc.hook_add(UC_HOOK_CODE, self.intercept, begin=address, end=address)

    @property
    def work(self):
        base = struct.unpack("<I", self.uc.mem_read(self.work_base, 4))[0]
        cursor = struct.unpack("<I", self.uc.mem_read(self.work_cursor, 4))[0]
        return base + cursor * 16

    @staticmethod
    def jump_target(word):
        require(word >> 26 == 3, "Expected original JAL")
        return (word & 0x3ffffff) << 2

    def intercept(self, uc, address, size, _):
        n = self.native
        if address == self.mul:
            a, b = (uc.reg_read(UC_MIPS_REG_0 + r) & 0xffffffff for r in (3, 7))
            a = a if a < 0x80000000 else a - 0x100000000
            b = b if b < 0x80000000 else b - 0x100000000
            product = a * b & 0xffffffffffffffff
            low, high = product & 0xffffffff, product >> 32
            extend = lambda v: v if v < 0x80000000 else v | 0xffffffff00000000
            uc.reg_write(UC_MIPS_REG_0 + 3, extend(low))
            uc.reg_write(UC_MIPS_REG_LO, extend(low))
            uc.reg_write(UC_MIPS_REG_HI, extend(high))
            uc.reg_write(UC_MIPS_REG_PC, address + 4)
            return
        if address in (self.read_full, self.read_partial):
            args = [uc.reg_read(r) for r in (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3)]
            name = bytes(uc.mem_read(args[0], 64)).split(b"\0", 1)[0].decode("ascii")
            require(name.casefold() in self.files, f"Unexpected menu input: {name}")
            value = self.files[name.casefold()]
            if isinstance(value, bytes):
                file_size = len(value)
                count = args[2] or file_size - args[1]
                block = value[args[1]:args[1] + count]
            else:
                file_size = value.stat().st_size
                count = args[2] or file_size - args[1]
                with value.open("rb") as stream:
                    stream.seek(args[1])
                    block = stream.read(count)
            require(0 <= args[1] <= file_size and 0 < count <= self.read_limit and len(block) == count,
                    "Menu file read allocation differs")
            uc.mem_write(args[3], block)
            self.events.append({"kind": "read", "name": name, "offset": args[1], "size": count,
                                "requested_size": args[2], "destination": args[3], "sha256": digest(block)})
            result = 1
        elif address == self.busy:
            result = 0
        elif address == self.finished:
            result = 1
        elif address == 0x102610:
            destination, source, count = [uc.reg_read(r) for r in (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2)]
            require(destination == self.work and 0 < count <= 0x180000, "Unexpected menu decompression buffer")
            self.events.append({"kind": "decode", "source": source, "size": count,
                                "destination": destination, "stored_sha256": digest(bytes(uc.mem_read(source, count)))})
            return
        elif address == self.upload:
            source = uc.reg_read(UC_MIPS_REG_A0)
            header = struct.unpack("<32I", uc.mem_read(source, 128))
            length = 128 + (1024 if header[3] == 3 else 0) + header[7] * header[8]
            require(128 <= length <= 0x100000, "Menu decoded texture exceeds work buffer")
            raw = bytes(uc.mem_read(source, length))
            Tmpp(raw)
            self.events.append({"kind": "upload", "size": length, "sha256": digest(raw),
                                "slot": uc.reg_read(UC_MIPS_REG_A1),
                                "palette_bank": uc.reg_read(UC_MIPS_REG_T0)})
            result = 0
        elif address == self.upload_callback:
            result = 0
        else:
            return
        uc.reg_write(UC_MIPS_REG_V0, result)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def step(self, address):
        opcode = self.uc.mem_read(address, 1)[0]
        require(opcode in ALLOWED, f"Menu probe has no audited handler for {opcode:02X} at {address:08X}")
        self.uc.mem_write(self.state, struct.pack("<I", address))
        if opcode == 0x89 and self.expected_size is not None:
            require(128 <= self.expected_size <= 0x100000, "Expected TM++ output size exceeds work buffer")
            self.uc.mem_write(self.work - 16, b"\xa5" * 16)
            self.uc.mem_write(self.work + self.expected_size, b"\xa5" * 16)
        for register, value in ((UC_MIPS_REG_GP, self.elf.gp), (UC_MIPS_REG_SP, self.native.STACK),
                                (UC_MIPS_REG_RA, self.native.STOP), (UC_MIPS_REG_A0, self.state)):
            self.uc.reg_write(register, value)
        self.uc.emu_start(self.handlers[opcode], self.native.STOP, count=20000000)
        require(self.uc.reg_read(UC_MIPS_REG_PC) == self.native.STOP, "Native menu command did not return")
        require(self.uc.reg_read(UC_MIPS_REG_SP) == self.native.STACK, "Native menu stack differs")
        if opcode == 0x89 and self.expected_size is not None:
            require(bytes(self.uc.mem_read(self.work - 16, 16)) == b"\xa5" * 16
                    and bytes(self.uc.mem_read(self.work + self.expected_size, 16)) == b"\xa5" * 16,
                    "Menu decompression crossed expected output boundary")
        return struct.unpack("<I", self.uc.mem_read(self.state, 4))[0]

    def state_word(self, offset, value):
        require(0xa0 <= offset <= 0xfc, "Only menu temporary state may be seeded")
        self.uc.mem_write(self.state + offset, struct.pack("<I", value))
