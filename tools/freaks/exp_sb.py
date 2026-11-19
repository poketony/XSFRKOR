"""EXP SB scripts: protected JPN/KOR editors and bounded string relocation."""

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import re
import struct


from build_paths import DATA, REPO
from unpack_disc import require


# Original EXP OV01.OVL:0xAB4EF0; lengths are u16 words.
LENGTHS = bytes.fromhex(
    "01030303030303030303010101010303030103010103010101010101010101010101010101000101010101010101"
)
DATA_ARRAYS = {
    "165c79a1d88c70ab1417a0bee77f91d4efd0ac0fc28a4accfda27056ae087246": ((0x61dc, 0x62fc),),
    "40598f0a9ddb59bfd6fbff5f2a85a76ac1e0cb330a8ae5ecd9f974ddab03cb05": ((0xd25c, 0xd2e4),),
}
BLOCK = re.compile(r"^\[([0-9A-F]{8})\]\n\[JPN\]\n(.*?)\n\[KOR\]\n(.*?)\n\[END\](?:\n|$)", re.M | re.S)
ESCAPE = re.compile(r"\{([0-9A-F]{2}(?:[0-9A-F]{2})?)\}")
CONTROL = re.compile(r"\{[0-9A-F]{2}(?:[0-9A-F]{2})?\}|\$[A-Za-z]+[^;\n]*;|&[0-9]{2}")
ENCODING = "euc_jis_2004"


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def originals():
    report = json.loads((DATA / "contents-report.json").read_text(encoding="utf-8"))
    members = [row for row in report["members"] if row["source"] == "EXP/XENOSAGA.00~02"]
    sources, by_hash = {}, {}
    for row in members:
        if not row["path"].endswith(".sb"):
            continue
        data = (REPO / row["output"]["path"]).read_bytes()
        require(checksum(data) == row["output"]["sha256"], f"Original EXP SB differs: {row['path']}")
        sources[row["path"]] = data
        require(row["output"]["sha256"] not in by_hash, "Ambiguous duplicate EXP SB source")
        by_hash[row["output"]["sha256"]] = row["path"]
    require(len(sources) == 135, "EXP SB source inventory differs")
    aliases = {name: [] for name in sources}
    for row in members:
        key = row["output"]["sha256"]
        if row["path"].endswith(".bin") and key in by_hash:
            name = by_hash[key]
            require((REPO / row["output"]["path"]).read_bytes() == sources[name], "Original EXP SB/BIN copies differ")
            aliases[name].append(row["path"])
    require(sum(map(len, aliases.values())) == 6, "EXP SB/BIN copy inventory differs")
    return sources, aliases


def u32(data, at):
    return struct.unpack_from("<I", data, at)[0]


def cstring(data, at, end):
    require(0 <= at < end <= len(data), "EXP SB string outside section")
    stop = data.find(b"\0", at, end)
    require(stop >= 0, "EXP SB string has no terminator")
    return data[at:stop]


@dataclass(frozen=True)
class Instruction:
    offset: int
    opcode: int
    operand: int | None
    size: int


