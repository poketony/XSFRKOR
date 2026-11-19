"""Lab MINI runtime: wide live letters/supply and existing-font tile drawing."""

import struct

from elf_probe import Elf
from mini_korean_runtime import Assembly, align, immediate as ins, jump, register as rr
from mini_condition_spacing import install as install_condition_spacing
from mini_random_personality import install as install_random_personality
from unpack_disc import require


SMALL, LARGE = 0x4000, 0x4400
N = 958
TILE_CLUT = 0x3d50
PREVIEW_PROFILE = 0x23c0c
DEMO_STAGES = (19, 25, 103, 105)
CURSOR_TILE_READS = (
    0x140508, 0x140920, 0x1409b8, 0x140a88, 0x140b88, 0x141654,
    0x144e98, 0x145f14, 0x145f54, 0x14603c, 0x146170, 0x1461ec, 0x1468ec,
    0x148504, 0x1485e4, 0x149844, 0x149b8c, 0x149bf8, 0x149cf0, 0x149d94,
    0x149f14, 0x149fb0, 0x14afa0,
)


def move(rd, rs):
    return rr(33, rd, rs)


def shift(rd, rt, amount, function=0):
    return rt << 16 | rd << 11 | amount << 6 | function


def save(a, registers, size):
    a.emit(ins(9, 29, 29, -size))
    for i, r in enumerate(registers):
        a.emit(ins(63, r, 29, i * 8))


def restore(a, registers, size):
    for i, r in enumerate(registers):
        a.emit(ins(55, r, 29, i * 8))
    a.emit(rr(8, 0, 31), ins(9, 29, 29, size))


class Image:
    def __init__(self, raw):
        self.original = raw
        self.elf = Elf(raw)
        self.raw = bytearray(raw)
        self.header = struct.unpack_from("<16sHHIIIIIHHHHHH", raw)
        self.phoff, self.phsize, self.phcount = self.header[5], self.header[9], self.header[10]
        require(self.phsize == 32 and self.phcount == 3, "Expected unextended MINI ELF")
        headers = [struct.unpack_from("<8I", raw, self.phoff + i * 32) for i in range(3)]
        self.base = align(max(p[2] + p[5] for p in headers if p[0] == 1), 4096)
        self.blob = bytearray(64)
        self.state = self.base
        self.changes = {}

    def append(self, data):
        self.blob.extend(b"\0" * (align(len(self.blob)) - len(self.blob)))
        address = self.base + len(self.blob)
        self.blob.extend(data)
        return address

    def words(self, at, count=1):
        return struct.unpack_from(f"<{count}I", self.original, self.elf.offset(at))

    def patch(self, at, words):
        require(not any(at + i * 4 in self.changes for i in range(len(words))), "Overlapping MINI patch")
        for i, w in enumerate(words):
            before = self.words(at + i * 4)[0]
            self.changes[at + i * 4] = (before, w)
        struct.pack_into(f"<{len(words)}I", self.raw, self.elf.offset(at), *words)

    def memory(self, at, old_op, new_op, old_offset=None, new_offset=None):
        w, = self.words(at)
        require(w >> 26 == old_op and (old_offset is None or w & 0xffff == old_offset & 0xffff),
                f"Unexpected memory instruction at {at:#x}")
        self.patch(at, [(w & 0x03ff0000) | new_op << 26 |
                        ((w if new_offset is None else new_offset) & 0xffff)])

    def immediate(self, at, before, after):
        w, = self.words(at)
        require(w & 0xffff == before & 0xffff, f"Unexpected immediate at {at:#x}")
        self.patch(at, [w & 0xffff0000 | after & 0xffff])

    def hook(self, at, code, count=2):
        target = self.append(code)
        self.patch(at, [jump(target), 0] + [0] * (count - 2))
        return target

    def finish(self):
        heap = align(self.base + len(self.blob), 64)
        require(heap < 0x600000, "Wide MINI patch consumes excessive game heap")
        # Keep the original BSS clear end: extending it would erase this segment.
        # Both kernel SetupHeap and libc's independent sbrk cursor must move.
        self.immediate(0x100180, 0x28, (heap + 0x8000) >> 16)
        self.immediate(0x100188, 0xd13c, heap)
        require(self.words(0x203d88)[0] == 0x27d13c, "Original libc break differs")
        self.patch(0x203d88, [heap])
        self.immediate(0x1078d4, 0x28, (heap + 0x8000) >> 16)
        self.immediate(0x1078e0, 0xd17c, heap)
        offset = align(len(self.raw), 4096)
        self.raw.extend(b"\0" * (offset - len(self.raw)))
        self.raw.extend(self.blob)
        slot = self.phoff + self.phcount * 32
        require(not any(self.original[slot:slot + 32]), "ELF extra program-header slot occupied")
        struct.pack_into("<8I", self.raw, slot, 1, offset, self.base, self.base,
                         len(self.blob), len(self.blob), 7, 4096)
        struct.pack_into("<H", self.raw, 44, 4)
        names = self.original[self.elf.sections[self.header[13]][4]:
                              self.elf.sections[self.header[13]][4] + self.elf.sections[self.header[13]][5]]
        name_at = len(names)
        names += b".kor958\0"
        names_offset = len(self.raw)
        self.raw.extend(names)
        self.raw.extend(b"\0" * (align(len(self.raw), 4) - len(self.raw)))
        section_offset = len(self.raw)
        for i, s in enumerate(self.elf.sections):
            row = list(s)
            if i == self.header[13]:
                row[4:6] = names_offset, len(names)
            self.raw.extend(struct.pack("<10I", *row))
        self.raw.extend(struct.pack("<10I", name_at, 1, 7, self.base, offset, len(self.blob), 0, 0, 16, 0))
        struct.pack_into("<I", self.raw, 32, section_offset)
        struct.pack_into("<H", self.raw, 48, self.header[12] + 1)
        return bytes(self.raw), {"segment_address": self.base, "segment_bytes": len(self.blob),
                                 "heap_start": heap, "system_heap_start": heap,
                                 "libc_initial_break": heap, "state": self.state,
                                 "instructions": {f"0x{k:X}": [f"0x{x:08X}" for x in v]
                                                  for k, v in sorted(self.changes.items())}}


def selector(image, table, stride=20, versus=False, demo_remaps=None):
    a = Assembly()
    if demo_remaps:
        a.address(24, image.state)
        a.emit(ins(35, 24, 24, 8))
        a.branch(4, 24, 0, "profile")
        a.emit(0)
        for source, copy in demo_remaps.items():
            a.emit(ins(9, 25, 0, source))
            a.branch(4, 25, 5, "absent")
            a.emit(ins(9, 24, 0, copy))
        a.branch(4, 0, 0, "absent")
        a.emit(move(24, 5))
        a.label("profile")
    if not versus:
        a.branch(5, 4, 0, "absent")
        a.emit(0)
    a.address(24, table)
    a.emit(ins(35, 25, 24, 0), ins(9, 24, 24, 4))
    a.label("loop")
    a.branch(4, 25, 0, "absent")
    a.emit(0, ins(35, 2, 24, 0))
    if versus:
        a.branch(5, 2, 5, "next")
        a.emit(0, ins(35, 2, 24, 24))
        a.branch(4, 2, 4, "found")
        a.emit(0)
        a.label("next")
    else:
        a.branch(4, 2, 5, "found")
        a.emit(0)
    a.emit(ins(9, 25, 25, -1))
    a.branch(4, 0, 0, "loop")
    a.emit(ins(9, 24, 24, stride))
    a.label("absent")
    if demo_remaps:
        a.address(25, image.state)
        a.emit(ins(35, 25, 25, 8))
        a.branch(4, 25, 0, "clear")
        a.emit(0, move(5, 24))
        a.label("clear")
    a.emit(move(24, 0))
    a.label("found")
    a.address(25, image.state)
    a.emit(ins(43, 24, 25, 0), ins(43, 0, 25, 4),
           *image.words(0x11cd28, 2), jump(0x11cd30), 0)
    image.hook(0x11cd28, a.finish())


