"""Regress real cursor state transitions and multi-frame retained tile drawing."""

import struct

from boot_safety_958 import LibcMachine, install_quad_spills
from mini_heap_native import HeapMachine
from unpack_disc import require
from verify_codecs import Native
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import (UC_MIPS_REG_0, UC_MIPS_REG_HI, UC_MIPS_REG_LO,
                               UC_MIPS_REG_PC, UC_MIPS_REG_RA)


class InteractionMachine(Native):
    STACK, STOP = LibcMachine.STACK, LibcMachine.STOP

    def __init__(self, elf):
        super().__init__(elf)
        install_quad_spills(self)
        self.draws = []
        self.commands = []
        self.uc.hook_add(UC_HOOK_CODE, self.capture, begin=0x18a598, end=0x18a598)
        self.uc.hook_add(UC_HOOK_CODE, self.capture, begin=0x12b020, end=0x12b020)
        for at in (0x12b7e8, 0x12b818):
            self.uc.hook_add(UC_HOOK_CODE, self.multiply, begin=at, end=at)

    def multiply(self, uc, at, size, user):
        word, = struct.unpack("<I", self.read(at, 4))
        require(word == (0x00821018 if at == 0x12b7e8 else 0x00621018),
                "Original cursor MULT instruction changed")
        values = [uc.reg_read(UC_MIPS_REG_0 + r) & 0xffffffff
                  for r in (word >> 21 & 31, word >> 16 & 31)]
        signed = lambda value: value if value < 0x80000000 else value - 0x100000000
        product = signed(values[0]) * signed(values[1])
        extend = lambda value: signed(value & 0xffffffff) & 0xffffffffffffffff
        uc.reg_write(UC_MIPS_REG_0 + (word >> 11 & 31), extend(product))
        uc.reg_write(UC_MIPS_REG_LO, extend(product))
        uc.reg_write(UC_MIPS_REG_HI, extend(product >> 32))
        uc.reg_write(UC_MIPS_REG_PC, at + 4)

    def capture(self, uc, at, size, user):
        if at == 0x12b020:
            self.commands.append(tuple(uc.reg_read(UC_MIPS_REG_0 + r) & 0xffffffff
                                       for r in (5, 6)))
            return
        pointer = uc.reg_read(UC_MIPS_REG_0 + 5) & 0xffffffff
        self.draws.append((pointer, bytes(uc.mem_read(pointer, 88))))
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    call = HeapMachine.call

    def put32(self, at, value):
        self.uc.mem_write(at, struct.pack("<I", value & 0xffffffff))

    def read(self, at, size):
        return bytes(self.uc.mem_read(at, size))

    def cursor(self, token, ticks=15):
        cursor = self.SOURCE
        self.uc.mem_write(cursor, bytes(0x100))
        self.call(0x11e170, cursor + 0x34, 0, 0)
        self.uc.reg_write(UC_MIPS_REG_0 + 8, 120)
        self.call(0x12b020, cursor, 3, token, 100)
        for frame in range(ticks):
            self.call(0x12b328, cursor)
            actual, = struct.unpack("<H", self.read(cursor + 0x34, 2))
            require(actual == token,
                    f"Cursor animation truncated token {token} to {actual} on frame {frame + 1}")
        return cursor

    def cursor_boundary(self, token, state):
        cursor = self.SOURCE
        self.uc.mem_write(cursor, bytes(0x100))
        self.call(0x11e170, cursor + 0x34, token, 0)
        for offset, value in ((0xac, state), (0xb0, 1), (0xb8, 100 << 8),
                              (0xbc, 120 << 8), (0xd0, 100), (0xd4, 120)):
            self.put32(cursor + offset, value)
        first = len(self.commands)
        self.call(0x12b328, cursor)
        require(len(self.commands) == first + 1 and self.commands[-1][1] == token,
                f"Cursor state {state} passed a truncated token {token}: {self.commands[first:]}")

    def retained(self, report, token, frames=3, size=1):
        records, primitives, piece = self.SOURCE + 0x1000, self.SOURCE + 0x2000, self.SOURCE
        capacity = max(4, size * size + 1)
        self.uc.mem_write(records, bytes(32 * capacity))
        self.uc.mem_write(primitives - 16, b"\xa5" * (88 * capacity + 32))
        for slot in range(capacity):
            self.put32(records + slot * 32 + 12, primitives + slot * 88)
        for at, value in ((0x224bd4, records), (0x224bd8, primitives), (0x224be0, capacity),
                          (0x224be4, 0), (0x224be8, -1)):
            self.put32(at, value)
        self.put32(report["patch"]["state"], 1)
        self.uc.mem_write(piece, bytes(0x6c))
        self.call(0x11e110, piece, token)
        self.uc.mem_write(piece + 4, bytes((size,)))
        self.uc.mem_write(piece + 8, struct.pack("<2I", 100, 120))
        self.uc.mem_write(piece + 0x14, struct.pack("<H", 47))
        self.call(0x11f790, piece)
        descriptor = self.SOURCE + 0x800
        self.uc.mem_write(descriptor, struct.pack("<2I", self.OUTPUT, 88 * 4))
        self.put32(0x224ab8, descriptor)
        count = self.read(piece + 0x17, 1)[0]
        slots = struct.unpack(f"<{count}H", self.read(piece + 0x18, count * 2))
        require(count == size * size + 1, "Retained tile did not register glyph/background")
        glyph_slot = slots[0]
        node = struct.unpack("<I", self.read(records + glyph_slot * 32 + 12, 4))[0]
        handle = self.read(node + 0x20, 4)
        expected = self.read(report["patch"]["font_glyphs"] + token * 16, 16)
        for frame in range(frames):
            self.put32(0x224abc, self.OUTPUT)
            self.put32(0x224ac0, 0)
            first = len(self.draws)
            self.call(0x120050, piece)
            produced = self.draws[first:]
            fonts = [(p, r) for p, r in produced if struct.unpack_from("<I", r, 12)[0] == 17]
            require(len(fonts) == 1 and fonts[0][1][0x28:0x38] == expected,
                    f"Retained tile {token} selected another/missing glyph on frame {frame + 1}")
            require(self.read(node + 12, 4) == struct.pack("<I", 12)
                    and self.read(node + 0x20, 4) == handle,
                    f"Retained SPRITE source was overwritten as FONT on frame {frame + 1}")
            require(fonts[0][0] != node, "Retained glyph queued its reusable source instead of a frame copy")
            require(struct.unpack_from("<2i2H", fonts[0][1], 0x1c)
                    == (100 - size * 16, 120 - size * 16, size * 32, size * 32),
                    "Retained glyph coordinates changed between frames")
            require(self.read(0x224abc, 8) == struct.pack("<2I", self.OUTPUT + 88, 1),
                    "Retained glyph did not advance exactly one original frame-cache slot")
        require(self.read(primitives - 16, 16) == b"\xa5" * 16
                and self.read(primitives + 88 * capacity, 16) == b"\xa5" * 16,
                "Retained primitive buffer crossed its bounds")
        return node

    def cache_limits(self, report):
        node = self.retained(report, 878, frames=1)
        glyph_slot = struct.unpack("<H", self.read(self.SOURCE + 0x18, 2))[0]
        before = self.read(node + 12, 76)
        for capacity, pointer in ((87, self.OUTPUT), (88, 0)):
            self.put32(self.SOURCE + 0x804, capacity)
            self.put32(0x224abc, pointer)
            first = len(self.draws)
            self.call(0x10fb98, glyph_slot)
            require(len(self.draws) == first and self.read(node + 12, 76) == before,
                    "Exhausted frame cache queued or corrupted a retained glyph")

    def multiple_retained(self, report):
        self.retained(report, 878, frames=1)
        slots = [struct.unpack("<H", self.read(self.SOURCE + 0x18, 2))[0]]
        tokens = [878, 453, 650]
        for token in tokens[1:]:
            slots.append(self.call(0x10fa38, (0x4000 + token) << 8, 100, 120, 46))
        require(all(slot < 4 for slot in slots), "Multiple retained glyph registration failed")
        for frame in range(20):
            self.put32(0x224abc, self.OUTPUT)
            self.put32(0x224ac0, 0)
            first = len(self.draws)
            for slot in slots:
                self.call(0x10fb98, slot)
            draws = self.draws[first:]
            require([p for p, raw in draws] == [self.OUTPUT + 88 * i for i in range(3)],
                    "Multiple retained glyphs alias the same frame-cache primitive")
            for (_, raw), token in zip(draws, tokens):
                expected = self.read(report["patch"]["font_glyphs"] + token * 16, 16)
                require(struct.unpack_from("<I", raw, 12)[0] == 17 and raw[0x28:0x38] == expected,
                        "Multiple retained glyphs change after another tile draws")
            require(self.read(0x224ac0, 4) == struct.pack("<I", 3),
                    "Multiple retained glyphs consumed the wrong number of cache slots")


