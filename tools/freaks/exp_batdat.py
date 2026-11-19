"""EXP battle-data text only: keep all sections, numbers and file size fixed."""

from dataclasses import dataclass
import json
import re
import struct

import exp_sb as sb

from build_paths import DATA, REPO
from unpack_disc import require


NAME = "yamamoto/batdat.bin"
# index: (label, record stride, record count, string fields). Verified against EXP OV01.
SCHEMAS = {
    0: ("party", 92, 15, ("name",)), 1: ("enemies", 92, 125, ("name",)),
    6: ("ether", 12, 109, ("name", "desc")), 7: ("skills", 12, 104, ("name", "desc")),
    8: ("skill_nodes", 16, 40, ("name", "desc")), 9: ("items", 16, 40, ("name", "desc")),
    10: ("secret_keys", 4, 31, ("name", "desc")), 11: ("decoders", 8, 107, ("name", "desc")),
    12: ("double_tech", 16, 16, ("name", "desc")), 13: ("character_tech", 16, 21, ("name", "desc")),
    14: ("es_tech", 16, 10, ("name", "desc")), 18: ("battle_messages", 2, 53, ("text",)),
}


@dataclass(frozen=True)
class Entry:
    section: int
    record: int
    field: str
    pointer_at: int
    string_at: int
    raw: bytes


class BattleData:
    def __init__(self, data):
        require(len(data) >= 84 and struct.unpack_from("<II", data) == (19, len(data)), "EXP batdat header differs")
        self.data = data
        offsets = struct.unpack_from("<19I", data, 8)
        require(offsets[0] == 84 and list(offsets) == sorted(set(offsets)) and offsets[-1] < len(data),
                "EXP batdat section offsets differ")
        self.bounds = tuple(zip(offsets, (*offsets[1:], len(data))))
        self.entries, self.pools = [], {}
        for index, (_, stride, count, fields) in SCHEMAS.items():
            start, end = self.bounds[index]
            pool = count * stride
            require(struct.unpack_from("<H", data, start)[0] == pool and pool < end - start < 0x8000,
                    f"EXP batdat record table/pool differs: {index}")
            references = set()
            for record in range(count):
                for number, field in enumerate(fields):
                    position = start + record * stride + number * 2
                    relative = struct.unpack_from("<h", data, position)[0]
                    require(pool <= relative < end - start, f"EXP batdat string pointer outside pool: {position:08X}")
                    pointer = start + relative
                    stop = data.find(b"\0", pointer, end)
                    require(stop >= pointer, "EXP batdat string is unterminated")
                    self.entries.append(Entry(index, record, field, position, pointer, data[pointer:stop]))
                    references.add(pointer)
            cursor = start + pool
            starts = set()
            while cursor < end:
                stop = data.find(b"\0", cursor, end)
                require(stop >= cursor, "EXP batdat pool has no final terminator")
                require(stop == cursor or cursor in references, f"Unreferenced EXP batdat pool string: {cursor:08X}")
                starts.add(cursor)
                cursor = stop + 1
            require(references <= starts, "EXP batdat pointer enters the middle of a string")
            self.pools[index] = start + pool, end
        self.by_position = {entry.pointer_at: entry for entry in self.entries}


def original():
    report = json.loads((DATA / "contents-report.json").read_text(encoding="utf-8"))
    row = next(r for r in report["members"] if r["source"] == "EXP/XENOSAGA.00~02" and r["path"] == NAME)
    data = (REPO / row["output"]["path"]).read_bytes()
    require(sb.checksum(data) == row["output"]["sha256"], "Original EXP batdat differs")
    BattleData(data)
    return data


def export_editor(data):
    lines = ["# FREAKS-EXP-BATDAT: JPN-KOR v1", f"# SOURCE: {NAME}", f"# SHA256: {sb.checksum(data)}",
             "# Empty KOR keeps original bytes. Keep hexadecimal IDs, JPN and control tokens.",
             "# Literal \\n remains the game's line-break command.", ""]
    for entry in BattleData(data).entries:
        label = SCHEMAS[entry.section][0]
        lines.extend((f"# {entry.section:02d}_{label} / {entry.record + 1:03d} / {entry.field}",
                      f"[{entry.pointer_at:08X}]", "[JPN]", sb.render(entry.raw), "[KOR]", "", "[END]", ""))
    return "\n".join(lines)


