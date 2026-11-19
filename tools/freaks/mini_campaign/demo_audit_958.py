"""Bounded native title requests and control flow; no resource heap or GPU."""

import argparse
import hashlib
import json
from pathlib import Path
import struct


from mini_heap_native import HeapMachine
from unpack_disc import require
from verify_codecs import Native
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import UC_MIPS_REG_0, UC_MIPS_REG_PC, UC_MIPS_REG_RA, UC_MIPS_REG_V0

import demo_958 as demo


class TitleMachine(Native):
    call = HeapMachine.call

    def __init__(self, elf):
        super().__init__(elf)
        self.request = None
        self.random_calls = 0
        self.stubs = (0x116cd8, 0x14df28, 0x11d0c8, 0x15bbd0, 0x15bba0)
        for at in self.stubs:
            self.uc.hook_add(UC_HOOK_CODE, self.skip, begin=at, end=at)
        for at in (0x143460, 0x1472c0):
            self.uc.hook_add(UC_HOOK_CODE, self.capture, begin=at, end=at)
        self.uc.hook_add(UC_HOOK_CODE, self.random, begin=0x114698, end=0x114698)
        self.uc.hook_add(UC_HOOK_CODE, self.memset, begin=0x1a87f8, end=0x1a87f8)

    def skip(self, uc, *_):
        uc.reg_write(UC_MIPS_REG_V0, 0)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def capture(self, uc, at, *_):
        pointer = uc.reg_read(UC_MIPS_REG_0 + 4)
        self.request = (at, bytes(uc.mem_read(pointer, 0x88 if at == 0x143460 else 0x44)))
        self.skip(uc)

    def random(self, *_):
        self.random_calls += 1

    def memset(self, uc, *_):
        pointer, value, count = (uc.reg_read(UC_MIPS_REG_0 + r) for r in (4, 5, 6))
        require(count <= 0x1000, "Title initializer memset exceeds focused bound")
        uc.mem_write(pointer, bytes((value & 255,)) * count)
        self.skip(uc)
        uc.reg_write(UC_MIPS_REG_V0, pointer)

    def title_request(self, stage):
        slot = [row[2] for row in demo.SLOTS].index(stage)
        self.uc.mem_write(self.SOURCE, bytes(0x100))
        self.uc.mem_write(self.SOURCE + 0x38, struct.pack("<2H", int(stage >= 100), slot))
        self.request = None
        self.call(0x16b520, self.SOURCE)
        require(self.request is not None, "Native attract entry did not reach its gameplay loader")
        entry, raw = self.request
        return entry, raw

    def initialize_request(self, stage, raw):
        self.uc.mem_write(self.SOURCE, raw)
        # Constructors reset/copy UI state but do not allocate the board. Their
        # Clear/pad control executes natively; EE-only memset is modeled exactly.
        if stage < 100:
            self.uc.mem_write(0x272cb0, bytes(0x48))
            entry, state, offset = 0x143278, 0x272cb0, 12
        else:
            self.uc.mem_write(0x272f10, bytes(0x40))
            entry, state, offset = 0x147088, 0x272f10, 8
        require(self.call(entry, self.SOURCE) == 0, "Native attract UI initialization failed")
        value = struct.unpack("<I", self.uc.mem_read(state + offset, 4))[0]
        ticks = struct.unpack("<I", self.uc.mem_read(state + offset + 4, 4))[0]
        turn = struct.unpack("<I", self.uc.mem_read(state + 0x24, 4))[0] if stage >= 100 else None
        return value, ticks, turn

    def timeout(self, stage):
        counters = dict(samples=0, starts=0, stops=0, gameplay=0, cleanup=0)
        hooks = []

        def external(uc, at, *_):
            key = {0x1371f8: 'samples', 0x137198: 'starts', 0x137238: 'stops',
                   0x16b860: 'gameplay', 0x137058: 'cleanup'}.get(at)
            if key:
                counters[key] += 1
            if at == 0x1371f8:
                uc.mem_write(uc.reg_read(UC_MIPS_REG_0 + 5), bytes(4))
            self.skip(uc)

        for at in (0x1371f8, 0x137198, 0x137238, 0x137058, 0x16b860,
                   0x111248, 0x111258, 0x111280, 0x113130, 0x1436c8, 0x147860):
            hooks.append(self.uc.hook_add(UC_HOOK_CODE, external, begin=at, end=at))
        try:
            self.uc.mem_write(self.SOURCE, bytes(0x100))
            self.uc.mem_write(self.SOURCE + 0x30, struct.pack('<I', 3))
            self.uc.mem_write(self.SOURCE + 0x34, struct.pack('<I', 20))
            self.uc.mem_write(self.SOURCE + 0x38, struct.pack('<2H', int(stage >= 100),
                                                          [r[2] for r in demo.SLOTS].index(stage)))
            self.uc.mem_write(self.SOURCE + 0x40, b'\xff')
            self.uc.mem_write(self.SOURCE + 0x48, struct.pack('<I', self.OUTPUT))
            for _ in range(20):
                self.call(0x16b9f0, self.SOURCE)
            require(counters['starts'] == 1 and counters['samples'] == 0,
                    'Title consumed a replay sample during warmup')
            for frame in range(demo.ACTIVE_FRAMES):
                self.call(0x16b9f0, self.SOURCE)
                state = struct.unpack('<h', self.uc.mem_read(self.SOURCE + 0x30, 2))[0]
                require(state == (-2 if frame == demo.ACTIVE_FRAMES - 1 else 3),
                        'Native title left active replay at another timeout')
            require(counters['samples'] == demo.ACTIVE_FRAMES * (2 if stage >= 100 else 1),
                    'Native title did not consume the correct number of pad lanes')
            for _ in range(20):
                self.call(0x16b9f0, self.SOURCE)
            require(struct.unpack('<h', self.uc.mem_read(self.SOURCE + 0x30, 2))[0] == -1
                    and counters['stops'] == 1, 'Native title fade did not finish after its timeout')
            self.call(0x16be48, self.SOURCE)
            require(counters['cleanup'] == 1 and not any(self.uc.mem_read(self.SOURCE + 0x48, 4)),
                    'Native title cleanup did not release/clear its replay object')
        finally:
            for hook in hooks:
                self.uc.hook_del(hook)
        return dict(stage=stage, **counters, warmup=20, active=1780, fade=20,
                    scope='Native title timeout/cleanup control with gameplay/decoder/audio bodies stubbed; '
                          'not an actual replay or physical-pad interruption test.')


