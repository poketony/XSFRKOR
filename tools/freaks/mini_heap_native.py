"""MINI's real arena allocator and complete WWROM load/decode preflight."""

import struct

from unpack_contents import digest, wwrom_parts
from unpack_disc import require
from verify_codecs import Native
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2,
                               UC_MIPS_REG_A3, UC_MIPS_REG_GP, UC_MIPS_REG_SP,
                               UC_MIPS_REG_RA, UC_MIPS_REG_PC, UC_MIPS_REG_V0,
                               UC_MIPS_REG_V1)


class HeapMachine(Native):
    def __init__(self, elf_data, rom):
        super().__init__(elf_data)
        self.rom = rom
        self.allocations = []
        self.pending = []
        self.io_events = []
        for address in (0x185138, 0x1a9720, 0x1a991c, 0x1b3f30, 0x1b4570, 0x1b41b8):
            self.uc.hook_add(UC_HOOK_CODE, self.external, begin=address, end=address)
        for address in (0x185b98, 0x185d34):
            self.uc.hook_add(UC_HOOK_CODE, self.allocation, begin=address, end=address)
        self.uc.hook_add(UC_HOOK_CODE, self.arena_request, begin=0x1a7880, end=0x1a7880)

    def string(self, address, limit=64):
        raw = bytes(self.uc.mem_read(address, limit))
        require(b"\0" in raw, "Native MINI probe string exceeds bound")
        return raw.split(b"\0", 1)[0]

    def arena_request(self, uc, address, size, user):
        require(uc.reg_read(UC_MIPS_REG_A0) == 64, "MINI outer heap alignment differs")
        self.arena_start = uc.reg_read(UC_MIPS_REG_V1) & 0xffffffff
        self.arena_size = uc.reg_read(UC_MIPS_REG_A1) & 0xffffffff
        self.arena_reached = True
        uc.emu_stop()

    def allocation(self, uc, address, size, user):
        if address == 0x185b98:
            self.pending.append({"name": self.string(uc.reg_read(UC_MIPS_REG_A0)).decode("ascii"),
                                 "requested": uc.reg_read(UC_MIPS_REG_A1) & 0xffffffff})
            return
        require(self.pending, "Unmatched native MINI allocation return")
        row = self.pending.pop()
        descriptor = uc.reg_read(UC_MIPS_REG_V0) & 0xffffffff
        row["descriptor"] = descriptor
        if descriptor:
            pointer, capacity = struct.unpack("<2I", uc.mem_read(descriptor, 8))
            row.update(pointer=pointer, capacity=capacity)
            require(row["requested"] <= capacity < row["requested"] + 256,
                    "Native MINI allocation capacity differs")
            require(self.arena_start <= pointer and pointer + capacity <= self.arena_start + self.arena_size,
                    "Native MINI allocation outside arena")
        self.allocations.append(row)

    def external(self, uc, address, size, user):
        args = [uc.reg_read(r) & 0xffffffff for r in (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2)]
        result = 0
        if address == 0x1a9720:
            require(self.string(args[1]) == b"%02d" and args[2] < 22, "Unexpected MINI allocation-name format")
            raw = f"{args[2]:02d}".encode("ascii")
            uc.mem_write(args[0], raw + b"\0")
            result = len(raw)
        elif address == 0x1a991c:
            raw = self.string(args[0], 32) + self.string(args[1], 4)
            require(raw.startswith(b"WWROM MMAP") and len(raw) < 32, "Unexpected MINI allocation name")
            uc.mem_write(args[0], raw + b"\0")
            result = args[0]
        elif address == 0x1b3f30:
            require(self.string(args[0]) == b"cdrom0:\\MINI\\WWROM.DAT;1", "Unexpected MINI heap-preflight path")
            result = 3
            self.io_events.append("open")
        elif address == 0x1b4570:
            require(args[0] == 3 and args[2] == len(self.rom), "Native MINI archive read size differs")
            row = self.allocations[-1]
            require(row["name"] == "ROM IMAGE" and row["pointer"] <= args[1]
                    and args[1] + args[2] <= row["pointer"] + row["capacity"], "Native MINI archive read outside allocation")
            uc.mem_write(args[1], self.rom)
            result = len(self.rom)
            self.io_events.append("read")
        elif address == 0x1b41b8:
            require(args[0] == 3, "Unexpected MINI heap-preflight descriptor")
            self.io_events.append("close")
        uc.reg_write(UC_MIPS_REG_V0, result)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def call(self, entry, *args, count=1_000_000):
        self.uc.reg_write(UC_MIPS_REG_GP, self.elf.gp)
        self.uc.reg_write(UC_MIPS_REG_SP, self.STACK)
        self.uc.reg_write(UC_MIPS_REG_RA, self.STOP)
        for register, value in zip((UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3), args):
            self.uc.reg_write(register, value)
        self.uc.emu_start(entry, self.STOP, count=count)
        require(self.uc.reg_read(UC_MIPS_REG_PC) == self.STOP, "Native MINI heap function did not return")
        require(self.uc.reg_read(UC_MIPS_REG_SP) == self.STACK, "Native MINI heap stack changed")
        return self.uc.reg_read(UC_MIPS_REG_V0) & 0xffffffff

    def initialize(self):
        self.uc.reg_write(UC_MIPS_REG_GP, self.elf.gp)
        self.uc.reg_write(UC_MIPS_REG_SP, self.STACK)
        self.arena_reached = False
        self.uc.emu_start(0x1078d4, self.STOP, count=100)
        require(self.arena_reached and self.arena_size > 20 << 20, "Native MINI arena request not reached")
        # The outer libc allocation is modelled; internal allocator code is not.
        self.uc.mem_write(self.arena_start + self.arena_size, b"\xa5" * 16)
        count = struct.unpack_from("<I", self.elf.data, self.elf.offset(0x207704))[0]
        self.call(0x185680, self.arena_start, self.arena_start + self.arena_size - 1, count, 0,
                  count=300_000_000)
        self.root = struct.unpack("<I", self.uc.mem_read(self.elf.gp - 0x7f70, 4))[0]
        require(self.root == (self.arena_start + 255) & ~255, "Native MINI arena root differs")
        state = 0x1bf4b0 + 0x2bc
        slots, stride = struct.unpack_from("<2I", self.elf.data, self.elf.offset(0x207708))
        require(self.call(0x186810, state, 0x2138f0, slots, stride) == 1, "Native MINI main pool allocation failed")

    def verify(self):
        rows = list(wwrom_parts(self.elf.data, self.rom))
        self.initialize()
        result = self.call(0x109c78, count=600_000_000)
        failed = next((r for r in self.allocations if not r["descriptor"]), None)
        require(result == 0xffffffff and failed is None,
                f"Native MINI resource allocation failed: {failed['name']} ({failed['requested']} bytes)"
                if failed else "Native MINI WWROM initialization failed")
        require(not self.pending and all(row["descriptor"] for row in self.allocations),
                "Native MINI resource allocation failed")
        require(self.io_events == ["open", "read", "close"], "Native MINI resource I/O differs")
        source = struct.unpack("<I", self.uc.mem_read(0x224570, 4))[0]
        require(bytes(self.uc.mem_read(source, len(self.rom))) == self.rom, "Native MINI archive bytes changed")
        decoded = 0
        for row, expected in rows:
            if row["codec"] != "Ulz":
                continue
            descriptor = struct.unpack("<I", self.uc.mem_read(0x224578 + row["index"] * 36, 4))[0]
            pointer, capacity = struct.unpack("<2I", self.uc.mem_read(descriptor, 8))
            require(len(expected) <= capacity and bytes(self.uc.mem_read(pointer, len(expected))) == expected,
                    f"Native MINI decoded allocation differs: {row['name']}")
            decoded += 1
        ordered = sorted((r["pointer"], r["pointer"] + r["capacity"]) for r in self.allocations)
        require(all(a[1] <= b[0] for a, b in zip(ordered, ordered[1:])), "Native MINI allocations overlap")
        require(bytes(self.uc.mem_read(self.arena_start + self.arena_size, 16)) == b"\xa5" * 16,
                "Native MINI arena end crossed")
        free = struct.unpack("<I", self.uc.mem_read(self.root + 0x20, 4))[0]
        return {"arena_start": self.arena_start, "arena_bytes": self.arena_size,
                "resource_allocations": self.allocations, "decoded_files": decoded,
                "free_after_resource_load": free, "rom_bytes": len(self.rom),
                "rom_sha256": digest(self.rom), "disjoint_allocations_and_arena_end": "pass",
                "scope": "Real game arena allocator, main pool, WWROM loader and Ulz decoders. Outer arena placement, file I/O and diagnostic formatting modelled; later runtime allocations not included."}