class Script:
    def __init__(self, data):
        require(len(data) >= 64 and data[:4] == b"SB  ", "Expected EXP SB header")
        self.data = data
        self.header = struct.unpack_from("<16I", data)
        h = self.header
        self.code, self.base, self.pool, self.exports, self.imports, source = h[5:11]
        self.source = source or len(data)
        require(64 <= self.code <= self.base <= self.pool <= self.exports <= self.imports <= self.source <= len(data),
                "EXP SB section boundaries differ")
        require((self.base - self.code) % 2 == 0 and (self.pool - self.base) % 4 == 0,
                "EXP SB code/data alignment differs")
        self.instructions = tuple(self.decode())
        boundaries = {item.offset for item in self.instructions}
        self.strings = {}
        at = self.pool
        while at < self.exports:
            raw = cstring(data, at, self.exports)
            self.strings[at] = raw
            at += len(raw) + 1
        require(self.exports + h[3] * 8 <= self.imports, "EXP SB export table overlaps imports")
        self.export_entries = []
        for at in range(self.exports, self.exports + h[3] * 8, 8):
            name, pc = struct.unpack_from("<II", data, at)
            name = cstring(data, self.exports + name, self.imports).decode("ascii")
            require(self.code + pc * 2 in boundaries, "EXP SB export has invalid PC")
            self.export_entries.append((name, pc))
        names = self.imports + h[4] * 4
        require(names <= self.source, "EXP SB import table overlaps source name")
        self.import_entries = []
        for at in range(self.imports, names, 4):
            low, high = struct.unpack_from("<HH", data, at)
            self.import_entries.append(tuple(cstring(data, names + off, self.source).decode("ascii")
                                             for off in (high, low)))
        self.code_references = defaultdict(list)
        for item in self.instructions:
            if item.opcode == 6:
                self.code_references[self.base + item.operand].append(item.offset + 2)
            if item.opcode == 0x15:
                require(item.operand < len(self.import_entries), "EXP SB call has invalid import index")
            targets = [item.operand] if item.opcode in (0x0e, 0x0f, 0x10, 0x12) else []
            if item.opcode == 0x25:
                targets = [u32(data, item.offset + 6)] + [u32(data, item.offset + 14 + i * 8)
                                                         for i in range(item.operand)]
                keys = [struct.unpack_from("<i", data, item.offset + 10 + i * 8)[0]
                        for i in range(item.operand)]
                require(keys == sorted(keys), "EXP SB switch keys are unsorted")
            require(all(self.code + pc * 2 in boundaries for pc in targets), "EXP SB branch has invalid PC")
        self.data_candidates = defaultdict(list)
        for at in range(self.base, self.pool, 4):
            self.data_candidates[self.base + u32(data, at)].append(at)

    def decode(self):
        at = self.code
        while at < self.base:
            require(at + 2 <= self.base, "Truncated EXP SB opcode")
            opcode = struct.unpack_from("<H", self.data, at)[0]
            require(opcode < len(LENGTHS), f"Unknown EXP SB opcode at {at:#x}")
            operand = None
            if LENGTHS[opcode] == 3 or opcode == 0x25:
                require(at + 6 <= self.base, "Truncated EXP SB operand")
                operand = u32(self.data, at + 2)
            size = (operand * 4 + 5) * 2 if opcode == 0x25 else LENGTHS[opcode] * 2
            require(size and at + size <= self.base, "EXP SB instruction crosses code boundary")
            yield Instruction(at, opcode, operand, size)
            at += size

    def texts(self):
        return [(at, raw) for at, raw in self.strings.items() if any(value >= 128 for value in raw)]


def render(raw):
    result, at = [], 0
    while at < len(raw):
        count = 2 if 0xa1 <= raw[at] <= 0xfe and at + 1 < len(raw) else 1
        unit = raw[at:at + count]
        try:
            char = unit.decode(ENCODING)
            require(len(char) == 1 and unit == char.encode(ENCODING) and
                    (ord(char) >= 32 and char not in "{}"), "Needs raw escape")
            result.append(char)
        except (UnicodeError, ValueError):
            result.append("{" + unit.hex().upper() + "}")
        at += count
    return "".join(result)


def encode(text, mapping=None):
    mapping = {} if mapping is None else mapping
    result, at = bytearray(), 0
    while at < len(text):
        escape = ESCAPE.match(text, at)
        if escape:
            result.extend(bytes.fromhex(escape[1]))
            at = escape.end()
        else:
            char = text[at]
            require(char not in "{}\r\n", "Use literal \\n for an EXP line break; braces require a raw escape")
            unit = mapping[char] if char in mapping else char.encode(ENCODING)
            require(char in mapping or len(unit) == 1 or len(unit) == 2 and all(0xa1 <= b <= 0xfe for b in unit),
                    "EXP renderer cannot read this Unicode character as a one/two-byte glyph")
            result.extend(unit)
            at += 1
    require(b"\0" not in result, "NUL is not allowed inside an EXP SB translation")
    return bytes(result)


