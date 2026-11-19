"""Original MINI dictionary initialization, cohort selection and record loading."""

from collections import Counter
import struct

from mini_dictionary import Dictionary
from mini_heap_native import HeapMachine
from mini_lexicon import Lexicon
from unpack_contents import digest, wwrom_parts
from unpack_disc import require
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import (UC_MIPS_REG_0, UC_MIPS_REG_A0, UC_MIPS_REG_A1,
                               UC_MIPS_REG_A2, UC_MIPS_REG_PC, UC_MIPS_REG_RA,
                               UC_MIPS_REG_V0)


class CandidateMachine(HeapMachine):
    def __init__(self, elf_data, rom, dictionary):
        super().__init__(elf_data, rom)
        require(isinstance(dictionary, Dictionary), "Expected checked MINI dictionary")
        files = {r["name"].removeprefix("dic/"): raw for r, raw in wwrom_parts(elf_data, rom)
                 if r["name"].startswith("dic/")}
        require(files == dictionary.files, "Native candidate dictionary differs from WWROM")
        self.dictionary, self.lexicon = dictionary, Lexicon(dictionary)
        require(all(e.metadata >> 24 == 0 for e in self.lexicon.entries)
                and not any(dictionary.files["dicdata.wix"][3::4]), "Nonzero MINI candidate flags are not audited")
        require(struct.unpack_from("<I", elf_data, self.elf.offset(0x114704))[0] == 0x00501018,
                "MINI candidate random multiply instruction differs")
        self.uc.hook_add(UC_HOOK_CODE, self.multiply, begin=0x114704, end=0x114704)
        self.uc.hook_add(UC_HOOK_CODE, self.copy, begin=0x1a8644, end=0x1a8644)
        self.copy_calls, self.multiply_calls = 0, 0

    def multiply(self, uc, address, size, user):
        left, right = (uc.reg_read(UC_MIPS_REG_0 + r) & 0xffffffff for r in (2, 16))
        require(left <= 0xffff and 1 <= right <= 16, "MINI candidate random multiply operands differ")
        uc.reg_write(UC_MIPS_REG_V0, left * right)
        uc.reg_write(UC_MIPS_REG_PC, address + 4)
        self.multiply_calls += 1

    def copy(self, uc, address, size, user):
        destination, source, count = (uc.reg_read(r) & 0xffffffff
                                      for r in (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2))
        require(count <= 1 << 20, "MINI candidate memcpy exceeds audited size")
        if count < 32 or (destination | source) & 15:
            return
        uc.mem_write(destination, bytes(uc.mem_read(source, count)))
        uc.reg_write(UC_MIPS_REG_V0, destination)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))
        self.copy_calls += 1

    def read(self, pointer, size):
        return bytes(self.uc.mem_read(pointer, size))

    def u32(self, pointer):
        return struct.unpack("<I", self.read(pointer, 4))[0]

    def prepare(self):
        heap = HeapMachine.verify(self)
        heap["resource_allocations"] = [dict(r) for r in heap["resource_allocations"]]
        self.call(0x1147d0)
        require(self.call(0x114828, count=5_000_000) == 0, "Original MINI dictionary initialization failed")
        self.index, self.wix, self.cycle_bits, self.record_bits = (self.u32(a) for a in (0x1c7404, 0x1c7408, 0x1c740c, 0x1c7410))
        count = len(self.dictionary.words)
        self.bitmap_size = (count + 7) // 8 + 1
        require(self.u32(0x1c7414) == count and self.u32(0x23cc18) == 1
                and self.read(self.index, len(self.lexicon.data)) == self.lexicon.data
                and self.read(self.wix, count * 4) == self.dictionary.files["dicdata.wix"],
                "Native MINI dictionary pointers/count/data differ")
        require(not any(self.read(self.cycle_bits, self.bitmap_size))
                and not any(self.read(self.record_bits, self.bitmap_size)), "Native MINI candidate bitmaps were not cleared")
        return heap

    def choose(self, entry, mode=-1):
        require(mode == -1 or 0 <= mode <= 255, "MINI candidate mode outside audited range")
        output = self.STACK - 0x1000
        self.uc.mem_write(output - 16, b"\xa5" * 36)
        pointer = self.call(0x114dd0, self.index + entry.offset if entry is not None else 0, mode, output)
        word_id = self.u32(output)
        require(self.read(output - 16, 16) == b"\xa5" * 16 and self.read(output + 4, 16) == b"\xa5" * 16,
                "Native MINI candidate output crossed its four-byte ID slot")
        if entry is None:
            require((pointer, word_id) == (0, 0), "Native MINI null candidate did not clear its output")
        else:
            require(word_id in entry.word_ids and pointer == self.wix + (word_id - 1) * 4,
                    "Native MINI candidate selected another cohort/ID/WIX record")
        return word_id, pointer

    def read_word(self, word_id, pointer):
        word = self.dictionary.words[word_id - 1]
        output = self.STACK - 0x2000 + 1
        self.uc.mem_write(output - 16, b"\xa5" * 288)
        require(self.call(0x115070, pointer, output) == output, "Native MINI selected-word read returned another pointer")
        expected = self.dictionary.data[word.offset:word.offset + 256]
        require(self.read(output, len(expected)) == expected
                and self.read(output + len(expected), 256 - len(expected)) == b"\xa5" * (256 - len(expected)),
                "Native MINI selected-word bytes differ or crossed WDT end")
        require(self.read(output - 16, 16) == b"\xa5" * 16
                and self.read(output + 256, 16) == b"\xa5" * 16, "Native MINI selected-word output crossed its buffer")
        return expected[:len(word.raw)]

    def verify_candidates(self):
        heap = self.prepare()
        allocated = [dict(r) for r in self.allocations[len(heap["resource_allocations"]):]]
        free_loaded = self.u32(self.root + 0x20)
        expected_record = bytearray(self.bitmap_size)
        for entry in self.lexicon.entries:
            word_id, pointer = self.choose(entry)
            require(word_id == entry.word_ids.start, "Native MINI first-candidate mode selected another ID")
            if len(entry.word_ids) == 1:
                index = word_id - 1
                expected_record[index // 8] |= 1 << (index % 8)
        require(self.read(self.record_bits, self.bitmap_size) == bytes(expected_record)
                and not any(self.read(self.cycle_bits, self.bitmap_size)),
                "Native MINI first/single candidate bitmap writes differ")
        cohorts = []
        for span in sorted({len(e.word_ids) for e in self.lexicon.entries} - {1}):
            entry = next(e for e in self.lexicon.entries if len(e.word_ids) == span)
            workspace = self.STACK - 0x3000
            self.uc.mem_write(workspace - 16, b"\xa5" * (span + 32))
            self.call(0x1151b0, entry.word_ids.start - 1, span, workspace)
            require(self.read(workspace, span) == b"\0" * span
                    and self.read(workspace - 16, 16) == b"\xa5" * 16
                    and self.read(workspace + span, 16) == b"\xa5" * 16,
                    "Native MINI cohort reset crossed its workspace")
            cycles = []
            for _ in range(2):
                selected = []
                for _ in range(span):
                    word_id, pointer = self.choose(entry, 0)
                    require(self.read_word(word_id, pointer) == self.dictionary.words[word_id - 1].raw,
                            "Native MINI candidate/word-record link differs")
                    selected.append(word_id)
                require(Counter(selected) == Counter(entry.word_ids), "Native MINI candidate cycle repeated or omitted an ID")
                cycles.append(selected)
            for word_id in entry.word_ids:
                at, bit = divmod(word_id - 1, 8)
                require(self.read(self.cycle_bits + at, 1)[0] & 1 << bit
                        and self.read(self.record_bits + at, 1)[0] & 1 << bit,
                        "Native MINI selected candidate did not mark both bitmaps")
            cohorts.append({"first_id": entry.word_ids.start, "span": span, "cycles": cycles})
        self.choose(None)
        require(self.read(self.index, len(self.lexicon.data)) == self.lexicon.data
                and self.read(self.wix, len(self.dictionary.files["dicdata.wix"])) == self.dictionary.files["dicdata.wix"],
                "Native MINI selection changed dictionary tables")
        require(self.u32(self.root + 0x20) == free_loaded, "Native MINI candidate selection leaked an allocation")
        require(self.call(0x114980) == 0 and self.u32(self.root + 0x20) == heap["free_after_resource_load"],
                "Native MINI dictionary allocations were not released")
        for row, expected in wwrom_parts(self.elf.data, self.rom):
            descriptor = self.u32(0x224578 + row["index"] * 36)
            source = self.u32(descriptor) if descriptor else self.u32(0x224570) + row["offset"]
            require(self.read(source, len(expected)) == expected, "Native MINI candidate selection changed a source resource")
        require(self.read(self.arena_start + self.arena_size, 16) == b"\xa5" * 16
                and not self.pending and all(r["descriptor"] for r in self.allocations), "Native MINI candidate heap audit failed")
        return {"dix_sha256": digest(self.lexicon.data), "first_candidate_lookups": len(self.lexicon.entries),
                "cohort_cycles": cohorts, "single_candidate_bitmap": "pass", "cohort_repeat_prevention": "pass",
                "selected_word_reader": "pass", "source_tables_resources_and_arena_end": "preserved",
                "dictionary_allocations_released": True, "free_after_dictionary_init": free_loaded,
                "native_entries": ["0x114828", "0x114DD0", "0x115118", "0x1151B0", "0x115250", "0x115070", "0x114980"],
                "dictionary_allocations": allocated, "real_resource_heap": heap,
                "memcpy_calls_modelled": self.copy_calls, "random_multiply_calls_modelled": self.multiply_calls,
                "scope": "Original dictionary initialization, zero-high-flag cohort selection, random state, bitmaps and selected WDT reads. Libc memcpy and one R5900 destination multiply modelled; nonzero flags, gameplay scoring and GPU display not run"}
