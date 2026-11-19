"""Bounded boot/allocator regression; not a substitute for a PS2 boot test."""

import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parent

from elf_probe import Elf, signed16
from mini_heap_native import HeapMachine
from unpack_disc import require
from verify_codecs import Native
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import (UC_MIPS_REG_0, UC_MIPS_REG_HI, UC_MIPS_REG_LO,
                               UC_MIPS_REG_PC, UC_MIPS_REG_RA, UC_MIPS_REG_V0)


def address_pair(elf, high, low):
    a, = struct.unpack_from("<I", elf.data, elf.offset(high))
    b, = struct.unpack_from("<I", elf.data, elf.offset(low))
    require(a >> 26 == 15 and b >> 26 == 9, "Unexpected absolute address pair")
    return ((a & 0xffff) << 16) + signed16(b)


def layout(raw):
    elf = Elf(raw)
    h = struct.unpack_from("<16sHHIIIIIHHHHHH", raw)
    loads = [struct.unpack_from("<8I", raw, h[5] + i * h[9]) for i in range(h[10])]
    loads = [p for p in loads if p[0] == 1]
    for p in loads:
        require(p[4] <= p[5] and p[1] + p[4] <= len(raw), "Invalid ELF load size")
        require(p[2] >= 0x100000 and p[2] + p[5] < 0x1ef0000, "ELF exceeds application RAM")
        require(p[6] in (6, 7) and p[7] == 4096 and p[1] % 4096 == p[2] % 4096,
                "ELF load flags/alignment differ")
    ordered = sorted((p[2], p[2] + p[5]) for p in loads)
    require(all(a[1] <= b[0] for a, b in zip(ordered, ordered[1:])), "ELF load ranges overlap")
    segment = elf.named[".kor958"]
    bss_stop = address_pair(elf, 0x100120, 0x100128)
    setup_heap = address_pair(elf, 0x100180, 0x100188)
    libc_break, = struct.unpack_from("<I", raw, elf.offset(0x203d88))
    arena_budget_floor = address_pair(elf, 0x1078d4, 0x1078e0) & ~63
    end = segment[3] + segment[5]
    # The original loop writes one final 16-byte block at/above the unaligned end.
    zero_end = ((bss_stop + 15) & ~15) + 16
    return {"segment_start": segment[3], "segment_end": end,
            "startup_bss_zero_end": zero_end, "setup_heap": setup_heap,
            "libc_initial_break": libc_break, "arena_budget_floor": arena_budget_floor,
            "segment_clear_safe": zero_end <= segment[3],
            "system_heap_excludes_segment": setup_heap >= end,
            "libc_heap_excludes_segment": libc_break >= end,
            "arena_budget_excludes_segment": arena_budget_floor >= end}


class LibcMachine(Native):
    """Run original memalign/malloc/sbrk; model kernel limits and thread locks only."""

    call = HeapMachine.call
    STACK, STOP = 0x1fee000, 0x1fef000

    def __init__(self, raw):
        super().__init__(raw)
        self.install_libc_checks()

    def install_libc_checks(self):
        self.break_requests = []
        for at in (0x1a88b8, 0x1a8938, 0x1b0040, 0x1b0774):
            self.uc.hook_add(UC_HOOK_CODE, self.libc_external, begin=at, end=at)
        self.uc.hook_add(UC_HOOK_CODE, self.break_request, begin=0x1b0760, end=0x1b0760)
        # R5900 MULT has a GPR result, unlike the generic MIPS64 CPU used here.
        self.uc.hook_add(UC_HOOK_CODE, self.libc_multiply, begin=0x1a79d0, end=0x1a79d0)

    def libc_external(self, uc, at, size, user):
        if at == 0x1b0774:
            uc.reg_write(UC_MIPS_REG_0 + 17, 0)
            uc.reg_write(UC_MIPS_REG_PC, at + 4)
            return
        uc.reg_write(UC_MIPS_REG_V0, 0x1ef0000 if at == 0x1b0040 else 0)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def libc_multiply(self, uc, at, size, user):
        word, = struct.unpack_from("<I", self.elf.data, self.elf.offset(at))
        r = lambda n: uc.reg_read(UC_MIPS_REG_0 + n) & 0xffffffff
        value = r(word >> 21 & 31) * r(word >> 16 & 31) & 0xffffffff
        value |= 0xffffffff00000000 if value >> 31 else 0
        uc.reg_write(UC_MIPS_REG_0 + (word >> 11 & 31), value)
        uc.reg_write(UC_MIPS_REG_LO, value)
        uc.reg_write(UC_MIPS_REG_HI, 0)
        uc.reg_write(UC_MIPS_REG_PC, at + 4)

    def break_request(self, uc, at, size, user):
        self.break_requests.append(uc.reg_read(UC_MIPS_REG_0 + 4) & 0xffffffff)