def demo_entries(image):
    # Only the title's prerecorded input paths should load the original boards.
    for at, target, link in ((0x16b6d4, 0x143460, False), (0x16b834, 0x1472c0, True)):
        require(image.words(at)[0] == jump(target, link), "Original demo entry changed")
        a = Assembly()
        save(a, [16, 17, 31], 32)
        a.address(16, image.state)
        a.emit(ins(35, 17, 16, 8), ins(9, 24, 0, 1), jump(target, True),
               ins(43, 24, 16, 8), ins(43, 17, 16, 8))
        restore(a, [16, 17, 31], 32)
        image.patch(at, [jump(image.append(a.finish()), link)])


def profile_lifetime(image):
    # The original 11CD28 tail calls the board constructor. Remember the
    # returned board so freeing a different board cannot reset this profile.
    at = 0x11cd80
    require(image.words(at, 2) == (jump(0x11c9d8), ins(9, 29, 29, 0x20)),
            "Unexpected original board-constructor tail")
    a = Assembly()
    save(a, [31], 16)
    a.emit(jump(0x11c9d8, True), 0)
    a.address(24, image.state)
    a.emit(ins(35, 25, 24, 0))
    a.branch(4, 25, 0, "return")
    a.emit(0)
    a.branch(5, 2, 0, "return")
    a.emit(ins(43, 2, 24, 4), ins(43, 0, 24, 0))
    a.label("return")
    restore(a, [31], 16)
    image.patch(at, [jump(image.append(a.finish()))])

    at = 0x11cde0
    a = Assembly()
    a.address(24, image.state)
    a.emit(ins(35, 25, 24, 4))
    a.branch(4, 25, 0, "release")
    a.emit(0)
    a.branch(5, 25, 4, "release")
    a.emit(0, ins(43, 0, 24, 0), ins(43, 0, 24, 4))
    a.label("release")
    a.emit(*image.words(at, 2), jump(at + 8), 0)
    image.hook(at, a.finish())


def preview_profiles(image, table, stride):
    # Only the menu preview allocation gets a private profile pointer after
    # the original board. Its loader and draw run synchronously under that profile.
    image.immediate(0x14b3d4, PREVIEW_PROFILE & 0xffff, (PREVIEW_PROFILE + 4) & 0xffff)
    a = Assembly()
    save(a, [16, 17, 18, 31], 32)
    a.address(16, image.state)
    a.emit(ins(35, 17, 16, 0))
    a.address(24, table)
    a.emit(ins(35, 25, 24, 0), ins(9, 24, 24, 4))
    a.label("search")
    a.branch(4, 25, 0, "missing")
    a.emit(0, ins(35, 2, 24, 0))
    a.branch(4, 2, 5, "selected")
    a.emit(ins(9, 25, 25, -1))
    a.branch(4, 0, 0, "search")
    a.emit(ins(9, 24, 24, stride))
    a.label("missing")
    a.emit(move(24, 0))
    a.label("selected")
    a.address(18, PREVIEW_PROFILE)
    a.emit(rr(33, 18, 18, 4), ins(43, 24, 18, 0), ins(43, 24, 16, 0),
           jump(0x11d7f0, True), 0, ins(43, 17, 16, 0))
    restore(a, [16, 17, 18, 31], 32)
    require(image.words(0x14b420)[0] == jump(0x11d7f0, True), "Original preview loader call changed")
    image.patch(0x14b420, [jump(image.append(a.finish()), True)])

    for at, target, from_job in ((0x14b430, 0x122f20, False), (0x122f0c, 0x123f18, True)):
        a = Assembly()
        save(a, [16, 17, 31], 32)
        a.address(16, image.state)
        a.address(24, PREVIEW_PROFILE)
        a.emit(ins(35, 17, 16, 0), ins(35, 25, 4, 0x34) if from_job else move(25, 4),
               rr(33, 24, 24, 25), ins(35, 24, 24, 0), ins(43, 24, 16, 0),
               jump(target, True), 0, ins(43, 17, 16, 0))
        restore(a, [16, 17, 31], 32)
        require(image.words(at)[0] == jump(target, True), "Original preview graphics call changed")
        image.patch(at, [jump(image.append(a.finish()), True)])


def supply_loader(image):
    a = Assembly()
    registers = [16, 17, 18, 19, 20, 31]
    save(a, registers, 80)
    a.emit(move(16, 4), move(17, 5), move(5, 6), jump(0x11dda8, True), 0)
    a.emit(ins(9, 4, 0, 13), move(5, 17), ins(9, 6, 0, 4), ins(9, 7, 29, 48),
           jump(0x109eb8, True), 0, ins(37, 18, 29, 50), ins(36, 2, 29, 48),
           ins(36, 3, 29, 49), ins(40, 2, 16, 0), ins(40, 3, 16, 1), ins(41, 18, 16, 2))
    a.address(4, 0x215790)
    a.emit(ins(9, 5, 18, 1), shift(5, 5, 2), jump(0x185b98, True), 0)
    a.branch(4, 2, 0, "end")
    a.emit(0, ins(35, 19, 2, 0), ins(43, 2, 16, 0x14), ins(43, 19, 16, 8),
           ins(9, 3, 18, 1), shift(3, 3, 1), rr(33, 3, 3, 19), ins(43, 3, 16, 12))
    a.address(20, image.state)
    a.emit(ins(35, 20, 20, 0))
    a.branch(5, 20, 0, "korean")
    a.emit(0, ins(9, 4, 0, 13), ins(9, 5, 17, 4), move(6, 18), move(7, 19), jump(0x109eb8, True), 0,
           ins(9, 8, 18, -1))
    a.label("expand")
    a.branch(1, 8, 0, "sentinel")
    a.emit(0, rr(33, 9, 19, 8), ins(36, 10, 9, 0), shift(9, 8, 1), rr(33, 9, 19, 9),
           ins(41, 10, 9, 0), ins(9, 8, 8, -1))
    a.branch(4, 0, 0, "expand")
    a.emit(0)
    a.label("korean")
    a.emit(ins(35, 8, 20, 12), move(9, 19), move(10, 18))
    a.label("copy")
    a.branch(4, 10, 0, "sentinel")
    a.emit(0, ins(37, 11, 8, 0), ins(9, 8, 8, 2), ins(41, 11, 9, 0), ins(9, 9, 9, 2),
           ins(9, 10, 10, -1))
    a.branch(4, 0, 0, "copy")
    a.emit(0)
    a.label("sentinel")
    a.emit(shift(8, 18, 1), rr(33, 8, 19, 8), ins(9, 9, 0, -1), ins(41, 9, 8, 0))
    a.label("end")
    restore(a, registers, 80)
    image.hook(0x11d290, a.finish())


