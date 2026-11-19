"""Inventory embedded EXP Japanese string candidates; not a patch approval list."""

import json
import re
import struct

from elf_probe import Elf
from font_tool import write_generated

from build_paths import DATA, REPO, WORK
from unpack_disc import file_hash, require


CONTROL_SIZES = bytes((0, 1, 1, 1, 1, 1, 3, 0, 255, 0, 0, 0, 3, 1, 5, 3,
                       5, 6, 2, 2, 0, 255, 4, 7, 1, 1, 0, 1, 0, 0, 0, 0))
PACKED_SIZES = (2, 2, 3, 2, 2, 0, 0, 0)


def parameter_count(data, at):
    control = data[at]
    require(control < 32, "Expected an EXP control opcode")
    count = CONTROL_SIZES[control]
    if count == 255:
        require(at + 1 < len(data), "Truncated variable EXP control")
        if control == 8:
            count = 1 + sum(size for bit, size in enumerate(PACKED_SIZES) if data[at + 1] >> bit & 1)
        elif control == 21:
            count = (data[at + 1] + 1) & 255
        else:
            raise ValueError("Unknown variable EXP control")
    require(at + count < len(data), "Truncated EXP control parameters")
    return count


def control_text(data, start, limit=None):
    limit = min(len(data), start + 2048) if limit is None else limit
    at, text, glyphs = start, [], 0
    while at < limit:
        first = data[at]
        if first == 0:
            return data[start:at], "".join(text), glyphs
        if first < 32:
            count = parameter_count(data, at)
            require(at + count < limit, "EXP control parameters cross the text bound")
            suffix = ":" + data[at + 1:at + count + 1].hex().upper() if count else ""
            text.append("{" + f"{first:02X}" + suffix + "}")
            at += count + 1
        elif first < 128:
            text.append(chr(first))
            at += 1
        else:
            require(at + 1 < limit and 0xa1 <= first <= 0xfe and 0xa1 <= data[at + 1] <= 0xfe,
                    "Non-EUC glyph in EXP text candidate")
            text.append(data[at:at + 2].decode("euc_jis_2004"))
            glyphs += 1
            at += 2
    raise ValueError("No bounded EXP text terminator")


def japanese(text):
    text = re.sub(r"\{[0-9A-F:]+\}", "", text)
    kana = sum("\u3040" <= c <= "\u30ff" for c in text)
    kanji = sum("\u4e00" <= c <= "\u9fff" for c in text)
    return kana >= 2 or kana >= 1 and kana + kanji >= 2 or (
        bool(text) and all("\u3000" <= c <= "\u30ff" or "\u4e00" <= c <= "\u9fff" for c in text))


def sections(data):
    header = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
    require(header[0][:6] == b"\x7fELF\x01\x01" and header[2] == 8, "Expected MIPS ELF")
    rows = [struct.unpack_from("<10I", data, header[6] + i * header[11]) for i in range(header[12])]
    strings = rows[header[13]]
    strings = data[strings[4]:strings[4] + strings[5]]
    for row in rows:
        if row[2] & 2 and row[1] != 8 and row[5] and (not row[2] & 4 or row[3] >= 0xa80000):
            name = strings[row[0]:].split(b"\0", 1)[0].decode("ascii")
            yield name, row


def inventory():
    original = json.loads((DATA / "disc-inventory.json").read_text(encoding="utf-8"))
    results, sources, controls = [], [], []
    for filename in ("EXP.ELF", "OV01.OVL", "OV02.OVL"):
        name = "EXP/" + filename
        path = REPO / "unpacked" / name
        record = next(item for item in original["files"] if item["path"] == name)
        require(file_hash(path) == record["sha256"], "Original embedded EXP executable differs")
        data = path.read_bytes()
        counts = {}
        for section_name, section in sections(data):
            count = 0
            chunk = data[section[4]:section[4] + section[5]]
            covered_to = section[4]
            starts = set()
            if section_name in (".data", ".rodata", ".sdata", "ov01", "ov02"):
                starts.update(range(section[4], section[4] + section[5], 8))
                starts.update(section[4] + match.start() for match in re.finditer(rb"\x0b\x0d[\x01-\x06]", chunk))
            for at in sorted(starts):
                if at < covered_to or data[at] == 0:
                    continue
                try:
                    raw, text, glyphs = control_text(data, at, min(at + 2048, section[4] + section[5]))
                except (ValueError, UnicodeError):
                    continue
                if glyphs and japanese(text):
                    controls.append({"source": filename, "section": section_name,
                                     "address": section[3] + at - section[4], "offset": at,
                                     "raw": raw.hex(), "text": text})
                    covered_to = at + len(raw) + 1
            for match in re.finditer(rb"[^\0]+\0", chunk):
                raw = match[0][:-1]
                if len(raw) > 2048:
                    continue
                variants = {}
                for encoding in ("euc_jis_2004", "cp932"):
                    try:
                        text = raw.decode(encoding)
                    except UnicodeError:
                        continue
                    if not all(ord(char) >= 32 or char in "\t\r\n\x01\x02\x03\x04\x05\x06\x0b\x0c\x15\x19" for char in text):
                        continue
                    if not any("\u3040" <= char <= "\u30ff" or "\u4e00" <= char <= "\u9fff" for char in text):
                        continue
                    if text.encode(encoding) != raw:
                        continue
                    variants[encoding] = text
                if not variants:
                    continue
                at = match.start()
                results.append({"source": filename, "section": section_name,
                                "address": section[3] + at, "offset": section[4] + at,
                                "raw": raw.hex(), "variants": variants})
                count += 1
            counts[section_name] = count
        sources.append({"file": name, "sha256": record["sha256"], "candidate_counts": counts})
    focused = [row for row in results if any(japanese(text) for text in row["variants"].values())
               and row["section"] not in (".vudata", ".reginfo", ".lit4")]
    main = Elf((REPO / "unpacked/EXP/EXP.ELF").read_bytes())
    boot_refs = {}
    for row in controls + focused:
        if row["source"] == "OV02.OVL" or row["section"] == "ov02":
            if row["address"] not in boot_refs:
                boot_refs[row["address"]] = [{"address": address, "kind": kind} for address, kind in main.refs(row["address"])
                                             if 0xa80000 <= address < 0xa836e8]
            row["boot_static_refs"] = boot_refs[row["address"]]
    return {"scope": "Provisional lexical/control candidates, including debug and false matches; not complete display coverage",
            "sources": sources, "raw_nul_candidate_count": len(results), "candidates": focused,
            "control_strings": controls, "game_files_modified": False}


if __name__ == "__main__":
    report = inventory()
    write_generated(WORK / "evidence/exp-embedded-candidates.json",
                    (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(report["sources"], ensure_ascii=False))
    print(f"[CANDIDATES] {len(report['candidates'])}; no display/relocation approval implied")
    print(f"[CONTROL STRINGS] {len(report['control_strings'])}; embedded parameter NULs preserved")
