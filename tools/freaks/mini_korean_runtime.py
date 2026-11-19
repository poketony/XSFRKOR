"""Stage-local DIX/texture selection in a separate ELF load segment."""

import struct

from elf_probe import Elf
from unpack_disc import require


STAGE_LOAD = 0x11cd28
STAGE_GRAPHICS = 0x11e210
RESOURCE_READ = 0x109eb8
DIX_LOAD = 0x114c64


def immediate(op, rt, rs, value):
    return op << 26 | rs << 21 | rt << 16 | value & 0xffff


def register(function, rd, rs, rt=0):
    return rs << 21 | rt << 16 | rd << 11 | function


def jump(address, link=False):
    require(address % 4 == 0 and address < 0x10000000, "Patch jump is outside its region")
    return (3 if link else 2) << 26 | address >> 2


class Assembly:
    def __init__(self):
        self.words, self.labels, self.branches = [], {}, []

    def emit(self, *words):
        self.words.extend(words)

    def address(self, rt, value):
        self.emit(immediate(15, rt, 0, value >> 16), immediate(13, rt, rt, value))

    def label(self, name):
        require(name not in self.labels, "Duplicate patch label")
        self.labels[name] = len(self.words)

    def branch(self, op, rs, rt, target):
        self.branches.append((len(self.words), target))
        self.emit(immediate(op, rt, rs, 0))

    def finish(self):
        for at, target in self.branches:
            distance = self.labels[target] - at - 1
            require(-32768 <= distance <= 32767, "Patch branch is too distant")
            self.words[at] |= distance & 0xffff
        return struct.pack(f"<{len(self.words)}I", *self.words)


def stage_selector(table, state, stage_register, original, continuation):
    a = Assembly()
    if stage_register == 4:
        a.emit(4 << 16 | 12 << 11 | 16 << 6, 12 << 16 | 12 << 11 | 16 << 6 | 3)
        stage_register = 12
    a.address(8, table)
    a.emit(immediate(35, 9, 8, 0), immediate(9, 8, 8, 4))
    a.label("loop")
    a.branch(4, 9, 0, "absent")
    a.emit(0, immediate(35, 10, 8, 0))
    a.branch(4, stage_register, 10, "found")
    a.emit(0, immediate(9, 9, 9, -1))
    a.branch(4, 0, 0, "loop")
    a.emit(immediate(9, 8, 8, 12))
    a.label("found")
    a.emit(immediate(35, 10, 8, 4), immediate(35, 11, 8, 8))
    a.branch(4, 0, 0, "store")
    a.emit(0)
    a.label("absent")
    a.emit(register(33, 10, 0, 0), register(33, 11, 0, 0))
    a.label("store")
    a.address(8, state)
    a.emit(immediate(43, 10, 8, 0), immediate(43, 11, 8, 4), *original,
           jump(continuation), 0)
    return a.finish()


def index_selector(state):
    a = Assembly()
    a.address(8, state)
    a.emit(immediate(35, 8, 8, 0))
    a.branch(5, 8, 0, "return")
    a.emit(0, immediate(35, 8, 4, 0x7404))
    a.label("return")
    a.emit(register(8, 0, 31), register(33, 3, 3, 2))
    return a.finish()


def texture_reader(state, original):
    a = Assembly()
    a.emit(immediate(9, 8, 0, 10))
    a.branch(5, 4, 8, "original")
    a.emit(0)
    a.address(8, state)
    a.emit(immediate(35, 8, 8, 4))
    a.branch(4, 8, 0, "original")
    a.emit(0, immediate(35, 9, 8, 0), immediate(9, 8, 8, 4))
    a.label("search")
    a.branch(4, 9, 0, "original")
    a.emit(0, immediate(35, 10, 8, 0))
    a.branch(5, 5, 10, "next")
    a.emit(0, immediate(35, 10, 8, 8))
    a.branch(5, 6, 10, "original")
    a.emit(0, immediate(35, 8, 8, 4), register(33, 9, 7), register(33, 10, 7, 6))
    a.label("copy")
    a.emit(immediate(36, 11, 8, 0), immediate(9, 8, 8, 1), immediate(40, 11, 9, 0),
           immediate(9, 9, 9, 1))
    a.branch(5, 9, 10, "copy")
    a.emit(0, register(8, 0, 31), register(33, 2, 6))
    a.label("next")
    a.emit(immediate(9, 9, 9, -1))
    a.branch(4, 0, 0, "search")
    a.emit(immediate(9, 8, 8, 12))
    a.label("original")
    a.emit(*original, jump(RESOURCE_READ + 8), 0)
    return a.finish()


def align(value, size=16):
    return (value + size - 1) & -size