class OrderingMachine(Native):
    """Execute queue insertion/traversal, stopping only at the original GS consumer."""

    STACK, STOP = LibcMachine.STACK, LibcMachine.STOP
    call = HeapMachine.call

    def __init__(self, raw):
        super().__init__(raw)
        if ".kor958" in self.elf.named:
            install_quad_spills(self)
        self.draws = []
        self.dispatches = []
        for at in (0x10c630, 0x10c680, 0x10e360):
            self.uc.hook_add(UC_HOOK_CODE, self.capture, begin=at, end=at)
        self.reset_queue()

    def reset_queue(self):
        self.buckets = self.SOURCE
        self.uc.mem_write(self.buckets, bytes(56))
        self.uc.mem_write(self.buckets + 8, struct.pack("<f", -1.0))
        self.uc.mem_write(self.buckets + 28 + 8, struct.pack("<f", 1000.0))
        self.uc.mem_write(self.buckets + 12, struct.pack("<I", 0xffffffff))
        self.uc.mem_write(self.buckets + 28 + 12, struct.pack("<I", 0xffffffff))
        self.uc.mem_write(0x274320, struct.pack("<3I2fI", 0, self.buckets, 1, 1000.0, 1000.0, 0))
        self.call(0x18a3a8, 0)

    def capture(self, uc, at, size, user):
        if at == 0x10c630:
            uc.mem_write(self.OUTPUT - 16, b"\xa5" * 112)
            uc.mem_write(self.OUTPUT, bytes(80))
            uc.reg_write(UC_MIPS_REG_V0, self.OUTPUT)
        elif at == 0x10e360:
            pointer = uc.reg_read(UC_MIPS_REG_0 + 4)
            require(pointer == self.OUTPUT, "Ordering-table consumer received another node")
            self.dispatches.append(bytes(uc.mem_read(pointer, 80)))
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def check_queue(self):
        read = lambda at: struct.unpack("<I", self.uc.mem_read(at, 4))[0]
        require(read(0x274334) == 1 and read(self.buckets + 4) == self.OUTPUT
                and read(self.buckets + 28) == self.OUTPUT
                and read(self.OUTPUT) == self.buckets
                and read(self.OUTPUT + 4) == self.buckets + 28,
                "Original ordering-table links/count are corrupt")
        self.call(0x18a700, 0, 0x1c70c8)
        require(len(self.dispatches) == 1, "Original queue did not dispatch exactly one FONT primitive")
        self.dispatches.clear()
        require(bytes(self.uc.mem_read(self.OUTPUT - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(self.OUTPUT + 80, 16)) == b"\xa5" * 16,
                "Ordering/dispatch crossed the primitive boundary")


def verify_ordering(original, patched):
    before, after = OrderingMachine(original), OrderingMachine(patched)
    # Ordinary FONT nodes must retain the original register and list effects.
    snapshots = []
    for machine in (before, after):
        machine.uc.mem_write(machine.OUTPUT - 16, b"\xa5" * 112)
        machine.uc.mem_write(machine.OUTPUT, bytes(80))
        machine.uc.mem_write(machine.OUTPUT + 8, struct.pack("<fI", 47.0, 17))
        for n in range(1, 32):
            machine.uc.reg_write(UC_MIPS_REG_0 + n, 0x1122334400000000 + n)
        machine.call(0x18a590, 0, machine.OUTPUT)
        snapshots.append(([machine.uc.reg_read(UC_MIPS_REG_0 + n) for n in range(32)],
                          bytes(machine.uc.mem_read(machine.OUTPUT, 80)),
                          bytes(machine.uc.mem_read(machine.buckets, 56))))
        machine.check_queue()
    require(snapshots[0] == snapshots[1], "Shared draw hook changed ordinary UI register/list behavior")
    # Check both virtual banks through the actual original producer, queue and
    # dispatch. The final GS rasterizer is deliberately not replaced by a claim.
    for token in range(958):
        for bank in (0x4000, 0x4400):
            after.reset_queue()
            after.call(0x10b1e8, (bank + token) << 8, 100, 120, 47)
            after.check_queue()
    return {"ordinary_font_gprs_and_queue_match_original": True,
            "virtual_producer_queue_dispatch_cases": 1916,
            "scope": "Actual original ordering insertion and traversal; FONT consumer entry only, not GPU/VU rasterization."}


def allocation(raw):
    bounds = layout(raw)
    machine = LibcMachine(raw)
    elf = machine.elf
    reserve, = struct.unpack_from("<I", raw, elf.offset(0x207700))
    size = (0x1fef000 - bounds["arena_budget_floor"] - reserve) & ~63
    before = bytes(machine.uc.mem_read(bounds["segment_start"], bounds["segment_end"] - bounds["segment_start"]))
    pointer = machine.call(0x1a7880, 64, size, count=2_000_000)
    require(pointer and pointer % 64 == 0 and pointer + size <= 0x1ef0000,
            "Original libc memalign failed or crossed the kernel heap limit")
    overlap = max(0, min(pointer + size, bounds["segment_end"]) - max(pointer, bounds["segment_start"]))
    # A valid user of the returned allocation is allowed to write any of its bytes.
    lo, hi = max(pointer, bounds["segment_start"]), min(pointer + size, bounds["segment_end"])
    if lo < hi:
        machine.uc.mem_write(lo, b"\xa5" * (hi - lo))
    after = bytes(machine.uc.mem_read(bounds["segment_start"], len(before)))
    return {"pointer": pointer, "requested": size, "end": pointer + size,
            "overlap_bytes": overlap, "segment_unchanged_after_legal_heap_write": before == after,
            "sbrk_requests": machine.break_requests,
            "scope": "Original libc memalign/malloc/sbrk. Kernel EndOfHeap and single-thread locks modeled."}


def require_safe(raw):
    bounds = layout(raw)
    require(all(bounds[k] for k in ("segment_clear_safe", "system_heap_excludes_segment",
                                    "libc_heap_excludes_segment", "arena_budget_excludes_segment")),
            "Startup/libc heap or BSS clear overlaps the 958 segment")
    allocated = allocation(raw)
    require(allocated["overlap_bytes"] == 0 and allocated["segment_unchanged_after_legal_heap_write"],
            "Real libc allocation overlaps the 958 segment")
    return {"layout": bounds, "allocation": allocated}


def verify_hook_entries(original, patched, changes, *, expected=31):
    source, output = Elf(original), Elf(patched)
    segment = output.named[".kor958"]
    entries = []
    for address, words in changes.items():
        at, target_word = int(address, 16), int(words[1], 16)
        if at >= 0x200000 or target_word >> 26 != 2:
            continue
        previous, = struct.unpack_from("<I", original, source.offset(at - 4))
        op, rs, function = previous >> 26, previous >> 21 & 31, previous & 63
        delay_slot = (op in (1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23)
                      or op == 0 and function in (8, 9)
                      or op in (16, 17, 18) and rs == 8)
        target = target_word << 2 & 0x0fffffff
        require(not delay_slot, f"Hook entry is an original branch delay slot: {at:#x}")
        require(segment[3] <= target < segment[3] + segment[5]
                and ((at + 4) ^ target) & 0xf0000000 == 0,
                f"Hook target is not loaded/reachable: {at:#x}")
        entries.append({"entry": at, "target": target})
    require(len(entries) == expected, "Unexpected 958 hook count")
    return {"original_delay_slots_preserved": True, "loaded_reachable_hooks": entries}


def install_quad_spills(machine):
    """Model new SQ/LQ spills only; generic Unicorn has no EE 128-bit GPRs."""
    segment = machine.elf.named[".kor958"]

    def spill(uc, at, size, user):
        word, = struct.unpack_from("<I", machine.elf.data, machine.elf.offset(at))
        register = word >> 16 & 31
        pointer = (uc.reg_read(UC_MIPS_REG_0 + 29) + signed16(word)) & 0xffffffff
        require(pointer % 16 == 0 and machine.STACK - 0x200 <= pointer < machine.STACK,
                "Injected quadword spill crosses its aligned stack frame")
        if word >> 26 == 31:
            uc.mem_write(pointer, struct.pack("<2Q", uc.reg_read(UC_MIPS_REG_0 + register), 0))
        else:
            uc.reg_write(UC_MIPS_REG_0 + register, struct.unpack("<Q", uc.mem_read(pointer, 8))[0])
        uc.reg_write(UC_MIPS_REG_PC, at + 4)

    for at in range(segment[3], segment[3] + segment[5] - 3, 4):
        word, = struct.unpack_from("<I", machine.elf.data, machine.elf.offset(at))
        if word >> 26 in (30, 31) and word >> 21 & 31 == 29:
            machine.uc.hook_add(UC_HOOK_CODE, spill, begin=at, end=at)


def main():
    raw = (ROOT / "rebuilt/MINI.ELF").read_bytes()
    print(json.dumps({"layout": layout(raw), "allocation": allocation(raw)}, indent=2))


if __name__ == "__main__":
    main()