def audit_title(elf):
    machine = TitleMachine(elf)
    rows = []
    for _, _, stage in demo.SLOTS:
        entry, raw = machine.title_request(stage)
        before = machine.random_calls
        initial, ticks, turn = machine.initialize_request(stage, raw)
        expected_stage = struct.unpack_from("<I", raw, 8 if stage >= 100 else 12)[0]
        require(expected_stage == stage and initial == 1 and ticks == 2,
                "Native title no-READY request/state differs")
        if stage < 100:
            require(struct.unpack_from("<I", raw, 16)[0] == 1, "Solo title demo uses another variant")
        else:
            require(turn == 0 and machine.random_calls == before,
                    "Title versus starting turn unexpectedly depends on random seed")
        rows.append(dict(stage=stage, entry=hex(entry), request_hex=raw.hex(),
                         initial_state=initial, setup_ticks=ticks, initial_turn=turn,
                         random_calls=machine.random_calls - before))
    return dict(source_sha256=hashlib.sha256(elf).hexdigest(), title_requests=rows,
                timeout=[machine.timeout(stage) for _, _, stage in demo.SLOTS],
                native_entry="0x16B520", native_initializers=["0x143278", "0x147088"],
                stubs=[hex(at) for at in machine.stubs],
                modeled=["0x1A87F8 EE memset"],
                scope="Original title request construction and UI initializers only. "
                      "Board/resource loading, input replay, rendered READY, gameplay and GPU not executed.")


def require_title_timing(results, title):
    policies = {row['stage']: row for row in title['title_requests']}
    for row in results:
        require(policies[row['stage']]['initial_state'] == 1
                and row['native_ready_frame'] == 0,
                f"Demo {row['stage']} replay proof did not use the title's no-READY entry")


def main():
    import wwrom
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_title(args.elf.read_bytes() if args.elf else wwrom.originals()[0])
    raw = json.dumps(result, ensure_ascii=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(raw, encoding="utf-8")
    print(raw)


if __name__ == "__main__":
    main()