def wide_supply_operations(image):
    # The history record already has two bytes at +2; its six-byte stride stays unchanged.
    for at in (0x126b28,):
        image.memory(at, 36, 37)
    for at in (0x126b34, 0x126b50):
        image.memory(at, 40, 41)
    for at in (0x126b2c, 0x126b44):
        image.immediate(at, 1, 2)


def generated_supply(image):
    # Original generated/Japanese supplies still arrive as bytes. Normalize them
    # to the same u16 live arrays without changing their source strings.
    a = Assembly()
    regs = [16, 17, 18, 19, 20, 21, 31]
    save(a, regs, 64)
    a.emit(move(16, 4), move(17, 6), move(18, 7), move(19, 8), move(20, 9),
           jump(0x11dda8, True), 0, ins(40, 17, 16, 0), ins(40, 18, 16, 1),
           ins(41, 19, 16, 2), ins(9, 5, 19, 1), shift(5, 5, 2))
    a.address(4, 0x215790)
    a.emit(jump(0x185b98, True), 0)
    a.branch(4, 2, 0, "end")
    a.emit(0, ins(35, 21, 2, 0), ins(43, 2, 16, 0x14), ins(43, 21, 16, 8),
           ins(9, 8, 19, 1), shift(8, 8, 1), rr(33, 8, 21, 8), ins(43, 8, 16, 12),
           move(8, 0), move(9, 21))
    a.label("copy")
    a.branch(4, 8, 19, "sentinel")
    a.emit(0, ins(36, 10, 20, 0), ins(41, 10, 9, 0), ins(9, 20, 20, 1),
           ins(9, 9, 9, 2), ins(9, 8, 8, 1))
    a.branch(4, 0, 0, "copy")
    a.emit(0)
    a.label("sentinel")
    a.emit(ins(9, 10, 0, -1), ins(41, 10, 9, 0))
    a.label("end")
    restore(a, regs, 64)
    image.hook(0x11d368, a.finish())


def supply_api(image):
    a = Assembly()
    save(a, [16, 17, 31], 32)
    a.emit(move(16, 4), move(17, 5), jump(0x127f50, True), 0)
    a.branch(4, 2, 0, "invalid")
    a.emit(0, ins(35, 8, 16, 0x34), ins(9, 9, 0, 2), ins(43, 9, 8, 0x2d4),
           ins(43, 17, 8, 0x2dc), ins(36, 9, 8, 0), ins(9, 10, 0, 1))
    a.branch(4, 9, 10, "index")
    a.emit(0, ins(35, 9, 8, 0x10), rr(33, 17, 17, 9))
    a.label("index")
    a.emit(ins(35, 8, 8, 12), shift(9, 17, 1), rr(33, 8, 8, 9), ins(37, 2, 8, 0))
    a.branch(4, 0, 0, "end")
    a.emit(0)
    a.label("invalid")
    a.emit(ins(13, 2, 0, 0xffff))
    a.label("end")
    restore(a, [16, 17, 31], 32)
    image.hook(0x128050, a.finish())

    a = Assembly()
    a.emit(ins(35, 8, 4, 0x2d0), ins(9, 9, 4, 0x2d6))
    a.branch(4, 8, 9, "end")
    a.emit(0, ins(35, 9, 4, 12), shift(10, 5, 1), rr(33, 9, 9, 10), ins(37, 9, 9, 0),
           ins(41, 5, 8, 0), ins(41, 9, 8, 2), ins(36, 9, 4, 0))
    a.branch(5, 9, 0, "advance")
    a.emit(0, ins(37, 9, 4, 0x10), ins(41, 9, 8, 4))
    a.label("advance")
    a.emit(ins(9, 8, 8, 6), ins(43, 8, 4, 0x2d0))
    a.label("end")
    a.emit(rr(8, 0, 31), 0)
    image.hook(0x128450, a.finish())

    a = Assembly()
    save(a, [16, 17, 18, 31], 32)
    a.emit(ins(35, 16, 4, 0x34), ins(36, 18, 16, 0), ins(35, 17, 16, 0x2dc), ins(9, 8, 0, 1))
    a.branch(4, 18, 8, "index")
    a.emit(0, ins(35, 8, 16, 0x10), rr(33, 17, 17, 8))
    a.label("index")
    a.emit(ins(9, 8, 0, 2))
    a.branch(4, 18, 8, "state")
    a.emit(0, move(4, 16), move(5, 17), jump(0x128450, True), 0,
           ins(35, 8, 16, 12), shift(9, 17, 1), rr(33, 8, 8, 9))
    a.label("shift")
    a.emit(ins(37, 9, 8, 2), ins(41, 9, 8, 0), ins(13, 10, 0, 0xffff), ins(9, 8, 8, 2))
    a.branch(5, 9, 10, "shift")
    a.emit(0, ins(37, 8, 16, 4), ins(9, 8, 8, -1), ins(41, 8, 16, 4))
    a.label("state")
    a.emit(ins(9, 8, 0, 1), ins(43, 8, 16, 0x2d4))
    a.branch(4, 18, 8, "end")
    a.emit(0, ins(37, 8, 16, 4), ins(35, 9, 16, 0x10), rr(35, 8, 8, 9), ins(10, 8, 8, 8))
    a.branch(4, 8, 0, "end")
    a.emit(0)
    a.branch(6, 9, 0, "end")
    a.emit(0, ins(9, 9, 9, -1), ins(43, 9, 16, 0x10))
    a.label("end")
    restore(a, [16, 17, 18, 31], 32)
    image.hook(0x1281e8, a.finish())

    a = Assembly()
    a.emit(ins(35, 8, 4, 0x34), ins(35, 9, 8, 0x2d0), ins(9, 10, 8, 0x18))
    a.branch(4, 9, 10, "empty")
    a.emit(ins(9, 2, 0, -1), ins(9, 9, 9, -6), ins(43, 9, 8, 0x2d0),
           ins(37, 10, 9, 0), ins(37, 11, 8, 4), ins(35, 12, 8, 12))
    a.label("shift")
    a.emit(rr(42, 13, 10, 11))
    a.branch(4, 13, 0, "restore")
    a.emit(0, shift(13, 11, 1), rr(33, 13, 12, 13), ins(37, 14, 13, -2), ins(41, 14, 13, 0),
           ins(9, 11, 11, -1))
    a.branch(4, 0, 0, "shift")
    a.emit(0)
    a.label("restore")
    a.emit(shift(13, 10, 1), rr(33, 13, 12, 13), ins(37, 14, 9, 2), ins(41, 14, 13, 0),
           ins(37, 11, 8, 4), ins(9, 11, 11, 1), ins(41, 11, 8, 4), ins(36, 11, 8, 0))
    a.branch(5, 11, 0, "result")
    a.emit(0, ins(33, 11, 9, 4), ins(43, 11, 8, 0x10))
    a.label("result")
    a.emit(ins(37, 11, 8, 4), shift(11, 11, 1), rr(33, 11, 11, 12), ins(9, 13, 0, -1),
           ins(41, 13, 11, 0), ins(35, 11, 8, 0x10), rr(35, 2, 10, 11))
    a.label("empty")
    a.emit(rr(8, 0, 31), 0)
    image.hook(0x128388, a.finish())