def export_editor(data, name):
    lines = ["# FREAKS-EXP-SB: JPN-KOR v1", f"# SOURCE: {name}", f"# SHA256: {checksum(data)}",
             "# Empty KOR preserves original bytes. Keep JPN, IDs and control tokens.",
             "# Write literal \\n for a line break.", ""]
    for at, raw in Script(data).texts():
        lines.extend((f"[{at:08X}]", "[JPN]", render(raw), "[KOR]", "", "[END]", ""))
    return "\n".join(lines)


def parse_editor(content, data, name):
    content = content.removeprefix("\ufeff").replace("\r\n", "\n")
    require(content.startswith("# FREAKS-EXP-SB: JPN-KOR v1\n") and "\r" not in content, "Wrong EXP SB editor format")
    header = content.split("\n[", 1)[0]
    require(re.findall(r"^# SOURCE: (.+)$", header, re.M) == [name], "EXP SB editor/source mismatch")
    require(re.findall(r"^# SHA256: (.+)$", header, re.M) == [checksum(data)], "EXP SB editor/source hash differs")
    matches, records = list(BLOCK.finditer(content)), Script(data).texts()
    require(len(matches) == len(records), "Missing, duplicate or malformed EXP SB blocks")
    cursor, result = 0, {}
    for match, (at, raw) in zip(matches, records):
        require(all(not line or line.startswith("#") for line in content[cursor:match.start()].splitlines()),
                "Unexpected content outside EXP SB block")
        original = render(raw)
        require(int(match[1], 16) == at and match[2] == original, "EXP SB ID or JPN was changed")
        text = match[3]
        if text.strip() and text != original:
            require(CONTROL.findall(text) == CONTROL.findall(original),
                    f"EXP SB control tokens changed at {at:08X}")
            result[at] = text
        cursor = match.end()
    require(all(not line or line.startswith("#") for line in content[cursor:].splitlines()), "Unparsed EXP SB editor tail")
    return result


def rebuild(data, replacements):
    script = Script(data)
    require(set(replacements) <= script.strings.keys(), "EXP SB replacement is not an original string")
    trusted = {at for start, end in DATA_ARRAYS.get(checksum(data), ()) for at in range(start, end, 4)}
    output, changes = bytearray(data), []
    for at, value in sorted(replacements.items()):
        require(isinstance(value, bytes) and b"\0" not in value, "EXP SB replacement must be NUL-free bytes")
        old = script.strings[at]
        if value == old:
            continue
        destination, sites = at, []
        if len(value) <= len(old):
            output[at:at + len(old) + 1] = value + bytes(len(old) - len(value) + 1)
        else:
            candidates = script.data_candidates[at]
            require(set(candidates) <= trusted, f"Unverified EXP SB data reference blocks relocation at {at:08X}")
            sites = script.code_references[at] + candidates
            require(sites, f"EXP SB relocation has no proven reference at {at:08X}")
            destination = len(output)
            require(destination - script.base <= 0xffffffff, "EXP SB relative address overflow")
            output.extend(value + b"\0")
            for site in sites:
                struct.pack_into("<I", output, site, destination - script.base)
        changes.append({"offset": at, "destination": destination, "old_size": len(old),
                        "new_size": len(value), "reference_sites": sites})
    rebuilt = bytes(output)
    checked = Script(rebuilt)
    require(script.header == checked.header and script.export_entries == checked.export_entries
            and script.import_entries == checked.import_entries, "EXP SB rebuild changed structural metadata")
    for change in changes:
        at = change["offset"]
        require(cstring(rebuilt, change["destination"], len(rebuilt)) == replacements[at], "EXP SB rebuilt bytes differ")
        require(all(script.base + u32(rebuilt, site) == change["destination"] for site in change["reference_sites"]),
                "EXP SB rebuilt reference differs")
    return rebuilt, changes
