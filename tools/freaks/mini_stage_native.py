"""Original MINI stage loader with real resource heap and board installation."""

import struct

from mini_heap_native import HeapMachine
from mini_stages import Stages
from unpack_contents import wwrom_parts
from unpack_disc import require
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import UC_MIPS_REG_0, UC_MIPS_REG_PC


class StageMachine(HeapMachine):
    def __init__(self, elf_data, rom):
        super().__init__(elf_data, rom)
        self.stages = Stages(next(raw for row, raw in wwrom_parts(elf_data, rom) if row["name"] == "stage/1p.stg"))
        word = struct.unpack_from("<I", elf_data, self.elf.offset(0x11db80))[0]
        require(word == 0x00c24018, "MINI stage group multiply instruction differs")
        self.uc.hook_add(UC_HOOK_CODE, self.multiply, begin=0x11db80, end=0x11db80)
        self.loads = 0

    def multiply(self, uc, address, size, user):
        left, right = (uc.reg_read(UC_MIPS_REG_0 + r) & 0xffffffff for r in (6, 2))
        require(left < 8 and right == 0x315c, "Unexpected MINI stage group multiply operands")
        uc.reg_write(UC_MIPS_REG_0 + 8, left * right)
        uc.reg_write(UC_MIPS_REG_PC, address + 4)

    def read(self, pointer, size):
        return bytes(self.uc.mem_read(pointer, size))

    def u32(self, pointer):
        return struct.unpack("<I", self.read(pointer, 4))[0]

    def load(self, index, variant=0, mode=0):
        require(0 <= index < len(self.stages.stages) and 0 <= variant < 3 and mode in (0, 1),
                "MINI stage audit index/variant/mode outside bounds")
        stage = self.stages.stages[index]
        require(self.call(0x11d120, index) == stage.offset, "Native MINI stage offset differs")
        pointer = self.call(0x11cd28, mode, index, variant, count=10_000_000)
        require(pointer != 0, "Native MINI stage allocation failed")
        self.loads += 1
        actual = struct.unpack("<6I", self.read(pointer, 24))
        board = stage.board
        require(actual[:3] == (board.width, board.height, len(board.pieces))
                and actual[4:6] == (len(board.cells), len(board.objects)), "Native MINI board header differs")
        for i, row in enumerate(board.objects):
            require(self.read(pointer + 0x1efa8 + i * 0x430, 5) == row[:5], "Native MINI board object differs")
        for i, row in enumerate(board.cells):
            cell = pointer + 0x1c + i * 0x6c
            require(self.read(cell, 6) == row[:6], "Native MINI board cell differs")
            for x in range(row[1], row[1] + row[3]):
                for y in range(row[2], row[2] + row[3]):
                    require(self.u32(pointer + 0x3178 + (x * 9 + y) * 4) == cell,
                            "Native MINI board coordinate-to-cell map differs")
        installed = []
        for group in board.pieces:
            for row in group:
                cell = self.call(0x121380, pointer, row[1], row[2])
                piece = self.u32(cell + 0x14) if cell else 0
                require(piece and self.read(piece, 4) == bytes((row[0], 0, row[1], row[2])),
                        "Native MINI preplaced piece differs")
                installed.append(piece)
        require(self.u32(pointer + 0x18) == len(installed), "Native MINI installed-piece count differs")
        goal = stage.goals[variant]
        target = pointer + 0x24a7c
        require((self.u32(target), self.u32(target + 4), self.u32(target + 12)) == tuple(goal[:3])
                and self.read(target + 16, 12) == goal[4:], "Native MINI goal parameters differ")
        require(self.u32(pointer + 0x24b60) == struct.unpack_from("<H", stage.data, 0x5c + variant * 2)[0],
                "Native MINI stage variant value differs")
        expected = bytearray(0xc2)
        for source, size, destination in ((0x1c, 46, 0), (0x4a, 18, 0x2a), (0xa0, 64, 0x42), (0xe0, 64, 0x82)):
            expected[destination:destination + size] = stage.data[source:source + size]
        require(self.read(pointer + 0x24a98, len(expected)) == bytes(expected), "Native MINI stage text bytes differ")
        for offset in (0x23c0c, 0x24344) if mode == 1 else (0x23c0c,):
            inventory = pointer + offset
            count = struct.unpack("<H", self.read(inventory + 2, 2))[0]
            start = self.u32(inventory + 8)
            require(count == len(stage.inventory) and self.read(start, count) == stage.inventory
                    and self.read(inventory, 2) == stage.data[stage.inventory_offset:stage.inventory_offset + 2],
                    "Native MINI stage inventory differs")
            require(self.u32(inventory + 12) == start + count + count % 2, "Native MINI inventory workspace differs")
        return pointer

    def verify_stages(self):
        heap = super().verify()
        heap["resource_allocations"] = [dict(row) for row in heap["resource_allocations"]]
        require(self.call(0x11c990) == len(self.stages.stages), "Native MINI stage count differs")
        before_free = self.u32(self.root + 0x20)
        for stage in self.stages.stages:
            for variant in range(3):
                pointer = self.load(stage.index, variant)
                self.call(0x11cde0, pointer)
                require(self.u32(self.root + 0x20) == before_free, "Native MINI stage allocation was not released")
        for row, expected in wwrom_parts(self.elf.data, self.rom):
            descriptor = self.u32(0x224578 + row["index"] * 36)
            source = self.u32(descriptor) if descriptor else self.u32(0x224570) + row["offset"]
            require(self.read(source, len(expected)) == expected, "Native MINI stage loading changed a resource")
        require(self.read(self.arena_start + self.arena_size, 16) == b"\xa5" * 16
                and not self.pending and all(r["descriptor"] for r in self.allocations), "Native MINI stage heap audit failed")
        return {"stages": len(self.stages.stages), "variants_per_stage": 3, "loads": self.loads,
                "native_entries": ["0x11C990", "0x11D120", "0x11CD28", "0x11C9D8", "0x11D458", "0x11D290", "0x11D198"],
                "headers_cells_pieces_goals_inventory_text": "pass", "source_resources_and_arena_end": "preserved",
                "stage_allocations_released": True, "real_resource_heap": heap,
                "scope": "Original stage offsets, allocator, resource reads, board installation and variant goals. One R5900 destination-register multiply modelled; gameplay scoring, stage-menu reachability and GPU display not run"}