def supply_display(image):
    # Keep the original clipping, all six states, highlight offsets and drawing
    # calls. Only widen the token accesses and byte-array address arithmetic.
    for at in (0x127794, 0x1277ec, 0x1278f4, 0x1279c4, 0x1279f0, 0x127a44,
               0x127a6c, 0x127aec, 0x127c00, 0x127ce0, 0x127cf8, 0x127d3c,
               0x127d58, 0x127dec):
        image.memory(at, 36, 37)
    for at in (0x127940, 0x127ab4, 0x127c38, 0x127d80, 0x127df8):
        image.memory(at, 40, 41)
    for at in (0x1277d8, 0x1279b8, 0x127a34, 0x127adc, 0x127cd4, 0x127d2c, 0x127ddc):
        image.immediate(at, 1, 2)
    for at in (0x12776c, 0x127850):
        image.immediate(at, -1, -2)
    for at in (0x127798, 0x1277a4, 0x1278f8, 0x1279c8, 0x1279ec, 0x1279fc,
               0x127a70, 0x127af0, 0x127bfc, 0x127ce4, 0x127cfc, 0x127d08,
               0x127d54, 0x127df0):
        word, = image.words(at)
        require(word >> 26 == 9 and word >> 21 & 31 == 0 and word & 0xffff == 255,
                "Supply display terminator differs")
        image.patch(at, [ins(13, word >> 16 & 31, 0, 0xffff)])

    a = Assembly()
    a.emit(*image.words(0x127758, 2), shift(6, 5, 1), rr(33, 2, 2, 6), jump(0x127764), 0)
    image.hook(0x127758, a.finish(), 3)
    a = Assembly()
    a.emit(*image.words(0x12783c, 2), shift(20, 4, 1), rr(33, 2, 2, 20), jump(0x127848), 0)
    image.hook(0x12783c, a.finish(), 3)


def piece_fields(image, versus=False):
    for start, register, value in ((0x11e110, 4, 5), (0x11e138, 5, 6),
                                   (0x11e170, 4, 5), (0x11e198, 4, 5)):
        a = Assembly()
        count = 3 if start == 0x11e198 else 2
        old = list(image.words(start, count))
        changed = False
        for i, word in enumerate(old):
            if (word >> 26 == 40 and word >> 21 & 31 == register
                    and word >> 16 & 31 == value and word & 0xffff == 0):
                old[i] = word & 0x03ffffff | 41 << 26
                changed = True
        require(changed, "Piece initializer does not store the expected token")
        a.emit(ins(40, 0, register, 7), *old, jump(start + count * 4), 0)
        image.hook(start, a.finish(), count)
    for at in (0x11f484, 0x11f4d4, 0x11f56c, 0x11f5c0, 0x11f690, 0x11f6e8,
               0x11f818, 0x11f86c, 0x11fbd0, 0x11fbfc, 0x121304):
        image.memory(at, 36, 37, 0, 0)
    for at in (0x11f834, 0x11fbec):
        image.memory(at, 36, 36, 1, 7)
    # ID 0x45 means a hidden letter only on the original kana path. The
    # preview-only wide marker has its own table entry and cannot be playable.
    for at, target, delay in ((0x11f828, 0x11f864, ins(36, 3, 16, 4)),
                              (0x11fbe0, 0x11fc00, ins(37, 2, 16, 0))):
        first, _, _ = image.words(at, 3)
        require(first == ins(9, 2, 0, 0x45), "Original preview marker compare changed")
        a = Assembly()
        a.address(2, image.state)
        a.emit(ins(35, 2, 2, 0))
        a.branch(5, 2, 0, "letter")
        a.emit(first)
        a.branch(5, 3, 2, "letter")
        a.emit(0, jump(at + 12), 0)
        a.label("letter")
        a.emit(jump(target), delay)
        image.hook(at, a.finish(), 3)
    for at in (0x11f4d8, 0x11f530, 0x11f5c4, 0x11f628, 0x11f69c, 0x11f6f8,
               0x11f81c, 0x11fbd4):
        image.immediate(at, 72, N + int(versus))
    for at in (0x12b13c, 0x12b1a0):
        image.memory(at, 40, 41, 0, 0)
    for at in (0x12b694, 0x12b83c, 0x12b8fc, 0x12b9b4, 0x12ba6c):
        image.memory(at, 36, 37, 0x34, 0x34)
    image.memory(0x135c48, 36, 37, 0, 0)
    image.memory(0x135c50, 40, 41, 0x34, 0x34)
    a = Assembly()
    a.emit(ins(40, 0, 11, 0x3b), *image.words(0x135c54, 2), jump(0x135c5c), 0)
    image.hook(0x135c54, a.finish())
    # Controller transitions pass the full picked-up letter back to the cursor animation.
    for at in CURSOR_TILE_READS:
        require(image.words(at - 4, 2) == (jump(0x12b020, True), ins(36, 6, 2, 0)),
                "Original cursor transfer changed")
        image.memory(at, 36, 37, 0, 0)


def preplaced(image, versus=False):
    a = Assembly()
    # v1's input was consumed at 11D770. v0 becomes dead when the obsolete
    # padding write below is removed. t1 already holds the source Y coordinate.
    a.emit(ins(36, 2, 29, 0xf1), ins(40, 2, 29, 0xf7), ins(40, 0, 29, 0xf1))
    a.address(2, image.state)
    a.emit(ins(35, 2, 2, 0))
    a.branch(4, 2, 0, "end")
    a.emit(0, ins(35, 2, 2, 8), ins(36, 3, 29, 0x61), shift(3, 3, 4),
           rr(33, 3, 3, 9), shift(3, 3, 1), rr(33, 2, 2, 3), ins(37, 2, 2, 0))
    if versus:
        a.emit(ins(13, 3, 0, 0xffff))
        a.branch(5, 2, 3, "store")
        a.emit(0)
        # The preview loads in mode zero, so the random marker has not been drawn.
        a.emit(ins(35, 3, 29, 0x164))
        a.branch(4, 3, 0, "marker")
        a.emit(0)
        # The original 0x45 marker has already gone through the weighted RNG.
        a.address(2, image.state)
        a.emit(ins(35, 2, 2, 0), ins(35, 2, 2, 28), ins(36, 3, 29, 0xf0), shift(3, 3, 1),
               rr(33, 2, 2, 3), ins(37, 2, 2, 0))
        a.branch(4, 0, 0, "store")
        a.emit(0)
        a.label("marker")
        a.emit(ins(9, 2, 0, N))
        a.label("store")
    a.emit(ins(41, 2, 29, 0xf0))
    a.label("end")
    a.emit(*image.words(0x11d778, 2), jump(0x11d780), 0)
    image.hook(0x11d778, a.finish())
    # All parsed PBF piece padding is zero. Do not let its original delay-slot
    # store erase the special flag moved to +7. Keep the enclosing JAL intact.
    require(image.words(0x11d78c)[0] == ins(40, 2, 29, 0xf7), "Preplaced padding store differs")
    image.patch(0x11d78c, [0])


