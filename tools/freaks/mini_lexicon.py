"""Read-only MINI DIX token index and original-code lookup audit."""

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import struct

from font_tool import write_generated
from mini_dictionary import originals
from unpack_contents import digest

from build_paths import WORK
from unpack_disc import require
from verify_codecs import Native
import wwrom
from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_GP,
                               UC_MIPS_REG_SP, UC_MIPS_REG_RA, UC_MIPS_REG_PC, UC_MIPS_REG_V0)


ALPHABET = 72
DIRECTORY_BYTES = ALPHABET * ALPHABET * 4


def build_index(records, word_count):
    """Serialize checked token keys and candidate spans in the native DIX layout."""
    require(type(word_count) is int and 0 < word_count < 1 << 20, "MINI DIX word count exceeds 20-bit IDs")
    buckets = defaultdict(lambda: defaultdict(list))
    seen = set()
    for symbols, metadata in records:
        require(isinstance(symbols, bytes) and 2 <= len(symbols) <= 9
                and all(s < ALPHABET for s in symbols), "MINI DIX key outside 72 tokens / length 2..9")
        require(symbols not in seen, "Duplicate MINI DIX key")
        require(type(metadata) is int and 0 <= metadata < 1 << 24, "Unsupported MINI DIX metadata flags")
        first, count = metadata & 0xfffff, (metadata >> 20 & 15) + 1
        require(1 <= first and first + count - 1 <= word_count, "MINI DIX candidate span outside WDT")
        seen.add(symbols)
        buckets[symbols[0] * ALPHABET + symbols[1]][len(symbols)].append((symbols, metadata))
    directory = [0xffffffff] * (ALPHABET * ALPHABET)
    body = bytearray()
    for prefix, lengths in sorted(buckets.items()):
        directory[prefix] = len(body)
        block = bytearray(b"\xff" * 32)
        for length, rows in sorted(lengths.items()):
            rows.sort()
            stride = 4 if length == 2 else 8 if length <= 6 else 12
            require(len(rows) <= 65535, "MINI DIX node exceeds u16 record count")
            struct.pack_into("<I", block, (length - 2) * 4, len(block) - 32)
            block.extend(struct.pack("<HH", len(rows), stride))
            for symbols, metadata in rows:
                block.extend(struct.pack("<I", metadata) + symbols[2:] + b"\0" * (stride - length - 2))
        body.extend(block)
    return struct.pack("<5184I", *directory) + body


@dataclass(frozen=True)
class Entry:
    symbols: bytes
    offset: int
    metadata: int

    @property
    def word_ids(self):
        first = self.metadata & 0xfffff
        return range(first, first + (self.metadata >> 20 & 15) + 1)


class Lexicon:
    def __init__(self, dictionary):
        self.dictionary = dictionary
        self.data = dictionary.index
        require(len(self.data) >= DIRECTORY_BYTES, "MINI DIX prefix directory is truncated")
        cursor, buckets, nodes = DIRECTORY_BYTES, 0, 0
        self.entries = []
        for prefix, relative in enumerate(struct.unpack_from("<5184I", self.data)):
            if relative == 0xffffffff:
                continue
            base = DIRECTORY_BYTES + relative
            require(base == cursor and base + 32 <= len(self.data), "MINI DIX prefix ranges are not contiguous")
            cursor += 32
            buckets += 1
            for length, offset in enumerate(struct.unpack_from("<8I", self.data, base), 2):
                if offset == 0xffffffff:
                    continue
                start = base + 32 + offset
                require(start == cursor and start + 4 <= len(self.data), "MINI DIX length-node range differs")
                count, stride = struct.unpack_from("<HH", self.data, start)
                require(count > 0 and stride == (4 if length == 2 else 8 if length <= 6 else 12),
                        "Unsupported MINI DIX record stride/count")
                cursor += 4 + count * stride
                require(cursor <= len(self.data), "MINI DIX node exceeds file")
                nodes += 1
                previous = None
                for i in range(count):
                    at = start + 4 + i * stride
                    metadata = struct.unpack_from("<I", self.data, at)[0]
                    symbols = bytes(divmod(prefix, ALPHABET)) + self.data[at + 4:at + 4 + length - 2]
                    require(all(s < ALPHABET for s in symbols) and (previous is None or previous < symbols),
                            "MINI DIX token record is outside alphabet or not sorted")
                    require(not any(self.data[at + 4 + length - 2:at + stride]), "MINI DIX record padding differs")
                    entry = Entry(symbols, at, metadata)
                    require(all(1 <= wid <= len(dictionary.words) for wid in entry.word_ids), "MINI DIX word ID outside WDT")
                    self.entries.append(entry)
                    previous = symbols
        require(cursor == len(self.data), "Unowned bytes at MINI DIX end")
        self.buckets, self.nodes = buckets, nodes

    def report(self):
        covered, aliases, mismatches = set(), defaultdict(set), []
        for entry in self.entries:
            for word_id in entry.word_ids:
                covered.add(word_id)
                word = self.dictionary.words[word_id - 1]
                reading = word.reading.decode("euc_jp").strip("…")
                if len(reading) != len(entry.symbols):
                    mismatches.append({"word_id": word_id, "reading": reading, "symbols": list(entry.symbols),
                                       "record_offset": entry.offset})
                    continue
                for char, symbol in zip(reading, entry.symbols):
                    aliases[char].add(symbol)
        require(all(len(values) == 1 for values in aliases.values()), "MINI kana alias has conflicting token IDs")
        missing = sorted(set(range(1, len(self.dictionary.words) + 1)) - covered)
        return {"dix_sha256": digest(self.data), "dix_bytes": len(self.data), "alphabet": ALPHABET,
                "directory_bytes": DIRECTORY_BYTES, "prefix_buckets": self.buckets, "length_nodes": self.nodes,
                "records": len(self.entries), "word_ids_covered": len(covered), "unindexed_word_ids": missing,
                "candidate_span_counts": dict(sorted(Counter(len(e.word_ids) for e in self.entries).items())),
                "metadata_high_bytes": dict(sorted(Counter(e.metadata >> 24 for e in self.entries).items())),
                "wix_high_bytes": dict(sorted(Counter(v >> 24 for v in struct.unpack(
                    f"<{len(self.dictionary.words)}I", self.dictionary.files["dicdata.wix"])).items())),
                "reading_key_length_mismatches": mismatches,
                "observed_reading_aliases": {c: next(iter(values)) for c, values in sorted(aliases.items())},
                "query_corpus_sha256": digest(b"".join(bytes((len(e.symbols),)) + e.symbols
                                                        + struct.pack("<II", e.offset, e.metadata) for e in self.entries)),
                "scope": "Original DIX structure, token bytes and metadata spans; no Korean rebuild or full board judgement yet"}


