"""EXP TXD editors and in-place slot-writer patch; no game files are written."""

import json
import re
import struct

from elf_probe import Elf
import exp_sb as sb

from build_paths import DATA, REPO
from unpack_disc import require


TABLES = {
    "yosikawa/MainMenu.txd": (569, 0x69f100, 0x2800, 0x227ae0),
    "yosikawa/Tutorial.txd": (254, 0x6a1900, 0x2000, 0x227b00),
}
MAIN = "yosikawa/MainMenu.txd"
SLOT_IDS = frozenset((218, 539, 543, 544, 546, 547, 548))
SLOT_CALLS = {0x232d18: 544, 0x232e74: 548, 0x234144: 546, 0x2341ec: 543,
              0x23433c: 539, 0x234968: 547, 0x236270: 218, 0x236324: 218}
SLOT_ENTRY = 0x232a30
CONTROL = re.compile(r"\{SLOT\}|" + sb.CONTROL.pattern)
SLOT_ORIGINAL = bytes.fromhex(
    "f0ffbd270000b0ff2d8080000800bfff"
    "b89e080c0202a424080043900800bfdf"
    "080003a209004490090004a20000b0df"
    "0800e0031000bd27")


class Table:
    def __init__(self, data, name, *, slot_metadata=False):
        require(name in TABLES, "Unknown EXP TXD source")
        count, _, limit, _ = TABLES[name]
        require(count * 4 < len(data) <= limit, f"EXP TXD buffer exceeded: {name} ({len(data)} > {limit})")
        self.data, self.name, self.slots = data, name, slot_metadata
        self.offsets = struct.unpack_from(f"<{count}I", data)
        self.strings, self.numbers = [], {}
        cursor = count * 4
        for index, offset in enumerate(self.offsets):
            dynamic = name == MAIN and index in SLOT_IDS
            if slot_metadata and dynamic:
                require(cursor + 2 < len(data), "Truncated EXP slot descriptor")
                position, width = data[cursor:cursor + 2]
                self.numbers[index] = position, width
                cursor += 2
            require(offset == cursor, f"EXP TXD pointer/layout differs: {index}")
            end = data.find(b"\0", offset)
            require(end >= offset, "Unterminated EXP TXD entry")
            raw = data[offset:end]
            if dynamic:
                position, width = self.numbers.get(index, (8, 2))
                require(width in (1, 2) and position + width <= len(raw), "Invalid EXP slot descriptor")
                require(raw[position:position + width] in (b"0", b"1", b"2") if width == 1
                        else raw[position:position + width] in (b"\xa3\xb0", b"\xa3\xb1", b"\xa3\xb2"),
                        "EXP slot descriptor does not point at a numeric glyph")
                self.numbers[index] = position, width
            self.strings.append(raw)
            cursor = end + 1
        require(cursor == len(data), "Trailing EXP TXD bytes")


def originals():
    report = json.loads((DATA / "contents-report.json").read_text(encoding="utf-8"))
    result = {}
    for name in TABLES:
        row = next(r for r in report["members"] if r["source"] == "EXP/XENOSAGA.00~02" and r["path"] == name)
        data = (REPO / row["output"]["path"]).read_bytes()
        require(sb.checksum(data) == row["output"]["sha256"], f"Original EXP TXD differs: {name}")
        Table(data, name)
        result[name] = data
    return result


def original_elf():
    inventory = json.loads((DATA / "disc-inventory.json").read_text(encoding="utf-8"))
    row = next(r for r in inventory["files"] if r["path"] == "EXP/EXP.ELF")
    data = (REPO / "unpacked/EXP/EXP.ELF").read_bytes()
    require(sb.checksum(data) == row["sha256"], "Original EXP TXD executable differs")
    return data


def editors():
    return {name: export_editor(data, name) for name, data in originals().items()}


def render(raw, name, index):
    if name == MAIN and index in SLOT_IDS:
        require(raw[8:10] == b"\xa3\xb0", "Original EXP slot marker differs")
        return sb.render(raw[:8]) + "{SLOT}" + sb.render(raw[10:])
    return sb.render(raw)


def export_editor(data, name):
    lines = ["# FREAKS-EXP-TXD: JPN-KOR v1", f"# SOURCE: {name}", f"# SHA256: {sb.checksum(data)}",
             "# Empty KOR preserves original bytes. Keep JPN, hexadecimal IDs and control tokens.",
             "# Keep {SLOT} exactly once; its byte position is adjusted automatically.", ""]
    for index, raw in enumerate(Table(data, name).strings):
        lines.extend((f"[{index:08X}]", "[JPN]", render(raw, name, index), "[KOR]", "", "[END]", ""))
    return "\n".join(lines)