def install(elf_data, profiles):
    """Profiles: stage, independent DIX bytes, (old offset, full native-read bytes)."""
    require(profiles and len({p["stage"] for p in profiles}) == len(profiles), "Missing/duplicate runtime stage profile")
    require(all(type(p["stage"]) is int and 0 <= p["stage"] < 107 for p in profiles), "Invalid runtime stage")
    elf = Elf(elf_data)
    h = struct.unpack_from("<16sHHIIIIIHHHHHH", elf_data)
    phoff, phsize, phcount = h[5], h[9], h[10]
    require(phsize == 32 and phcount == 3, "Unexpected MINI ELF program headers")
    headers = [struct.unpack_from("<8I", elf_data, phoff + i * phsize) for i in range(phcount)]
    end = max(p[2] + p[5] for p in headers if p[0] == 1)
    base, offset = align(end, 4096), align(len(elf_data), 4096)
    new_header = phoff + phcount * phsize
    require(not any(elf_data[new_header:new_header + 32]) and new_header + 32 <= 4096,
            "No unused ELF program-header slot")
    expected = {STAGE_LOAD: (0x27bdffe0, 0xffb00000), STAGE_GRAPHICS: (0x27bdffe0, 0x00042400),
                RESOURCE_READ: (0x27bdffd0, 0x3c02001c), DIX_LOAD: (0x8c887404, 0x00621821)}
    for address, words in expected.items():
        require(struct.unpack_from("<2I", elf_data, elf.offset(address)) == words,
                f"Original runtime patch site differs: {address:#x}")
    require(struct.unpack_from("<I", elf_data, elf.offset(0x1078d4))[0] == 0x3c030028
            and struct.unpack_from("<I", elf_data, elf.offset(0x1078e0))[0] == 0x2463d17c,
            "Original MINI heap start differs")
    state, table = base, base + 16
    blob = bytearray(align(16 + 4 + len(profiles) * 12))

    def append(data):
        blob.extend(b"\0" * (align(len(blob)) - len(blob)))
        address = base + len(blob)
        blob.extend(data)
        return address

    entries = {}
    entries[STAGE_LOAD] = append(stage_selector(table, state, 5, expected[STAGE_LOAD], STAGE_LOAD + 8))
    entries[STAGE_GRAPHICS] = append(stage_selector(table, state, 4, expected[STAGE_GRAPHICS], STAGE_GRAPHICS + 8))
    entries[RESOURCE_READ] = append(texture_reader(state, expected[RESOURCE_READ]))
    entries[DIX_LOAD] = append(index_selector(state))
    struct.pack_into("<I", blob, 16, len(profiles))
    records = []
    for i, profile in enumerate(sorted(profiles, key=lambda p: p["stage"])):
        dix = append(profile["dix"])
        textures = []
        for old, raw in profile["textures"]:
            require(raw and len(raw) <= 65536 and old >= 0, "Invalid runtime texture read bank")
            textures.append((old, append(raw), len(raw)))
        require(len({r[0] for r in textures}) == len(textures), "Duplicate runtime texture offset")
        texture_table = append(struct.pack("<I", len(textures))
                               + b"".join(struct.pack("<3I", *row) for row in textures))
        struct.pack_into("<3I", blob, 20 + i * 12, profile["stage"], dix, texture_table)
        records.append({"stage": profile["stage"], "dix_address": dix, "dix_bytes": len(profile["dix"]),
                        "texture_table": texture_table, "texture_reads": textures})
    heap_start = align(base + len(blob), 64)
    require(heap_start < 0x1000000, "Runtime segment leaves too little game RAM")
    output = bytearray(elf_data.ljust(offset, b"\0") + blob)
    struct.pack_into("<8I", output, new_header, 1, offset, base, base, len(blob), len(blob), 7, 4096)
    struct.pack_into("<H", output, 44, phcount + 1)
    for address, target in entries.items():
        words = (jump(target, link=address == DIX_LOAD), 0)
        struct.pack_into("<2I", output, elf.offset(address), *words)
    struct.pack_into("<I", output, elf.offset(0x1078d4), immediate(15, 3, 0, (heap_start + 0x8000) >> 16))
    struct.pack_into("<I", output, elf.offset(0x1078e0), immediate(9, 3, 3, heap_start))
    # Keep the old sections intact and describe the new load segment to inspectors.
    names = elf_data[elf.sections[h[13]][4]:elf.sections[h[13]][4] + elf.sections[h[13]][5]]
    name_at = len(names)
    names += b".korpatch\0"
    names_offset = len(output)
    output.extend(names)
    output.extend(b"\0" * (align(len(output), 4) - len(output)))
    section_offset = len(output)
    for i, section in enumerate(elf.sections):
        row = list(section)
        if i == h[13]:
            row[4:6] = names_offset, len(names)
        output.extend(struct.pack("<10I", *row))
    output.extend(struct.pack("<10I", name_at, 1, 7, base, offset, len(blob), 0, 0, 16, 0))
    struct.pack_into("<I", output, 32, section_offset)
    struct.pack_into("<H", output, 48, h[12] + 1)
    checked = Elf(bytes(output))
    require(checked.offset(base) == offset and checked.gp == elf.gp, "Extended ELF section/GP differs")
    return bytes(output), {"segment_address": base, "segment_bytes": len(blob), "heap_start": heap_start,
                           "state_address": state, "profile_table": table, "profiles": records,
                           "hooks": {f"0x{k:X}": v for k, v in entries.items()},
                           "original_bss_and_entrypoint": "preserved"}