def index_lookup():
    # Sorted fixed 24-byte records: metadata, length, nine u16 letters.
    a = Assembly()
    a.emit(ins(35, 8, 6, 0), ins(9, 6, 6, 4), move(9, 0))
    a.label("search")
    a.emit(rr(42, 10, 9, 8))
    a.branch(4, 10, 0, "absent")
    a.emit(0, rr(33, 10, 8, 9), shift(10, 10, 1, 2), shift(11, 10, 1),
           rr(33, 11, 11, 10), shift(11, 11, 3), rr(33, 11, 11, 6),
           ins(37, 12, 11, 4), rr(35, 12, 5, 12))
    a.branch(5, 12, 0, "different")
    a.emit(0, move(13, 0), ins(9, 14, 11, 6), move(15, 4))
    a.label("letters")
    a.emit(ins(37, 12, 15, 0), ins(37, 24, 14, 0), rr(35, 12, 12, 24))
    a.branch(5, 12, 0, "different")
    a.emit(0, ins(9, 13, 13, 1), ins(9, 14, 14, 2), ins(9, 15, 15, 2))
    a.branch(5, 13, 5, "letters")
    a.emit(0, rr(8, 0, 31), move(2, 11))
    a.label("different")
    a.branch(1, 12, 0, "lower")
    a.emit(0, ins(9, 9, 10, 1))
    a.branch(4, 0, 0, "search")
    a.emit(0)
    a.label("lower")
    a.emit(move(8, 10))
    a.branch(4, 0, 0, "search")
    a.emit(0)
    a.label("absent")
    a.emit(rr(8, 0, 31), move(2, 0))
    return a.finish()


def judge(image, lookup):
    a = Assembly()
    a.address(24, image.state)
    a.emit(ins(35, 24, 24, 0))
    a.branch(5, 24, 0, "korean")
    a.emit(0, *image.words(0x123f98, 2), jump(0x123fa0), 0)
    a.label("korean")
    regs = [16, 17, 18, 19, 20, 21, 22, 31]
    save(a, regs, 96)
    a.emit(move(18, 5), move(22, 24), jump(0x124098, True), 0, move(16, 2), move(21, 0))
    a.branch(4, 16, 0, "end")
    a.emit(0)
    a.address(8, 0xe000)
    a.emit(rr(33, 8, 8, 16), ins(35, 20, 8, 0), move(17, 16))
    a.label("row")
    a.branch(4, 20, 0, "done")
    a.emit(0, ins(35, 19, 17, 0x24), move(8, 0), move(9, 17), ins(9, 10, 29, 64))
    a.label("letter")
    a.emit(ins(35, 11, 9, 0), ins(35, 11, 11, 0x14), move(12, 18))
    a.branch(4, 11, 0, "store")
    a.emit(0, ins(37, 12, 11, 0))
    a.label("store")
    a.emit(ins(41, 12, 10, 0), ins(9, 8, 8, 1), ins(9, 9, 9, 4), ins(9, 10, 10, 2))
    a.branch(5, 8, 19, "letter")
    a.emit(0, ins(9, 4, 29, 64), move(5, 19), ins(35, 6, 22, 4), jump(lookup, True), 0,
           ins(43, 2, 17, 0x28), ins(9, 8, 21, 1), rr(11, 21, 8, 2),
           ins(9, 20, 20, -1), ins(9, 17, 17, 56))
    a.branch(4, 0, 0, "row")
    a.emit(0)
    a.label("done")
    a.branch(5, 21, 0, "end")
    a.emit(0, move(4, 16), jump(0x124468, True), 0, move(16, 0))
    a.label("end")
    a.emit(move(2, 16))
    restore(a, regs, 96)
    image.hook(0x123f98, a.finish())


def goal_judge(image):
    # The word event at controller +0x970 follows the current query pointer.
    # Query +0x28 points to the accepted wide dictionary record, not the
    # truncated letters copied into the event for the original byte counter.
    a = Assembly()
    a.address(24, image.state)
    a.emit(ins(35, 24, 24, 0))
    a.branch(4, 24, 0, "original")
    a.emit(0, ins(35, 25, 24, 20))
    a.branch(4, 25, 0, "original")
    a.emit(0)
    a.address(8, 0x272bb4)
    a.emit(ins(35, 8, 8, 0), ins(35, 9, 8, 0x34), ins(35, 10, 9, 0), ins(9, 11, 0, 9))
    a.branch(4, 10, 11, "special")
    a.emit(ins(9, 11, 0, 10))
    a.branch(5, 10, 11, "original")
    a.emit(0)
    a.label("special")
    a.emit(ins(35, 2, 4, -4))
    a.branch(4, 2, 0, "reject")
    a.emit(0, ins(35, 3, 2, 0x28), ins(35, 12, 24, 4), ins(35, 13, 12, 0),
           ins(9, 12, 12, 4), rr(43, 14, 3, 12))
    a.branch(5, 14, 0, "reject")
    a.emit(0, shift(14, 13, 1), rr(33, 13, 14, 13), shift(13, 13, 3),
           rr(33, 13, 12, 13), ins(9, 13, 13, -23), rr(43, 14, 3, 13))
    a.branch(4, 14, 0, "reject")
    a.emit(0, ins(37, 12, 3, 4), ins(35, 13, 2, 0x24))
    a.branch(5, 12, 13, "reject")
    a.emit(0, ins(11, 14, 12, 2))
    a.branch(5, 14, 0, "reject")
    a.emit(0, ins(11, 14, 12, 10))
    a.branch(4, 14, 0, "reject")
    a.emit(ins(9, 3, 3, 6), ins(35, 13, 9, 0xc))
    a.branch(4, 10, 11, "contains")
    a.emit(0)
    a.branch(5, 12, 13, "reject")
    a.emit(ins(9, 9, 9, 16))
    a.label("word_loop")
    a.emit(ins(36, 13, 9, 0), ins(11, 14, 13, 72))
    a.branch(4, 14, 0, "reject")
    a.emit(0, shift(13, 13, 1), rr(33, 13, 25, 13), ins(37, 13, 13, 0), ins(37, 14, 3, 0))
    a.branch(5, 13, 14, "reject")
    a.emit(0, ins(9, 3, 3, 2), ins(9, 9, 9, 1), ins(9, 12, 12, -1))
    a.branch(5, 12, 0, "word_loop")
    a.emit(0, jump(0x12c230), move(4, 8))
    a.label("contains")
    a.emit(ins(11, 14, 13, 72))
    a.branch(4, 14, 0, "reject")
    a.emit(0, shift(13, 13, 1), rr(33, 13, 25, 13), ins(37, 13, 13, 0))
    a.label("letter_loop")
    a.emit(ins(37, 14, 3, 0))
    a.branch(4, 13, 14, "matched")
    a.emit(ins(9, 3, 3, 2), ins(9, 12, 12, -1))
    a.branch(5, 12, 0, "letter_loop")
    a.emit(0)
    a.label("reject")
    a.emit(rr(8, 0, 31), move(2, 0))
    a.label("matched")
    a.emit(jump(0x12c230), move(4, 8))
    a.label("original")
    a.emit(*image.words(0x146978, 2), jump(0x146980), 0)
    return image.hook(0x146978, a.finish())