def parse_editor(content, data, name):
    content = content.removeprefix("\ufeff").replace("\r\n", "\n")
    require(content.startswith("# FREAKS-EXP-TXD: JPN-KOR v1\n") and "\r" not in content,
            "Wrong EXP TXD editor format")
    header = content.split("\n[", 1)[0]
    require(re.findall(r"^# SOURCE: (.+)$", header, re.M) == [name], "EXP TXD editor/source mismatch")
    require(re.findall(r"^# SHA256: (.+)$", header, re.M) == [sb.checksum(data)], "EXP TXD source hash differs")
    matches, strings = list(sb.BLOCK.finditer(content)), Table(data, name).strings
    require(len(matches) == len(strings), "Missing, duplicate or malformed EXP TXD blocks")
    cursor, result = 0, {}
    for index, (match, raw) in enumerate(zip(matches, strings)):
        require(all(not line or line.startswith("#") for line in content[cursor:match.start()].splitlines()),
                "Unexpected content outside EXP TXD block")
        original = render(raw, name, index)
        require(int(match[1], 16) == index and match[2] == original, "EXP TXD ID or JPN was changed")
        text = match[3]
        if text.strip() and text != original:
            require(CONTROL.findall(text) == CONTROL.findall(original), f"EXP TXD control tokens changed at {index}")
            result[index] = text
        cursor = match.end()
    require(all(not line or line.startswith("#") for line in content[cursor:].splitlines()), "Unparsed EXP TXD editor tail")
    return result


def encode(text, name, index, mapping=None):
    if name == MAIN and index in SLOT_IDS:
        require(text.count("{SLOT}") == 1, "EXP TXD needs exactly one {SLOT} marker")
        before, after = text.split("{SLOT}")
        prefix = sb.encode(before, mapping)
        require(len(prefix) <= 255, "EXP slot marker exceeds its 255-byte position field")
        return prefix + b"0" + sb.encode(after, mapping), (len(prefix), 1)
    require("{SLOT}" not in text, "Unexpected EXP slot marker")
    return sb.encode(text, mapping), None


def rebuild(data, name, translations, mapping=None):
    original = Table(data, name)
    require(set(translations) <= set(range(len(original.strings))), "Unknown EXP TXD entry")
    rows, numbers, changed = list(original.strings), dict(original.numbers), []
    for index, text in translations.items():
        require(CONTROL.findall(text) == CONTROL.findall(render(rows[index], name, index)),
                f"EXP TXD control tokens changed at {index}")
        raw, number = encode(text, name, index, mapping)
        if raw == rows[index]:
            continue
        changed.append({"index": index, "old_bytes": len(rows[index]), "bytes": len(raw)})
        rows[index] = raw
        if number is not None:
            numbers[index] = number
    if not changed:
        return data, {"changed": [], "slot_metadata": False, "size": len(data)}
    metadata = name == MAIN
    output, offsets = bytearray(len(rows) * 4), []
    for index, raw in enumerate(rows):
        if metadata and index in SLOT_IDS:
            output.extend(bytes(numbers[index]))
        offsets.append(len(output))
        output.extend(raw + b"\0")
    struct.pack_into(f"<{len(rows)}I", output, 0, *offsets)
    output = bytes(output)
    Table(output, name, slot_metadata=metadata)
    return output, {"changed": sorted(changed, key=lambda r: r["index"]), "slot_metadata": metadata,
                    "size": len(output), "limit": TABLES[name][2],
                    "slot_positions": {str(i): list(numbers[i]) for i in sorted(numbers)} if metadata else {}}


def patch_slots(data):
    elf = Elf(data)
    require(set(elf.refs(SLOT_ENTRY)) == {(site, "jump/call") for site in SLOT_CALLS},
            "EXP slot writer caller set differs")
    offset = elf.offset(SLOT_ENTRY)
    require(data[offset:offset + len(SLOT_ORIGINAL)] == SLOT_ORIGINAL, "EXP slot writer code differs")
    # All eight callers discard v0. Use only registers already clobbered by the original.
    # The two hidden bytes before each mutable string hold (digit offset, byte width).
    words = (0x9082fffe, 0x9083ffff, 0x00822021, 0x24a50031, 0x2463ffff,
             0x10600005, 0, 0x240300a3, 0xa0830000, 0x24840001, 0x24a50080,
             0x03e00008, 0xa0850000, 0)
    replacement = struct.pack("<14I", *words)
    require(len(replacement) == len(SLOT_ORIGINAL), "EXP slot patch size differs")
    return data[:offset] + replacement + data[offset + len(replacement):]
