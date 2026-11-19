"""Execute the three real EXP field loaders; model only allocation, libc fill and I/O."""

import struct

import exp_field as field
from exp_txd import original_elf
from unpack_disc import require
from verify_codecs import Native
from unicorn import UC_HOOK_CODE
from unicorn import mips_const as mips


class FieldMachine:
    def __init__(self, sources):
        self.sources = sources
        self.machine = Native(original_elf())
        self.uc = self.machine.uc
        self.cursor = 0x1000000
        self.allocations, self.reads = [], []
        for address in (0x206830, 0x2a46ac, 0x19bd00, 0x19c278, 0x19c3a0, 0x1a6460):
            self.uc.hook_add(UC_HOOK_CODE, self.stub, begin=address, end=address)

    def register(self, name):
        return self.uc.reg_read(getattr(mips, "UC_MIPS_REG_" + name)) & 0xffffffff

    def filename(self, pointer):
        raw = bytes(self.uc.mem_read(pointer, 256)).split(b"\0", 1)[0]
        name = raw.decode("ascii").replace("\\", "/")
        require(name in self.sources, f"Unexpected native EXP field filename: {name}")
        return name

    def stub(self, uc, address, size, unused):
        a0, a1, a2, a3 = (self.register(name) for name in ("A0", "A1", "A2", "A3"))
        result = 0
        if address == 0x206830:
            require(0 < a0 < 1 << 20 and self.cursor + a0 + 32 < self.machine.OUTPUT,
                    "Native EXP field allocation exceeds probe arena")
            pointer = self.cursor + 16
            self.uc.mem_write(self.cursor, b"\xa5" * (a0 + 32))
            self.allocations.append((pointer, a0))
            self.cursor = (pointer + a0 + 31) & ~15
            result = pointer
        elif address == 0x2a46ac:
            require(any(pointer <= a0 and a0 + a2 <= pointer + count for pointer, count in self.allocations),
                    "Native EXP memset crosses an allocation")
            self.uc.mem_write(a0, bytes((a1 & 255,)) * a2)
            result = a0
        elif address == 0x19bd00:
            result = len(self.sources[self.filename(a0)])
        elif address == 0x19c278:
            name = self.filename(a0)
            data = self.sources[name]
            require((a1, len(data)) in self.allocations and a2 in (0, 1) and a3 == 0,
                    "Native EXP field read destination/flags differ")
            self.uc.mem_write(a1, data)
            self.reads.append((name, a1))
        elif address == 0x1a6460:
            raise ValueError("Native EXP field loader asserted")
        self.uc.reg_write(mips.UC_MIPS_REG_V0, result)
        self.uc.reg_write(mips.UC_MIPS_REG_PC, self.register("RA"))

    def verify(self, name):
        profile = field.PROFILES[name]
        saved = {getattr(mips, "UC_MIPS_REG_" + key): 0x480000 + index * 16
                 for index, key in enumerate(("S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "FP"))}
        saved[mips.UC_MIPS_REG_GP] = self.machine.elf.gp
        for register, value in saved.items():
            self.uc.reg_write(register, value)
        self.uc.reg_write(mips.UC_MIPS_REG_SP, self.machine.STACK)
        self.uc.reg_write(mips.UC_MIPS_REG_RA, self.machine.STOP)
        self.uc.emu_start(profile["entry"], self.machine.STOP, count=20_000_000, timeout=60_000_000)
        require(self.register("PC") == self.machine.STOP and self.register("SP") == self.machine.STACK,
                "Native EXP field loader did not return")
        require(all(self.uc.reg_read(register) == value for register, value in saved.items()),
                "Native EXP field loader changed a saved register")
        pointer = struct.unpack("<I", self.uc.mem_read(profile["global"], 4))[0]
        count = profile["count"] * profile["stride"]
        require((pointer, count) == self.allocations[0], "Native EXP field record allocation differs")
        expected = bytearray(count)
        values = field.source_fields(self.sources[name], name)
        offsets = {key: at for key, at, _ in profile["fields"]}
        for item in values:
            record, key = item.key.split(".")
            at = (int(record) - 1) * profile["stride"] + offsets[key]
            raw = field.runtime_text(item.value.encode("cp932"))
            expected[at:at + len(raw)] = raw
        require(bytes(self.uc.mem_read(pointer, count)) == expected,
                "Native EXP field records differ from the codec model")
        require(len(self.reads) == 1 and self.reads[0][0] == name, "Native EXP field read set differs")
        for filename, source in self.reads:
            require(bytes(self.uc.mem_read(source, len(self.sources[filename]))) == self.sources[filename],
                    "Native EXP field parser changed its source")
        for allocation, size in self.allocations:
            require(bytes(self.uc.mem_read(allocation - 16, 16)) == b"\xa5" * 16
                    and bytes(self.uc.mem_read(allocation + size, 16)) == b"\xa5" * 16,
                    "Native EXP field loader crossed an allocation")
        return {"file": name, "records": profile["count"], "fields": len(values),
                "runtime_record_bytes": count, "input_bytes": len(self.sources[name]),
                "allocator_requests": [size for _, size in self.allocations],
                "all_record_bytes_source_and_canaries_match": True,
                "scope": "Original loader/format search/quoted parser/SJIS conversion; allocation, memset and completed disc I/O modelled, no GPU or game-state execution"}
