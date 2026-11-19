"""MINI dictionary records and definition-only relocation, preserving puzzle IDs."""

from dataclasses import dataclass
import json
import struct

from mini_font import glyph_index
from unpack_contents import digest

from build_paths import DATA, REPO
from unpack_disc import require


NAMES = ("dicdata.dhd", "dicdata.dix", "dicdata.wix", "dicdata.wdt")


def text_codes(raw, allowed_codes=()):
    result = []
    at = 0
    while at < len(raw):
        value = raw[at]
        if value < 128:
            require(0x20 <= value <= 0x7e, "Control byte in MINI dictionary text")
            result.append(raw[at:at + 1])
            at += 1
        else:
            code = raw[at:at + 2]
            require(len(code) == 2 and all(0xa1 <= v <= 0xfe for v in code), "Invalid MINI EUC-JP pair")
            if code not in allowed_codes:
                try:
                    code.decode("euc_jp")
                except UnicodeDecodeError as error:
                    raise ValueError("Undefined MINI dictionary code needs an explicit font mapping") from error
            result.append(code)
            at += 2
    return result


@dataclass(frozen=True)
class Word:
    word_id: int
    offset: int
    category: int
    reading: bytes
    spelling: bytes
    definition: bytes
    raw: bytes


class Dictionary:
    def __init__(self, files, allowed_codes=()):
        require(set(files) == set(NAMES), "Expected all four MINI dictionary members")
        self.files = files
        header, self.index, offsets, self.data = (files[n] for n in NAMES)
        require(len(header) == 1024 and header[:4] == b"DHD\0", "Invalid MINI dictionary header")
        version, size, dix_size, wix_size, wdt_size, count = struct.unpack_from("<6I", header, 4)
        require(version == 0x100 and size == 1024 and 0 < count < 1 << 20, "Unsupported MINI dictionary version/count")
        require((dix_size, wix_size, wdt_size) == (len(self.index), len(offsets), len(self.data))
                and wix_size == count * 4 and wdt_size < 1 << 24, "MINI dictionary sizes disagree")
        self.allowed_codes = frozenset(allowed_codes)
        require(all(glyph_index(c) >= 0 for c in self.allowed_codes), "Added dictionary code has no MINI glyph index")
        positions = struct.unpack(f"<{count}I", offsets)
        require(positions[0] == 0 and all(a < b for a, b in zip(positions, positions[1:]))
                and positions[-1] < wdt_size, "MINI dictionary offset table is not ordered")
        self.words = []
        for word_id, (start, end) in enumerate(zip(positions, (*positions[1:], len(self.data))), 1):
            raw = self.data[start:end]
            require(40 <= len(raw) <= 255 and raw[0] == len(raw) and 1 <= raw[1] <= 10,
                    f"Invalid MINI word record: {word_id}")
            require(raw.endswith(b"\0\0") and b"\0" not in raw[38:-2], "Invalid MINI definition terminator")
            fields = []
            for fixed in (raw[2:20], raw[20:38]):
                value, _, padding = fixed.partition(b"\0")
                require(not any(padding), "MINI dictionary fixed field padding differs")
                fields.append(value)
            require(fields[0], "MINI dictionary reading is empty")
            for value in (*fields, raw[38:-2]):
                text_codes(value, self.allowed_codes)
            self.words.append(Word(word_id, start, raw[1], *fields, raw[38:-2], raw))

    def used_codes(self):
        return {code for word in self.words for raw in (word.reading, word.spelling, word.definition)
                for code in text_codes(raw, self.allowed_codes)}

    def rebuild_definitions(self, edits, allowed_codes=()):
        require(isinstance(edits, dict) and all(type(i) is int and 1 <= i <= len(self.words) for i in edits),
                "Unknown MINI dictionary word ID")
        allowed = self.allowed_codes | frozenset(allowed_codes)
        require(all(glyph_index(c) >= 0 for c in allowed), "Added dictionary code has no MINI glyph index")
        changed = {}
        for word_id, raw in edits.items():
            require(isinstance(raw, bytes) and len(raw) <= 215, "MINI definition exceeds the 255-byte word record")
            codes = text_codes(raw, allowed)
            if raw != self.words[word_id - 1].definition:
                require(all(len(c) == 1 or glyph_index(c) >= 0 for c in codes),
                        "New MINI definition contains a code unsupported by the renderer")
                changed[word_id] = raw
        if not changed:
            return self.files, {"changed": [], "moved": 0, "old_wdt_bytes": len(self.data),
                                "wdt_bytes": len(self.data), "puzzle_readings_and_ids_preserved": True}
        data, positions = bytearray(), []
        for word in self.words:
            positions.append(len(data))
            if word.word_id in changed:
                definition = changed[word.word_id]
                data.extend(bytes((40 + len(definition),)) + word.raw[1:38] + definition + b"\0\0")
            else:
                data.extend(word.raw)
        require(len(data) < 1 << 24, "MINI dictionary WDT exceeds native 24-bit offsets")
        header = bytearray(self.files["dicdata.dhd"])
        struct.pack_into("<I", header, 20, len(data))
        files = {"dicdata.dhd": bytes(header), "dicdata.dix": self.index,
                 "dicdata.wix": struct.pack(f"<{len(positions)}I", *positions), "dicdata.wdt": bytes(data)}
        checked = Dictionary(files, allowed)
        for before, after in zip(self.words, checked.words):
            require((before.word_id, before.category, before.reading, before.spelling)
                    == (after.word_id, after.category, after.reading, after.spelling), "MINI puzzle word changed")
            require(after.definition == changed.get(before.word_id, before.definition), "MINI definition rebuild differs")
            if before.word_id not in changed:
                require(after.raw == before.raw, "Unedited MINI dictionary record changed")
        return files, {"changed": sorted(changed), "moved": sum(w.offset != p for w, p in zip(self.words, positions)),
                       "old_wdt_bytes": len(self.data), "wdt_bytes": len(data),
                       "puzzle_readings_and_ids_preserved": True}


def originals():
    report = json.loads((DATA / "contents-report.json").read_text(encoding="utf-8"))
    hashes = {r["name"].removeprefix("dic/"): r["output"]["sha256"] for r in report["members"]
              if r["source"] == "MINI/WWROM.DAT" and r["name"].startswith("dic/")}
    files = {}
    for name in NAMES:
        data = (REPO / "decompressed/MINI/WWROM.DAT/dic" / name).read_bytes()
        require(digest(data) == hashes[name], f"Original MINI dictionary changed: {name}")
        files[name] = data
    return Dictionary(files)
