"""Run EXP's in-place movie subtitle compiler and real width functions."""

import struct

import exp_movie as movie
from exp_font_native import FontMachine, FONT, GLOBAL, TEXT
from unpack_disc import require
from unicorn import UC_HOOK_CODE
from unicorn import mips_const as mips


class MovieMachine(FontMachine):
    def __init__(self, font_data):
        super().__init__(font_data)
        self.uc.hook_add(UC_HOOK_CODE, self.quadword)

    def quadword(self, uc, address, size, unused):
        word = struct.unpack("<I", uc.mem_read(address, 4))[0]
        opcode = word >> 26
        if opcode not in (0x1e, 0x1f):
            return
        # These width-path stack saves only feed the low 64-bit MIPS registers.
        require(0x195000 <= address < 0x199000, "Unaudited EXP movie quadword instruction")
        base, target = word >> 21 & 31, word >> 16 & 31
        immediate = (word & 0xffff) - (0x10000 if word & 0x8000 else 0)
        pointer = (uc.reg_read(mips.UC_MIPS_REG_0 + base) + immediate) & 0xffffffff
        require(base == 29, "EXP movie quadword access is not a stack save")
        if opcode == 0x1f:
            value = uc.reg_read(mips.UC_MIPS_REG_0 + target)
            uc.mem_write(pointer, struct.pack("<QQ", value & 0xffffffffffffffff, 0))
        else:
            uc.reg_write(mips.UC_MIPS_REG_0 + target, struct.unpack("<Q", uc.mem_read(pointer, 8))[0])
        uc.reg_write(mips.UC_MIPS_REG_PC, address + 4)

    def call(self, entry, a0, a1=0, a2=0):
        for register, value in self.saved.items():
            self.uc.reg_write(register, value)
        for name, value in (("A0", a0), ("A1", a1), ("A2", a2),
                            ("SP", self.machine.STACK), ("RA", self.machine.STOP)):
            self.uc.reg_write(getattr(mips, "UC_MIPS_REG_" + name), value)
        self.uc.emu_start(entry, self.machine.STOP, count=30_000_000, timeout=60_000_000)
        require(self.reg("PC") == self.machine.STOP and self.reg("SP") == self.machine.STACK,
                "EXP native movie function did not return")
        require(all(self.uc.reg_read(register) == value for register, value in self.saved.items()),
                "EXP native movie function changed a saved register")
        return self.reg("V0") & 0xffffffff

    def compile(self, data):
        require(b"\0" not in data.rstrip(b"\0") and len(data) < 65536, "EXP movie probe input is invalid")
        self.uc.mem_write(GLOBAL + 8, b"\x1e\0\0\0\x04\x03\0\0")
        self.uc.mem_write(TEXT - 16, b"\xa5" * (len(data) + 544))
        self.uc.mem_write(TEXT, data + bytes(512))
        length = self.call(0x1e6fa8, TEXT)
        require(0 < length < len(data) + 512, "EXP movie compiler exceeded the probe arena")
        require(bytes(self.uc.mem_read(TEXT - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(TEXT + len(data) + 512, 16)) == b"\xa5" * 16,
                "EXP movie compiler crossed a canary")
        output = bytes(self.uc.mem_read(TEXT, length))
        require(bytes(self.uc.mem_read(FONT, len(self.font_data))) == self.font_data,
                "EXP movie compiler changed its font")
        return output, {"input_bytes": len(data), "compiled_bytes": length,
                        "aligned_movie_work_offset": (length + 63) & ~63,
                        "compiler": "0x1E6FA8", "line_parser": "0x1E6BD8",
                        "scope": "Real in-place compiler and width logic; no CRI decoding, disc I/O or GPU"}

    def width(self, raw):
        pointer = 0x1500000
        require(b"\0" not in raw and len(raw) < 16384, "Invalid EXP movie width input")
        self.uc.mem_write(pointer, raw + b"\0")
        return self.call(0x195e50, pointer)

    def verify(self, data, *, translated=False):
        output, report = self.compile(data)
        rows = movie.cues(data)
        # Independent grammar expansion; only width arithmetic uses the separate native helper.
        at, measured, count = 0, [], 0
        for cue in rows:
            if cue.frame:
                require(struct.unpack_from("<H", output, at)[0] == cue.frame,
                        "EXP native movie frame differs")
                at += 2
            lines = (cue.raw,) if not cue.frame else movie.pages(cue.raw)
            expected = bytearray()
            for line in lines:
                width = self.width(line)
                if translated and cue.frame:
                    require(width < 510, "EXP movie line exceeds the one-byte centering range; use literal \\n")
                measured.append(width)
                expected.extend(bytes((1, ~(width // 2) & 255)) + line + b"\n")
            expected[-1] = 0
            require(output[at:at + len(expected)] == expected,
                    "EXP native movie text/centering differs from the independent grammar")
            at += len(expected)
            count += 1
        require(output[at:at + 3] == b"\xff\xff\0", "EXP native movie end marker differs")
        report.update({"cues": count, "dialogues": len(movie.editable(data)),
                       "native_width_checks": len(measured), "maximum_native_width": max(measured),
                       "frame_text_ruby_centering_and_end": "pass"})
        return report

    def verify_loading(self, name, data, *, patched=True, work_bytes=0x180000):
        require(name in movie.NAMES and data.endswith(bytes(16)), "Loading probe needs terminated movie output")
        _, compiled = self.compile(data)
        require(compiled["aligned_movie_work_offset"] <= movie.RESERVE and len(data) <= movie.RESERVE,
                "EXP subtitles exceed the reserved input/runtime prefix")
        original = self.machine.elf.data
        patched_elf = movie.patch_loader(original) if patched else original
        at = self.machine.elf.offset(movie.ALLOCATION_ARGUMENT)
        self.uc.mem_write(movie.ALLOCATION_ARGUMENT, patched_elf[at:at + 4])
        self.uc.ctl_remove_cache(movie.ALLOCATION_ARGUMENT - 4, movie.ALLOCATION_ARGUMENT + 4)
        filename = 0x1600000
        self.uc.mem_write(filename, name.rsplit("/", 1)[-1].removesuffix(".txt").encode("ascii") + b"\0")
        self.uc.mem_write(0x63a01a, b"\x01")
        self.uc.mem_write(0x63a009, b"\x00")
        state = {"requests": [], "reads": [], "handoff": None}

        def stub(uc, address, size, unused):
            def reg(key):
                return self.reg(key) & 0xffffffff
            if address == 0x1a06f8:
                state["handoff"] = reg("A1")
                uc.emu_stop()
                return
            result = 0
            if address == 0x1a0670:
                result = work_bytes
            elif address == 0x1e0e28:
                requested = reg("A0")
                require(work_bytes <= requested <= work_bytes + movie.RESERVE,
                        "Unexpected EXP movie allocation request")
                state["requests"].append(requested)
                uc.mem_write(TEXT - 16, b"\xa5" * (requested + 32))
                result = TEXT
            elif address == 0x19c278:
                source = bytes(uc.mem_read(reg("A0"), 64)).split(b"\0", 1)[0].decode("ascii")
                require(source == name and (reg("A1"), reg("A2"), reg("A3")) == (TEXT, 0, 0),
                        "EXP movie read path/destination/flags differ")
                state["reads"].append(source)
                uc.mem_write(TEXT, data)
            uc.reg_write(mips.UC_MIPS_REG_V0, result)
            uc.reg_write(mips.UC_MIPS_REG_PC, reg("RA"))

        hooks = [self.uc.hook_add(UC_HOOK_CODE, stub, begin=entry, end=entry)
                 for entry in (0x1e71a8, 0x1e07a8, 0x1a0670, 0x1e0e28, 0x19c3a0,
                               0x1990a8, 0x1b4684, 0x19c278, 0x1a06f8)]
        try:
            for register, value in self.saved.items():
                self.uc.reg_write(register, value)
            self.uc.reg_write(mips.UC_MIPS_REG_A0, filename)
            self.uc.reg_write(mips.UC_MIPS_REG_SP, self.machine.STACK)
            self.uc.reg_write(mips.UC_MIPS_REG_RA, self.machine.STOP)
            self.uc.emu_start(0x1e72a8, self.machine.STOP, count=30_000_000, timeout=60_000_000)
        finally:
            for handle in hooks:
                self.uc.hook_del(handle)
        require(state["reads"] == [name] and len(state["requests"]) == 1
                and state["handoff"] == TEXT + compiled["aligned_movie_work_offset"],
                "EXP subtitle compilation/CRI handoff differs")
        requested = state["requests"][0]
        require(bytes(self.uc.mem_read(TEXT - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(TEXT + requested, 16)) == b"\xa5" * 16,
                "EXP movie loading crossed an allocation")
        needed = state["handoff"] - TEXT + work_bytes
        require(needed <= requested, "Original movie allocation omits the subtitle prefix")
        return {"source": name, "requested_bytes": requested, "cri_work_bytes": work_bytes,
                "subtitle_prefix": state["handoff"] - TEXT, "end_inside_allocation": True,
                "scope": "Real movie caller/compiler through CRI handoff; allocation size and completed I/O modelled"}