def goal_glyphs(image, table, stride):
    # All original goal letter sprites are 32x32. Leave their sizing and
    # background draw alone; substitute only the foreground glyph handle.
    for at, kind, scratch, source, offset in ((0x12d278, 9, 6, 18, 0), (0x12d3ec, 10, 8, 23, 12)):
        first, second = image.words(at, 2)
        require(first == ins(35, 4, 3, 0) and second == ins(13, 3 if kind == 9 else 8, 0, 0xffff),
                "Unexpected original goal glyph load")
        a = Assembly()
        a.emit(first, ins(35, 3, 23, 0), ins(9, scratch, 0, kind))
        a.branch(5, 3, scratch, "resume")
        a.emit(0)
        # Menu condition jobs outlive the preview's scoped current profile.
        # Both menus own solo goals. The second menu's fixed variant 1 is a
        # difficulty variant, not the runtime's mode 1 (versus).
        a.emit(ins(9, 29, 29, -32), ins(31, 24, 29, 0), ins(31, 25, 29, 16))
        a.address(3, image.state)
        a.emit(ins(35, 3, 3, 0))
        for menu, goals, stages in ((0, 0x273838, 0x273800), (1, 0x273d70, 0x273d48)):
            for slot in range(4):
                a.address(24, goals + slot * 28)
                a.branch(5, 24, 23, f"menu_{menu}_{slot}_next")
                a.emit(0)
                a.address(24, stages + slot * 4)
                a.emit(ins(35, 24, 24, 0))
                a.address(25, table + 4)
                a.address(3, table)
                a.emit(ins(35, 3, 3, 0))
                a.label(f"menu_{menu}_{slot}_search")
                a.branch(4, 3, 0, "menu_missing")
                a.emit(0, ins(35, scratch, 25, 0))
                a.branch(5, scratch, 24, f"menu_{menu}_{slot}_advance")
                a.emit(0)
                if stride == 32:
                    a.emit(ins(35, scratch, 25, 24))
                    a.branch(5, scratch, 0, f"menu_{menu}_{slot}_advance")
                    a.emit(0)
                a.branch(4, 0, 0, "menu_found")
                a.emit(move(3, 25))
                a.label(f"menu_{menu}_{slot}_advance")
                a.emit(ins(9, 3, 3, -1))
                a.branch(4, 0, 0, f"menu_{menu}_{slot}_search")
                a.emit(ins(9, 25, 25, stride))
                a.label(f"menu_{menu}_{slot}_next")
        a.branch(4, 0, 0, "menu_found")
        a.emit(0)
        a.label("menu_missing")
        a.emit(move(3, 0))
        a.label("menu_found")
        a.emit(ins(30, 24, 29, 0), ins(30, 25, 29, 16), ins(9, 29, 29, 32))
        a.branch(4, 3, 0, "resume")
        a.emit(0, ins(35, 3, 3, 20))
        a.branch(4, 3, 0, "resume")
        a.emit(0, ins(36 if kind == 9 else 35, scratch, source, offset), shift(scratch, scratch, 1),
               rr(33, 3, 3, scratch), ins(37, 3, 3, 0),
               ins(9, 3, 3, SMALL), shift(4, 3, 8))
        a.label("resume")
        a.emit(jump(at + 8), second)
        image.hook(at, a.finish())


def handle_tables(image, small, large):
    for at, table in ((0x11f498, small), (0x11f874, small), (0x11f580, large),
                      (0x11f6a0, large), (0x11f88c, large), (0x11fc04, large),
                      (0x17a30c, small)):
        old = image.words(at, 3)
        require(old[0] >> 26 == 15 and old[1] & 63 == 33 and old[2] >> 26 == 35,
                "Unexpected piece handle-table sequence")
        base = old[0] >> 16 & 31
        offset = old[1] >> 16 & 31
        destination = old[2] >> 16 & 31
        require(old[1] >> 21 & 31 == base and old[2] >> 21 & 31 == base,
                "Handle table does not use its expected base")
        require(base != offset, "Handle table scratch register aliases its offset")
        a = Assembly()
        a.address(base, image.state)
        a.emit(ins(35, base, base, 0))
        a.branch(4, base, 0, "original")
        a.emit(0)
        a.address(base, table)
        a.emit(rr(33, base, base, offset), ins(35, destination, base, 0), jump(at + 12), 0)
        a.label("original")
        a.emit(*old, jump(at + 12), 0)
        image.hook(at, a.finish(), 3)


def font_drawing(image, glyphs):
    # A virtual handle is never dereferenced as a sprite. Convert its queued primitive
    # in place to the game's own FONT primitive before the ordering-table consumer.
    a = Assembly()
    # This leaf is shared by ordinary UI draws. Preserve registers that its
    # original implementation never changed, including the EE's upper 64 bits.
    extra = (*range(9, 16), 24, 25)
    frame = len(extra) * 16
    a.emit(ins(9, 29, 29, -frame))
    for i, register in enumerate(extra):
        a.emit(ins(31, register, 29, i * 16))
    a.emit(ins(35, 24, 5, 12), ins(9, 25, 0, 12))
    a.branch(5, 24, 25, "original")
    a.emit(0, ins(35, 24, 5, 0x20), shift(25, 24, 8, 2), ins(9, 25, 25, -SMALL),
           ins(11, 8, 25, 2048))
    a.branch(4, 8, 0, "original")
    a.emit(0, ins(12, 8, 25, 1023), ins(11, 9, 8, N))
    a.branch(4, 9, 0, "original")
    a.emit(0, shift(9, 25, 10, 2), ins(9, 10, 0, 32), ins(9, 11, 0, 48),
           rr(11, 10, 11, 9), move(11, 10), ins(35, 12, 5, 0x44), ins(12, 12, 12, 1))
    a.branch(4, 12, 0, "scale")
    a.emit(0, ins(37, 10, 5, 0x2c), ins(37, 11, 5, 0x2e))
    a.label("scale")
    a.emit(ins(35, 12, 5, 0x38))
    a.branch(4, 12, 0, "position")
    a.emit(0)
    # Original scale arguments are IEEE singles, not integer pixel counts.
    a.emit(0x448a0000, 0x46800020, 0x448c0800, 0x46010002, 0x46000024, 0x440a0000,
           0x448b0000, 0x46800020, 0x46010002, 0x46000024, 0x440b0000)
    a.label("position")
    a.emit(ins(35, 12, 5, 0x24), ins(35, 13, 5, 0x28), shift(14, 10, 1, 2),
           shift(15, 11, 1, 2), rr(35, 12, 12, 14), rr(35, 13, 13, 15))
    a.address(14, glyphs)
    a.emit(shift(8, 8, 4), rr(33, 14, 14, 8), ins(55, 8, 14, 0), ins(55, 14, 14, 8),
           ins(9, 15, 0, 17), ins(43, 15, 5, 12), ins(43, 12, 5, 0x1c), ins(43, 13, 5, 0x20),
           ins(41, 10, 5, 0x24), ins(41, 11, 5, 0x26), ins(63, 8, 5, 0x28),
           ins(63, 14, 5, 0x30), ins(9, 8, 0, 0), ins(43, 8, 5, 0x38))
    a.label("original")
    for i, register in enumerate(extra):
        a.emit(ins(30, register, 29, i * 16))
    a.emit(ins(9, 29, 29, frame))
    a.emit(*image.words(0x18a590, 2), jump(0x18a598), 0)
    draw = image.hook(0x18a590, a.finish())
    # Size requests from clipping/scaling paths must not index the original 2,060-row table.
    a = Assembly()
    a.emit(shift(3, 4, 8, 2), ins(9, 3, 3, -SMALL), ins(11, 2, 3, 2048))
    a.branch(4, 2, 0, "original")
    a.emit(0, shift(3, 3, 10, 2))
    a.branch(4, 3, 0, "small")
    a.emit(ins(9, 2, 0, 32), ins(9, 2, 0, 60))
    a.label("small")
    a.emit(ins(43, 2, 7, 0), ins(43, 2, 6, 0), rr(8, 0, 31), ins(9, 2, 0, -1))
    a.label("original")
    a.emit(*image.words(0x10bf00, 2), jump(0x10bf08), 0)
    dims = image.hook(0x10bf00, a.finish())
    return {"font_primitive_hook": draw, "virtual_dimensions_hook": dims,
            "virtual_small_sprite_base": SMALL, "virtual_large_sprite_base": LARGE,
            "scope": "Existing font pages; no extra tile textures. Palette/rotation visual checks need PCSX2."}


