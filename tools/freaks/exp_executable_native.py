"""Observe EXP embedded width and numeric message blocks without game I/O."""

import struct

import audit_exp_strings as audit
from elf_probe import Elf
from exp_font_native import GLOBAL
from exp_movie_native import MovieMachine
from unpack_disc import require
from unicorn import UC_HOOK_CODE
from unicorn import mips_const as mips


class ExecutableMachine(MovieMachine):
    def quadword(self, uc, address, size, unused):
        if address not in (0x2a4fec, 0x2a4ffc, 0x2a5104, 0x2a5108):
            return super().quadword(uc, address, size, unused)
        word = struct.unpack("<I", uc.mem_read(address, 4))[0]
        base, register = word >> 21 & 31, word >> 16 & 31
        require(base == 29 and word >> 26 in (0x1e, 0x1f), "EXP strcat stack save differs")
        pointer = self.reg("SP") + (word & 0xffff)
        if word >> 26 == 0x1f:
            value = uc.reg_read(mips.UC_MIPS_REG_0 + register)
            uc.mem_write(pointer, struct.pack("<QQ", value & 0xffffffffffffffff, 0))
        else:
            uc.reg_write(mips.UC_MIPS_REG_0 + register, struct.unpack("<Q", uc.mem_read(pointer, 8))[0])
        uc.reg_write(mips.UC_MIPS_REG_PC, address + 4)

    def __init__(self, data, font_data):
        super().__init__(font_data)
        self.elf = Elf(data)
        # Keep the existing probe font/work state, and replace only the game's
        # file-backed segments with this in-memory build.
        phoff = struct.unpack_from("<I", data, 28)[0]
        stride, count = struct.unpack_from("<HH", data, 42)
        for index in range(count):
            kind, offset, address, _, size, _, _, _ = struct.unpack_from("<8I", data, phoff + index * stride)
            if kind == 1:
                self.uc.mem_write(address, data[offset:offset + size])
        self.uc.ctl_remove_cache(0x120000, 0x2b0000)
        self.campaign_templates = {}

    def embedded_width(self, raw):
        require(audit.control_text(raw + b"\0", 0)[0] == raw, "Invalid EXP embedded probe stream")
        pointer = 0x1500000
        self.uc.mem_write(GLOBAL + 8, b"\x1e\0\0\0\x04\x03\0\0")
        self.uc.mem_write(pointer - 16, b"\xa5" * (len(raw) + 33))
        self.uc.mem_write(pointer, raw + b"\0")
        width = self.call(0x195e50, pointer)
        require(bytes(self.uc.mem_read(pointer, len(raw) + 1)) == raw + b"\0"
                and bytes(self.uc.mem_read(pointer - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(pointer + len(raw) + 1, 16)) == b"\xa5" * 16,
                "EXP embedded width helper changed its input")
        return width

    def numeric_block(self, *, short, count, name_bytes=b"Aqua S"):
        require(0 <= count <= 99, "EXP item-count probe is decimal 00..99")
        require(0 < len(name_bytes) < 256 and b"\0" not in name_bytes, "Invalid EXP item-name probe")
        uc = self.uc
        local = 0x1600000 + count % 16 if short else self.machine.STACK
        name = 0x1601000 + count % 16
        message = 0x3e19b0
        # strcat legitimately saves registers immediately below the long buffer.
        left = 16 if short else 64
        uc.mem_write(local - left, b"\xa5" * (left + 272))
        uc.mem_write(name, name_bytes + b"\0")
        uc.mem_write(message - 16, b"\xa5" * 288)
        notified = []

        def cstring(pointer):
            data = bytes(uc.mem_read(pointer, 256))
            end = data.find(b"\0")
            require(end >= 0, "EXP item message exceeds its 256-byte probe buffer")
            return data[:end]

        def strings(uc, address, size, unused):
            if address == 0x2a5438:
                # Select the game's existing scalar strcpy path, not its MMI optimization.
                uc.reg_write(mips.UC_MIPS_REG_PC, 0x2a54d8)
            elif address == 0x2a5000:
                uc.reg_write(mips.UC_MIPS_REG_PC, 0x2a50dc)
            else:
                notified.append((self.reg("A0"), self.reg("A1")))
                uc.reg_write(mips.UC_MIPS_REG_V0, 0)
                uc.reg_write(mips.UC_MIPS_REG_PC, self.reg("RA"))

        hooks = [uc.hook_add(UC_HOOK_CODE, strings, begin=address, end=address)
                 for address in (0x2a5438, 0x2a5000, 0x24c658)]
        try:
            for register, value in self.saved.items():
                uc.reg_write(register, value)
            registers = {"SP": self.machine.STACK, "GP": self.elf.gp, "FP": local,
                         "A0": local, "S4": name, "S5": name, "S2": count, "S3": count,
                         "LO": count // 10, "HI": count % 10}
            if not short:
                low_at, high_at = self.elf.offset(0x1ee09c), self.elf.offset(0x1ee07c)
                high, low = struct.unpack_from("<I", self.elf.data, high_at)[0], struct.unpack_from("<I", self.elf.data, low_at)[0]
                immediate = (low & 0xffff) - (0x10000 if low & 0x8000 else 0)
                registers["S6"] = ((high & 0xffff) << 16) + immediate
            for register, value in registers.items():
                uc.reg_write(getattr(mips, "UC_MIPS_REG_" + register), value)
            saved = {register: uc.reg_read(register) for register in self.saved}
            start, stop = (0x1fa458, 0x1fa4a8) if short else (0x1ee4c8, 0x1ee520)
            uc.emu_start(start, stop, count=10000)
            require(self.reg("PC") == stop and self.reg("SP") == self.machine.STACK,
                    "EXP numeric message block did not reach its original continuation")
            # The long original block also constructs s0 = message, unlike a callee boundary.
            require(all(uc.reg_read(register) == value for register, value in saved.items()
                        if short or register != mips.UC_MIPS_REG_S0), "EXP numeric block changed a saved input")
            require(notified == ([(name, count)] if short else []), "EXP item notification arguments changed")
            require(bytes(uc.mem_read(message - 16, 16)) == b"\xa5" * 16
                    and bytes(uc.mem_read(message + 256, 16)) == b"\xa5" * 16
                    and bytes(uc.mem_read(local - left, 16)) == b"\xa5" * 16
                    and bytes(uc.mem_read(local + 256, 16)) == b"\xa5" * 16,
                    "EXP numeric block crossed a message buffer canary")
            return cstring(message)
        finally:
            for hook in hooks:
                uc.hook_del(hook)

    def campaign(self, index, target, active=True):
        require(0 <= index < 36, "EXP campaign probe index is 0..35")
        uc, captures = self.uc, []
        if target not in self.campaign_templates:
            self.campaign_templates[target] = audit.control_text(bytes(uc.mem_read(target, 2048)), 0)[0]
        template = self.campaign_templates[target]
        uc.mem_write(target, template + b"\0")
        source_before = bytes(uc.mem_read(target - 16, len(template) + 33))
        uc.mem_write(0x6a3958, b"\xa5" * 48)
        uc.mem_write(0x6977fc, bytes((index,)))
        uc.mem_write(0x3e7010 + index, bytes((bool(active),)))

        def help_text(uc, address, size, unused):
            source = self.reg("A1")
            raw = audit.control_text(bytes(uc.mem_read(source, 2048)), 0)[0]
            captures.append((self.reg("A0"), source, raw))

        handle = uc.hook_add(UC_HOOK_CODE, help_text, begin=0x222480, end=0x222480)
        try:
            self.call(0x229550, 0)
            require(len(captures) == 1 and captures[0][:2] == (0x6a3968, target if active else 0x696d58),
                    "EXP campaign changed its help destination or source")
            raw = captures[0][2]
            # The real help initializer writes a pointer/count record, not a copy
            # of the body, and normalizes literal newlines in that body in place.
            pointer, count, offset, state = struct.unpack("<IIiI", uc.mem_read(0x6a3968, 16))
            ascii_bytes = sum(value < 128 for value in raw)
            expected_count = ascii_bytes + (len(raw) - ascii_bytes) // 2 - raw.count(b"\\n") - raw.count(b"\x07")
            require((pointer, count, offset, state) == (captures[0][1], expected_count, -8, 0),
                    "EXP real help initializer record differs")
            require(bytes(uc.mem_read(0x6a3958, 16)) == b"\xa5" * 16
                    and bytes(uc.mem_read(0x6a3978, 16)) == b"\xa5" * 16,
                    "EXP help initializer crossed its 16-byte record")
            expected_source = bytearray(source_before)
            if active:
                expected_source[16:16 + len(raw)] = raw.replace(b"\\n", b"\x0a\x07")
            require(bytes(uc.mem_read(target - 16, len(template) + 33)) == expected_source,
                    "EXP campaign/help initializer changed unrelated source bytes")
            return captures[0][2]
        finally:
            uc.hook_del(handle)