def verify(elf, report):
    cursor = InteractionMachine(elf)
    for token in range(958):
        cursor.cursor(token)
        for state in (3, 5, 6, 7, 8):
            cursor.cursor_boundary(token, state)
    retained = InteractionMachine(elf)
    for token in range(958):
        retained.retained(report, token)
    for size in (2, 3, 4):
        for token in (0, 255, 256, 957):
            retained.retained(report, token, frames=5, size=size)
    retained.cache_limits(report)
    retained.multiple_retained(report)
    return {"cursor_tokens": 958, "cursor_frames_each": 15,
            "cursor_terminal_states": [3, 5, 6, 7, 8], "cursor_state_boundaries": 958 * 5,
            "retained_tokens": 958, "retained_frames_each": 3,
            "retained_frame_caller": 0x120050,
            "frame_cache_exhaustion_cases": 2,
            "simultaneous_glyphs": 3, "simultaneous_frames": 20,
            "scope": "Original cursor command/tick and retained board registration/draw; frame cache allocation native, R5900 MULT modeled, ordering insertion captured, no GPU."}


def reproduce(elf, report):
    observations = {}
    for name, check, expected in (
            ("cursor", lambda m: m.cursor(878), "Cursor animation truncated token 878 to 110 on frame 15"),
            ("retained", lambda m: m.retained(report, 878), "Retained SPRITE source was overwritten as FONT on frame 1")):
        try:
            check(InteractionMachine(elf))
        except ValueError as error:
            require(str(error) == expected, f"Previous candidate failed for an unrelated reason: {error}")
            observations[name] = str(error)
        else:
            raise ValueError(f"Previous {name} regression was not reproduced")
    return observations