def retained_font_drawing(image):
    # Both the normal and the scaled retained draw reuse their SPRITE record.
    # Convert a frame-cache copy, never the record used on the next frame.
    a = Assembly()
    a.emit(ins(35, 2, 5, 12), ins(9, 3, 0, 12))
    a.branch(5, 2, 3, "original")
    a.emit(0, ins(35, 2, 5, 0x20), shift(2, 2, 8, 2), ins(9, 2, 2, -SMALL),
           ins(11, 3, 2, 2048))
    a.branch(4, 3, 0, "original")
    a.emit(0, ins(12, 2, 2, 1023), ins(11, 3, 2, N))
    a.branch(4, 3, 0, "original")
    a.emit(0)
    save(a, [16, 17, 18, 31], 32)
    a.emit(move(16, 4), move(17, 5))
    a.address(2, 0x224ab8)
    a.emit(ins(35, 2, 2, 0))
    a.branch(4, 2, 0, "end")
    a.emit(0, ins(35, 3, 2, 4), ins(35, 2, 2, 0), rr(33, 18, 2, 3),
           jump(0x10c630, True), ins(9, 4, 0, 2))
    a.branch(4, 2, 0, "end")
    a.emit(0, ins(9, 3, 2, 88), rr(43, 3, 18, 3))
    a.branch(5, 3, 0, "end")
    a.emit(0)
    for offset in range(0, 88, 8):
        a.emit(ins(55, 3, 17, offset), ins(63, 3, 2, offset))
    a.emit(move(4, 16), move(5, 2), jump(0x18a590, True), 0,
           jump(0x10c680, True), ins(9, 4, 0, 2))
    a.label("end")
    restore(a, [16, 17, 18, 31], 32)
    a.label("original")
    a.emit(jump(0x18a590), 0)
    target = image.append(a.finish())
    for at in (0x10fc10, 0x1108ec):
        require(image.words(at)[0] == jump(0x18a590, True), "Retained draw call differs")
        image.patch(at, [jump(target, True)])
    return target


def tile_palette(image):
    # Font CLUTs occupy 3D40..3D4F; ordinary sprite groups start at 3D60.
    # Keep both packed two-bit planes in separate, tile-only CLUT blocks.
    ink = image.original[image.elf.offset(0x1caee0):image.elf.offset(0x1caee0) + 3]
    require(ink == bytes((8, 16, 59)), "Original dark tile ink changed")
    color = int.from_bytes(ink, "little")
    levels = (0, 43, 85, 128)
    palettes = image.append(b"".join(struct.pack("<I", color | levels[(i >> (plane * 2)) & 3] << 24)
                                     for plane in range(2) for i in range(16)))
    a = Assembly()
    save(a, [16, 31], 16)
    a.emit(jump(0x114480, True), 0, move(16, 2))
    a.branch(5, 16, 0, "end")
    a.emit(0)
    for plane in range(2):
        a.address(4, palettes + plane * 64)
        a.emit(ins(9, 5, 0, TILE_CLUT + plane), move(6, 0), jump(0x10ec10, True), move(7, 0))
    a.label("end")
    a.emit(move(2, 16))
    restore(a, [16, 31], 16)
    loader = image.append(a.finish())
    require(image.words(0x1137f0)[0] == jump(0x114480, True), "Original font startup call changed")
    image.patch(0x1137f0, [jump(loader, True)])
    return {"loader": loader, "palette_data": palettes,
            "clut_addresses": [TILE_CLUT, TILE_CLUT + 1], "ink_rgb": list(ink),
            "alpha_levels": list(levels), "texture_filter": "nearest",
            "scope": "Tile-only CLUTs in unused font-bank blocks; shared font image/palettes unchanged."}


def versus_help_tiles(image, alphabet):
    if not {"단", "어"} <= set(alphabet):
        return None
    tokens = [alphabet.index(c) for c in "단어단어"]
    require(image.words(0x21c858, 4) == (59, 18, 59, 18), "Original help tile letters differ")
    image.patch(0x21c858, tokens)
    image.memory(0x1801a0, 36, 37, 0, 0)
    a = Assembly()
    a.emit(ins(40, 7, 3, 4), ins(41, 2, 3, 0), ins(40, 0, 3, 7), jump(0x1801b4), 0)
    require(image.words(0x1801ac, 2) == (ins(40, 7, 3, 4), ins(40, 2, 3, 0)),
            "Original help tile initializer differs")
    image.hook(0x1801ac, a.finish())
    a = Assembly()
    save(a, [16, 17, 31], 32)
    a.address(16, image.state)
    a.emit(ins(35, 17, 16, 0), ins(43, 16, 16, 0), jump(0x11f178, True), 0,
           ins(43, 17, 16, 0))
    restore(a, [16, 17, 31], 32)
    require(image.words(0x180010)[0] == jump(0x11f178, True), "Original help tile draw differs")
    draw = image.append(a.finish())
    image.patch(0x180010, [jump(draw, True)])
    return {"text": "단어", "tokens": tokens, "draw": draw,
            "scope": "Four held example tiles on the versus-mode explanation screen only"}


def definition_leading_spaces(image):
    require(image.words(0x113e70, 2) == (jump(0x10c630, True), ins(9, 4, 0, 2)),
            "Original ASCII glyph allocation differs")
    require(image.words(0x1165f0, 2) == (jump(0x113bf0, True), ins(9, 8, 0, 1))
            and image.words(0x113c2c)[0] == ins(63, 31, 29, 0x78),
            "Original dictionary caller/frame differs")
    require(image.words(0x126714, 2) == (jump(0x113bf0), ins(9, 29, 29, 0x10)),
            "Original word-popup definition caller differs")
    # The match popup tail-calls the renderer; give only its body a known return address.
    popup = Assembly()
    save(popup, [31], 16)
    popup.emit(jump(0x113bf0, True), 0)
    popup_return_offset = len(popup.words) * 4
    restore(popup, [31], 16)
    popup_wrapper = image.append(popup.finish())
    image.patch(0x126714, [jump(popup_wrapper)])
    a = Assembly()
    # Only the two definition callers, not headings or other text boxes.
    a.emit(ins(35, 2, 29, 0x78))
    a.address(3, 0x1165f8)
    a.branch(4, 2, 3, "definition")
    a.emit(0)
    a.address(3, popup_wrapper + popup_return_offset)
    a.branch(5, 2, 3, "original")
    a.emit(0)
    a.label("definition")
    a.emit(ins(37, 2, 20, 0), ins(9, 3, 0, 0x1d60))
    a.branch(5, 2, 3, "original")
    a.emit(0, ins(9, 2, 0, 1))
    a.branch(5, 22, 2, "original")
    a.emit(0, ins(33, 2, 23, 0))
    a.branch(5, 19, 2, "original")
    a.emit(0, ins(33, 2, 23, 2), ins(35, 3, 17, 0x310), rr(33, 2, 2, 3))
    a.branch(5, 21, 2, "original")
    a.emit(0, jump(0x113f98), 0)
    a.label("original")
    a.emit(jump(0x10c630), 0)
    target = image.append(a.finish())
    image.patch(0x113e70, [jump(target, True)])
    return {"hook": target, "caller": 0x1165f0, "skip_resume": 0x113f98,
            "popup_caller": 0x126714, "popup_wrapper": popup_wrapper,
            "scope": "ASCII spaces at the start of dictionary definition line 2 only; text bytes unchanged"}


