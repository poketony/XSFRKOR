"""Original MINI board-to-token-query and DIX lookup path."""

from collections import Counter
import struct

from mini_dictionary import Dictionary
from mini_heap_native import HeapMachine
from mini_lexicon import Lexicon
from mini_stage_native import StageMachine
from unpack_contents import wwrom_parts
from unpack_disc import require


def simple_queries(board, token, x, y):
    require(all(row[0] == row[3] == 1 for row in board.cells), "Independent query model requires ordinary unit cells")
    cells = {(r[1], r[2]) for r in board.cells}
    if (x, y) not in cells:
        return []
    placed = {(r[1], r[2]): r[0] for group in board.pieces for r in group}
    require((x, y) not in placed, "Independent query model requires an empty placement cell")
    result = []
    for dx, dy in ((0, 1), (1, 0)):
        sides = []
        for direction in (-1, 1):
            side = []
            for distance in range(1, 9):
                at = x + dx * distance * direction, y + dy * distance * direction
                if at not in placed:
                    break
                side.append(placed[at])
            sides.append(side)
        left, right = sides
        for a in range(len(left) + 1):
            for b in range(len(right) + 1):
                if 2 <= a + b + 1 <= 9:
                    result.append(bytes((*reversed(left[:a]), token, *right[:b])))
    return result


class BoardMachine(StageMachine):
    def __init__(self, elf_data, rom):
        super().__init__(elf_data, rom)
        files = {row["name"].removeprefix("dic/"): raw for row, raw in wwrom_parts(elf_data, rom)
                 if row["name"].startswith("dic/")}
        self.lexicon = Lexicon(Dictionary(files))
        self.entries = {e.symbols: e.offset for e in self.lexicon.entries}
        # Unicorn has 64-bit GPRs; these eight R5900 spills restore pointer halves
        # only. Model their low 64 bits in RAM without changing the source ELF.
        self.spills = {0x1241cc: 0x7fa800c0, 0x1241d4: 0x7fa900d0,
                       0x124270: 0x7ba900d0, 0x124278: 0x7ba800c0,
                       0x124354: 0x7fa800c0, 0x12435c: 0x7fa900d0,
                       0x1243f8: 0x7ba900d0, 0x124400: 0x7ba800c0}
        for address, expected in self.spills.items():
            actual = struct.unpack_from("<I", elf_data, self.elf.offset(address))[0]
            require(actual == expected, "MINI board query R5900 spill differs")
            opcode = 0x37 if expected >> 26 == 0x1e else 0x3f
            self.uc.mem_write(address, struct.pack("<I", expected & 0x3ffffff | opcode << 26))

    def initialize_search(self):
        heap = HeapMachine.verify(self)
        heap["resource_allocations"] = [dict(row) for row in heap["resource_allocations"]]
        rows = list(wwrom_parts(self.elf.data, self.rom))
        row, data = next((row, data) for row, data in rows if row["name"] == "dic/dicdata.dix")
        descriptor = self.u32(0x224578 + row["index"] * 36)
        self.index = self.u32(descriptor) if descriptor else self.u32(0x224570) + row["offset"]
        require(data == self.lexicon.data and self.read(self.index, len(data)) == data, "Native MINI board DIX resource differs")
        self.call(0x1147d0)
        self.uc.mem_write(0x1c7404, struct.pack("<I", self.index))
        self.uc.mem_write(0x23cc18, struct.pack("<I", 1))
        return heap

    def queries(self, pointer, token, x, y, *, judge=False):
        require(0 <= token < 72 and 0 <= x < 13 and 0 <= y < 9, "Native MINI query outside original token/cell range")
        before = self.read(pointer, 0x24b84)
        free = self.u32(self.root + 0x20)
        query = self.call(0x123f98 if judge else 0x124098, pointer, token, x, y, count=3_000_000)
        result = []
        if query:
            count = self.u32(query + 0xe000)
            require(count <= 1024, "Native MINI board query buffer exceeded")
            for i in range(count):
                row = query + i * 56
                length = self.u32(row + 0x24)
                require(2 <= length <= 9, "Native MINI board query length differs")
                symbols = self.read(row + 0x2c, length)
                require(all(t < 72 for t in symbols), "Native MINI board query token outside alphabet")
                if judge:
                    expected = self.entries.get(symbols)
                    require(self.u32(row + 0x28) == (self.index + expected if expected is not None else 0),
                            "Native MINI board query selected another DIX record")
                result.append(symbols)
            self.call(0x124468, query)
        require(self.read(pointer, 0x24b84) == before and self.u32(self.root + 0x20) == free,
                "Native MINI query changed its board or leaked an allocation")
        return result

    def verify_boards(self):
        heap = self.initialize_search()
        trials, queries, matched, longest = 0, 0, 0, 0
        cases = ((36, range(72)), (28, (0, 1, 23, 44, 71)), (33, (0, 71)))
        for index, tokens in cases:
            stage = self.stages.stages[index]
            root = self.load(index)
            before = self.u32(self.root + 0x20)
            occupied = {(r[1], r[2]) for group in stage.board.pieces for r in group}
            for row in stage.board.cells:
                x, y = row[1:3]
                if (x, y) in occupied:
                    continue
                for token in tokens:
                    expected = simple_queries(stage.board, token, x, y)
                    actual = self.queries(root, token, x, y)
                    require(Counter(actual) == Counter(expected), "Original MINI board query differs from independent unit-cell model")
                    accepted = self.queries(root, token, x, y, judge=True)
                    any_match = any(s in self.entries for s in expected)
                    require(Counter(accepted) == Counter(expected if any_match else []), "Native MINI whole-placement acceptance differs")
                    trials += 1
                    queries += len(expected)
                    matched += sum(s in self.entries for s in expected)
                    longest = max(longest, max(map(len, expected), default=0))
            require(self.u32(self.root + 0x20) == before, "Native MINI board trials leaked memory")
            self.call(0x11cde0, root)
        require(self.read(self.index, len(self.lexicon.data)) == self.lexicon.data
                and self.read(self.arena_start + self.arena_size, 16) == b"\xa5" * 16,
                "Native MINI board trials changed DIX or crossed arena end")
        return {"stages": [i for i, _ in cases], "placement_trials": trials, "generated_queries": queries,
                "matching_queries": matched, "longest_query": longest,
                "native_entries": ["0x124098", "0x124570", "0x124718", "0x1244A0", "0x123F98", "0x114C18"],
                "independent_unit_cell_queries": "pass", "exact_dix_addresses_and_acceptance": "pass",
                "board_dix_and_arena_end": "preserved", "temporary_allocations_released": True,
                "r5900_pointer_spills_modelled_in_ram": [f"0x{a:X}" for a in self.spills],
                "real_resource_heap": heap,
                "scope": "Original query enumeration, token assembly and DIX acceptance on ordinary unit cells. Initialized search globals and eight low-64-bit R5900 pointer spills modelled; special multi-cell boards, scoring and GPU display not run"}