def parse_editor(content, data):
    content = content.removeprefix("\ufeff").replace("\r\n", "\n")
    require(content.startswith("# FREAKS-EXP-BATDAT: JPN-KOR v1\n") and "\r" not in content,
            "Wrong EXP batdat editor format")
    header = content.split("\n[", 1)[0]
    require(re.findall(r"^# SOURCE: (.+)$", header, re.M) == [NAME], "EXP batdat source name changed")
    require(re.findall(r"^# SHA256: (.+)$", header, re.M) == [sb.checksum(data)], "EXP batdat source hash changed")
    matches, entries = list(sb.BLOCK.finditer(content)), BattleData(data).entries
    require(len(matches) == len(entries), "Missing, duplicate or malformed EXP batdat blocks")
    cursor, translations = 0, {}
    for match, entry in zip(matches, entries):
        require(all(not line or line.startswith("#") for line in content[cursor:match.start()].splitlines()),
                "Unexpected content outside EXP batdat block")
        jpn = sb.render(entry.raw)
        require(int(match[1], 16) == entry.pointer_at and match[2] == jpn, "EXP batdat ID/JPN changed")
        if match[3].strip() and match[3] != jpn:
            require(sb.CONTROL.findall(match[3]) == sb.CONTROL.findall(jpn),
                    f"EXP batdat control tokens changed: {entry.pointer_at:08X}")
            translations[entry.pointer_at] = match[3]
        cursor = match.end()
    require(all(not line or line.startswith("#") for line in content[cursor:].splitlines()), "Unparsed EXP batdat tail")
    return translations


def rebuild(data, translations, mapping=None):
    original = BattleData(data)
    require(set(translations) <= original.by_position.keys(), "Unknown EXP batdat translation field")
    encoded, changed = {}, []
    for position, text in translations.items():
        entry = original.by_position[position]
        require(sb.CONTROL.findall(text) == sb.CONTROL.findall(sb.render(entry.raw)),
                f"EXP batdat control tokens changed: {position:08X}")
        try:
            raw = sb.encode(text, mapping)
        except (ValueError, UnicodeError) as error:
            raise ValueError(f"BATDAT.txt:{position:08X}: {error}") from error
        if raw != entry.raw:
            encoded[position] = raw
            changed.append({"id": f"{position:08X}", "section": entry.section,
                            "record": entry.record + 1, "field": entry.field,
                            "old_bytes": len(entry.raw), "bytes": len(raw)})
    output, sections = bytearray(data), []
    for index in sorted({original.by_position[position].section for position in encoded}):
        start, end = original.bounds[index]
        pool, _ = original.pools[index]
        packed = bytearray()
        for entry in original.entries:
            if entry.section != index:
                continue
            relative = pool - start + len(packed)
            require(relative < 0x8000, "EXP batdat signed text pointer overflow")
            struct.pack_into("<H", output, entry.pointer_at, relative)
            packed.extend(encoded.get(entry.pointer_at, entry.raw) + b"\0")
        require(len(packed) <= end - pool,
                f"EXP batdat section {index:02d}_{SCHEMAS[index][0]} pool exceeded ({len(packed)} > {end - pool})")
        output[pool:end] = packed + bytes(end - pool - len(packed))
        sections.append({"section": index, "used_bytes": len(packed), "capacity": end - pool})
    output = bytes(output)
    after = BattleData(output)
    require(output[:84] == data[:84] and len(output) == len(data), "EXP batdat header or file size changed")
    require(all(entry.raw == encoded.get(entry.pointer_at, original.by_position[entry.pointer_at].raw)
                for entry in after.entries), "Rebuilt EXP batdat text differs")
    return output, {"changed": sorted(changed, key=lambda item: item["id"]), "sections": sections,
                    "size": len(output), "section_layout_unchanged": True, "deduplicated_strings": False}