def definition_popup(image):
    # The 16px match popup is separate from the 20px dictionary browser.
    # Its paper has room for 160px; keep the original horizontal center.
    at = 0x126108
    require(image.words(at, 2) == (ins(41, 4, 17, 0x954), ins(41, 3, 17, 0x956)),
            "Original word-popup dimensions changed")
    a = Assembly()
    a.emit(ins(9, 2, 0, 160), ins(41, 2, 17, 0x954), image.words(at + 4)[0],
           jump(at + 8), 0)
    width_hook = image.hook(at, a.finish())
    at = 0x12638c
    require(image.words(at, 2) == (ins(41, 8, 4, 0x950), ins(43, 5, 4, 0xa90)),
            "Original word-popup position changed")
    a = Assembly()
    a.emit(ins(9, 3, 5, 20), ins(41, 3, 4, 0x950), image.words(at + 4)[0],
           jump(at + 8), 0)
    position_hook = image.hook(at, a.finish())
    return {"width_hook": width_hook, "position_hook": position_hook,
            "before": [28, 49, 144, 32], "after": [20, 49, 160, 32],
            "center_x_relative_to_book": 100, "font_pixels": 16,
            "book_and_title_unchanged": True}


def install(elf, alphabet, profiles, font_rows, demo_remaps=None, *, discovery_words=None):
    require(len(alphabet) == N and len(set(alphabet)) == N and len(font_rows) == N,
            "Expected the complete DBC 958-letter alphabet")
    image = Image(elf)
    wide_goals = any(p.get("goal_letters") is not None for p in profiles)
    versus = any(p.get("mode", 0) == 1 for p in profiles)
    stride = 32 if versus else 24 if wide_goals else 20
    table = image.append(bytes(4 + stride * len(profiles)))
    struct.pack_into("<I", image.blob, table - image.base, len(profiles))
    saved_profiles, word_indexes = [], {}
    for i, p in enumerate(profiles):
        records = bytearray(struct.pack("<I", len(p["words"])))
        for key, wid in sorted(p["words"], key=lambda r: (len(r[0]), r[0])):
            require(2 <= len(key) <= 9 and all(0 <= x < N for x in key), "Invalid wide word key")
            records.extend(struct.pack("<IH9H", wid, len(key), *key, *([0] * (9 - len(key)))))
        record_bytes = bytes(records)
        if record_bytes not in word_indexes:
            word_indexes[record_bytes] = image.append(record_bytes)
        index = word_indexes[record_bytes]
        mode = p.get("mode", 0)
        require(mode in (0, 1), "Invalid wide stage mode")
        random_letters = p.get("random_letters")
        require(random_letters is None or mode == 1 and len(random_letters) == 72
                and all(0 <= x < N for x in random_letters), "Invalid versus random tile map")
        cells = [0] * (13 * 16)
        for x, y, token in p["preplaced"]:
            require(0 <= x < 13 and 0 <= y < 9 and (0 <= token < N or token == 0xffff and random_letters is not None),
                    "Invalid wide preplaced letter")
            cells[x * 16 + y] = token
        cells_at = image.append(struct.pack(f"<{len(cells)}H", *cells))
        supply = image.append(struct.pack(f"<{len(p['inventory'])}H", *p["inventory"]))
        offset = table - image.base + 4 + i * stride
        struct.pack_into("<5I", image.blob, offset,
                         p["stage"], index, cells_at, supply, len(p["inventory"]))
        if p.get("goal_letters") is not None:
            letters = p["goal_letters"]
            require(len(letters) == 72 and all(0 <= x < N or x == 0xffff for x in letters),
                    "Invalid wide goal letter mapping")
            address = image.append(struct.pack("<72H", *letters))
            struct.pack_into("<I", image.blob, offset + 20, address)
        if versus:
            random_map = image.append(struct.pack("<72H", *random_letters)) if random_letters is not None else 0
            struct.pack_into("<2I", image.blob, offset + 24, mode, random_map)
        saved_profiles.append({"stage": p["stage"], "profile": table + 4 + i * stride,
                               "index": index, "words": len(p["words"]), "inventory": supply,
                               "inventory_count": len(p["inventory"]), "preplaced": p["preplaced"]})
    glyphs = image.append(b"".join(font_rows))
    tables = []
    for virtual, marker in ((SMALL, 0x2e400), (LARGE, 0x2e500)):
        handles = [(virtual + i) << 8 for i in range(N)]
        if versus:
            handles.append(marker)
        tables.append(image.append(struct.pack(f"<{len(handles)}I", *handles)))
    small, large = tables
    selector(image, table, stride, versus, demo_remaps)
    if demo_remaps:
        demo_entries(image)
    profile_lifetime(image)
    preview_profiles(image, table, stride)
    supply_loader(image)
    generated_supply(image)
    wide_supply_operations(image)
    supply_api(image)
    supply_display(image)
    piece_fields(image, versus)
    preplaced(image, versus)
    lookup = image.append(index_lookup())
    judge(image, lookup)
    goals = goal_judge(image) if wide_goals else None
    if wide_goals:
        goal_glyphs(image, table, stride)
    handle_tables(image, small, large)
    graphics = font_drawing(image, glyphs)
    graphics["retained_frame_copy_hook"] = retained_font_drawing(image)
    graphics["tile_style"] = tile_palette(image)
    graphics["versus_help_tiles"] = versus_help_tiles(image, alphabet)
    discovery = None
    if discovery_words is not None:
        from discovery_958 import install as install_discovery
        discovery = install_discovery(image, discovery_words)
    spacing = definition_leading_spaces(image)
    popup = definition_popup(image)
    condition_spacing = install_condition_spacing(image)
    random_personality = install_random_personality(image)
    output, report = image.finish()
    report.update(profiles=saved_profiles, lookup=lookup, word_indexes=len(word_indexes), small_handles=small,
                  large_handles=large, font_glyphs=glyphs, graphics=graphics, definition_spacing=spacing,
                  definition_popup=popup, condition_spacing=condition_spacing,
                  random_personality=random_personality)
    if goals is not None:
        report["wide_goal_hook"] = goals
    if demo_remaps:
        report["demo_remaps"] = demo_remaps
    if discovery is not None:
        report["discovery"] = discovery
    return output, report