class LexiconMachine(Native):
    INDEX = 0x400000

    def __init__(self, elf_data, lexicon):
        super().__init__(elf_data)
        self.lexicon = lexicon
        require(len(lexicon.data) < 1 << 20, "Native DIX audit buffer exceeded")
        self.uc.mem_write(self.INDEX, lexicon.data)
        self.uc.mem_write(0x1c7404, struct.pack("<I", self.INDEX))
        self.uc.mem_write(0x23cc18, struct.pack("<I", 1))

    def lookup(self, symbols):
        require(2 <= len(symbols) <= 9 and all(s < ALPHABET for s in symbols), "MINI query outside original alphabet/length")
        self.uc.mem_write(self.SOURCE, symbols)
        self.uc.reg_write(UC_MIPS_REG_GP, self.elf.gp)
        self.uc.reg_write(UC_MIPS_REG_SP, self.STACK)
        self.uc.reg_write(UC_MIPS_REG_RA, self.STOP)
        self.uc.reg_write(UC_MIPS_REG_A0, self.SOURCE)
        self.uc.reg_write(UC_MIPS_REG_A1, len(symbols))
        self.uc.emu_start(0x114c18, self.STOP, count=1_000_000)
        require(self.uc.reg_read(UC_MIPS_REG_PC) == self.STOP and self.uc.reg_read(UC_MIPS_REG_SP) == self.STACK,
                "Original MINI DIX lookup did not return with its stack restored")
        require(bytes(self.uc.mem_read(self.SOURCE, len(symbols))) == symbols, "Native DIX lookup changed its query")
        return self.uc.reg_read(UC_MIPS_REG_V0) & 0xffffffff

    def verify(self):
        for entry in self.lexicon.entries:
            pointer = self.lookup(entry.symbols)
            require(pointer == self.INDEX + entry.offset, "Native MINI DIX lookup selected another record")
            require(struct.unpack("<I", self.uc.mem_read(pointer, 4))[0] == entry.metadata,
                    "Native MINI DIX record metadata differs")
        require(bytes(self.uc.mem_read(self.INDEX, len(self.lexicon.data))) == self.lexicon.data,
                "Native MINI DIX source changed")
        missing = next(bytes(divmod(i, ALPHABET)) for i, offset in enumerate(struct.unpack_from("<5184I", self.lexicon.data))
                       if offset == 0xffffffff)
        require(self.lookup(missing) == 0, "Native MINI missing-prefix query was accepted")
        return {"lookups": len(self.lexicon.entries), "original_entry": "0x114C18",
                "original_compare": "0x1150D0", "exact_record_offsets": "pass",
                "source_query_and_stack_pointer_preserved": "pass", "absent_prefix_rejected": True,
                "scope": "Original DIX address lookup and byte comparator. Initialized index state modelled; candidate selection, scoring and board assembly not run"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    lexicon = Lexicon(originals())
    report = lexicon.report()
    if args.native:
        report["native"] = LexiconMachine(wwrom.originals()[0], lexicon).verify()
    name = "mini-lexicon-native.json" if args.native else "mini-lexicon-structure.json"
    write_generated(WORK / "evidence" / name, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"[PASS] {report['records']} DIX records; {report['word_ids_covered']} word IDs; native={args.native}")
    print(f"[ORIGINAL] Unindexed IDs: {report['unindexed_word_ids']}; reading/key mismatches: {len(report['reading_key_length_mismatches'])}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"[ERROR] {error}")
