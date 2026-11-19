"""EXP embedded-text lab builder; typed controls and numeric writer positions."""

from dataclasses import dataclass
import json
import re
import struct

import audit_exp_strings as audit
from elf_probe import Elf, signed16
import exp_sb as sb
import exp_literal_relocation as literal

from build_paths import DATA, REPO
from unpack_disc import require


TOKEN = re.compile(r"\{(?:COUNT|CAMPAIGN|[0-9A-F]{2}(?::(?:[0-9A-F]{2})+)?)\}")
EDITOR = "EXP/Executable.txt"
FORMAT = re.compile(r"%(?:%|d)")
MAIN_GROUPS = {
    "Pause / movie": (0x2b20c0, 0x2b2160, 0x2b2180, 0x2b21a0, 0x2b21c0, 0x2b21f8, 0x2b2228),
    "Movie error / ending save": (0x682bd0, 0x682c48, 0x682cc0, 0x682d70, 0x682dc0, 0x682e50),
    "Acquisition / segment": (0x683000, 0x684540, 0x684568, 0x684598, 0x696998),
    "Campaign / resident album": (0x3e6f88, 0x684840, 0x684998, 0x6849f0, 0x684ab8, 0x684c90,
                                  0x696dd0, 0x696de0, 0x696de8, 0x696df0),
    "Shop / HDD": (0x686d28, 0x686dc0, 0x697230, 0x697268, 0x697270, 0x687928),
}
BOOT = (0xa82a90, 0xa82af8, 0xa82b48, 0xa82b90, 0xa82bd8, 0xa82c30, 0xa82c90,
        0xa82df8, 0xa82ee8, 0xa82f08, 0xa83088, 0xa830f8, 0xa83140, 0xa831b0,
        0xa83228, 0xa83278, 0xa83308, 0xa833f8, 0xa83430, 0xa83460, 0xa834a8)
NUMBERS = {0x683000: ("COUNT", 2, b"00"), 0x696998: ("COUNT", 2, b"00"),
           0x3e6f88: ("CAMPAIGN", 18, b"01")}
# These complete, read-only renderer inputs have one verified base-pointer consumer each.
DONORS = (0x682bd0, 0x687928)


@dataclass(frozen=True)
class Entry:
    address: int
    group: str
    raw: bytes
    text: str
    span: int


def originals():
    result = {name: (REPO / "unpacked/EXP" / name).read_bytes()
              for name in ("EXP.ELF", "OV01.OVL", "OV02.OVL")}
    validate_sources(result)
    return result


def validate_sources(sources):
    inventory = json.loads((DATA / "disc-inventory.json").read_text(encoding="utf-8"))
    require(set(sources) == {"EXP.ELF", "OV01.OVL", "OV02.OVL"}, "EXP embedded source set differs")
    for name in ("EXP.ELF", "OV01.OVL", "OV02.OVL"):
        expected = next(row["sha256"] for row in inventory["files"] if row["path"] == "EXP/" + name)
        require(sb.checksum(sources[name]) == expected, f"Original EXP embedded text source differs: {name}")
    boot_sections = list(audit.sections(sources["OV02.OVL"]))
    require(len(boot_sections) == 1 and boot_sections[0][0] == "ov02"
            and boot_sections[0][1][3:6] == (0xa80000, 0x1000, 0x36e8), "EXP boot section differs")
    main = Elf(sources["EXP.ELF"])
    require(sources["EXP.ELF"][main.offset(0xa80000):main.offset(0xa836e7) + 1]
            == sources["OV02.OVL"][0x1000:0x46e8],
            "EXP embedded/external boot copies differ")


def entries(sources):
    validate_sources(sources)
    data = sources["EXP.ELF"]
    elf = Elf(data)
    result = []
    for group, addresses in (*MAIN_GROUPS.items(), ("Boot memory card / HDD", BOOT)):
        for address in addresses:
            at = elf.offset(address)
            raw, text, _ = audit.control_text(data, at)
            require(raw, "Empty EXP embedded editor source")
            span = (len(raw) + 8) & ~7
            require(data[at + len(raw):at + span] == bytes(span - len(raw)),
                    f"EXP embedded string padding differs: {address:08X}")
            if address in NUMBERS:
                _, position, digits = NUMBERS[address]
                require(raw[position:position + 2] == digits, "Original EXP numeric position differs")
            result.append(Entry(address, group, raw, text, span))
    ordered = sorted(result, key=lambda row: row.address)
    require(all(a.address + a.span <= b.address for a, b in zip(ordered, ordered[1:])),
            "EXP embedded editor spans overlap")
    return tuple(result)


def encode(text, entry, mapping=None):
    require(not any(char in text for char in "\r\n\t\0$&"),
            "EXP embedded text rejects physical controls and unverified renderer commands")
    output, at, number = bytearray(), 0, None
    for match in TOKEN.finditer(text):
        output.extend(plain(text[at:match.start()], mapping))
        token = match[0][1:-1]
        if token in ("COUNT", "CAMPAIGN"):
            require(entry.address in NUMBERS and token == NUMBERS[entry.address][0] and number is None,
                    "Wrong or repeated EXP numeric placeholder")
            number = len(output)
            output.extend(NUMBERS[entry.address][2])
        else:
            opcode, _, parameters = token.partition(":")
            raw = bytes.fromhex(opcode + parameters)
            require(raw[0] != 0 and raw[0] < 32 and audit.parameter_count(raw + b"\0", 0) == len(raw) - 1,
                    "EXP embedded control parameter count differs")
            output.extend(raw)
        at = match.end()
    output.extend(plain(text[at:], mapping))
    raw = bytes(output)
    full, rendered, _ = audit.control_text(raw + b"\0", 0)
    require(full == raw, "EXP embedded translation ends inside a control or glyph")
    original_controls = TOKEN.findall(entry.text)
    require([token for token in TOKEN.findall(rendered)] == original_controls,
            "EXP embedded control sequence changed; preserve every original control token")
    require(FORMAT.findall(text) == FORMAT.findall(entry.text)
            and text.count("%") == entry.text.count("%"), "EXP embedded format arguments changed")
    require(text.count("\\n") == entry.text.count("\\n"), "EXP embedded literal line breaks changed")
    if entry.address in NUMBERS:
        require(number is not None and number < 0x7fff, "Keep one typed EXP numeric placeholder")
    else:
        require(number is None, "Unexpected EXP numeric placeholder")
    return raw, number


def plain(text, mapping):
    require(all(ord(char) >= 32 and ord(char) != 127 for char in text), "EXP embedded literal control rejected")
    require("{" not in text and "}" not in text and "\\" not in text.replace("\\n", ""),
            "Malformed EXP embedded token or backslash command")
    result = bytearray()
    for char in text:
        if "\uac00" <= char <= "\ud7a3":
            require(mapping is not None and char in mapping, "EXP embedded Hangul is not in the shared font map")
            result.extend(mapping[char])
        else:
            result.extend(char.encode(sb.ENCODING))
    return bytes(result)


def export_editor(sources):
    rows = ["# FREAKS-EXP-EXECUTABLE: JPN-KOR v1", "# Empty KOR preserves original.",
            "# Original control tokens, formats and literal \\n counts are protected.",
            "# Numeric KOR requires {COUNT} or {CAMPAIGN}; the builder updates digit writers."]
    rows.extend(f"# SHA256 {name}: {sb.checksum(data)}" for name, data in sources.items())
    group = None
    for entry in entries(sources):
        if entry.group != group:
            group = entry.group
            rows.extend(("", "# " + group))
        rows.extend(("", f"# Original slot: {entry.span - 1} bytes", f"[{entry.address:08X}]", "[JPN]",
                     entry.text, "[KOR]", "", "[END]"))
    return "\n".join(rows) + "\n"


def parse_editor(content, sources):
    content = content.replace("\r\n", "\n")
    template = export_editor(sources)
    originals = list(sb.BLOCK.finditer(template))
    blocks = list(sb.BLOCK.finditer(content))
    require(len(blocks) == len(originals), "EXP embedded editor block count differs")
    parts, previous, result = [], 0, {}
    for block, original in zip(blocks, originals):
        require(block[1] == original[1] and block[2] == original[2], "EXP embedded ID/JPN differs")
        parts.extend((content[previous:block.start(3)], ""))
        previous = block.end(3)
        if block[3] and block[3] != block[2]:
            require("\r" not in block[3] and "\n" not in block[3], "Use tokens/literal \\n, not physical embedded-text lines")
            result[int(block[1], 16)] = block[3]
    parts.append(content[previous:])
    require("".join(parts) == template, "EXP embedded source metadata or layout differs")
    return result


def patch_word(output, elf, address, expected, replacement):
    at = elf.offset(address)
    require(struct.unpack_from("<I", output, at)[0] == expected,
            f"EXP embedded patch source differs: {address:08X}")
    struct.pack_into("<I", output, at, replacement)


def pointer_pair(output, elf, high_at, low_at, target):
    high = struct.unpack_from("<I", output, elf.offset(high_at))[0]
    low = struct.unpack_from("<I", output, elf.offset(low_at))[0]
    require(high >> 26 == 15 and low >> 26 == 9 and (high >> 16 & 31) == (low >> 21 & 31),
            "EXP embedded pointer instructions differ")
    patch_word(output, elf, high_at, high, high & 0xffff0000 | ((target + 0x8000) >> 16))
    patch_word(output, elf, low_at, low, low & 0xffff0000 | target & 0xffff)


def short_count_block(target, position):
    # Build the same final global message directly, instead of copying a temporary
    # stack message to it. The temporary is not read after this original block.
    high, low = (target + 0x8000) >> 16, target & 0xffff
    words = (0x3c020000 | high, 0x24420000 | low, 0x00001812, 0x00003810,
             0x24630030, 0x24e70030, 0xa0430000 | position, 0xa0470000 | (position + 1),
             0x3c04003e, 0x248419b0, 0x0c000000 | 0x2a53e8 // 4, 0x0280282d,
             0x3c050000 | high, 0x24a50000 | low, 0x0c000000 | 0x2a4fe8 // 4, 0x0040202d, 0)
    return struct.pack("<17I", *words)


def reference_candidates(sources, targets):
    """Retain even unproven references into the bounded text ranges supplied."""
    found = set()
    main = Elf(sources["EXP.ELF"])
    for name, data in sources.items():
        sections = main.sections if name == "EXP.ELF" else [section for _, section in audit.sections(data)]
        for section in sections:
            if not section[2] & 2 or section[1] == 8:
                continue
            words = struct.unpack_from(f"<{section[5] // 4}I", data, section[4])
            for index, word in enumerate(words):
                if word in targets:
                    found.add(word)
                if not section[2] & 4:
                    continue
                if word >> 21 & 31 == 28 and main.gp + signed16(word) in targets:
                    found.add(main.gp + signed16(word))
                if word >> 26 != 15:
                    continue
                register = word >> 16 & 31
                for low in words[index + 1:index + 17]:
                    if low >> 21 & 31 != register or low >> 26 not in (9, 13, 0x19, 0x20, 0x21, 0x23,
                                                                           0x24, 0x25, 0x28, 0x29, 0x2b):
                        continue
                    target = ((word & 0xffff) << 16) + (low & 0xffff if low >> 26 == 13 else signed16(low))
                    if target in targets:
                        found.add(target)
    return found


def tail_has_reference(sources, start, end):
    return bool(reference_candidates(sources, set(range(start, end))))


def rebuild(sources, replacements, mapping=None):
    rows = {row.address: row for row in entries(sources)}
    require(set(replacements) <= set(rows), "Unknown EXP embedded translation target")
    prepared = {address: encode(replacements[address], rows[address], mapping) for address in sorted(replacements)}
    prepared = {address: item for address, item in prepared.items() if item[0] != rows[address].raw}
    interiors = {target for address in prepared for target in range(address + 1, address + rows[address].span)}
    references = reference_candidates(sources, interiors) if interiors else set()
    require(not references,
            "EXP embedded string has an interior reference candidate; dedicated analysis needed: "
            + ", ".join(f"{address:08X}" for address in sorted(references)))
    output = bytearray(sources["EXP.ELF"])
    elf = Elf(output)
    locations = {address: address for address in prepared}
    loans, donors = [], []
    for address in DONORS:
        if address not in prepared:
            continue
        row, raw = rows[address], prepared[address][0]
        if len(raw) + 1 < row.span:
            start, end = address + len(raw) + 1, address + row.span
            donors.append([start, end, address])
    for address, (raw, _) in prepared.items():
        if len(raw) < rows[address].span:
            continue
        if address not in NUMBERS:
            require(address in literal.RELOCATABLE,
                    f"EXP embedded slot exceeded: {address:08X} ({len(raw)} > {rows[address].span - 1}); "
                    "formatted/composed consumers keep their original slot limits")
            continue
        available = [item for item in donors if item[1] - item[0] >= len(raw) + 1]
        require(available, f"EXP numeric fragment {address:08X} needs {len(raw) + 1} bytes; no verified shortened message tail is available")
        donor = min(available, key=lambda item: abs(item[0] - address))
        locations[address] = donor[0]
        loans.append({"source": address, "target": donor[0], "donor": donor[2], "bytes": len(raw) + 1})
        donor[0] += len(raw) + 1
    if any(a in literal.RELOCATABLE and len(raw) >= rows[a].span for a, (raw, _) in prepared.items()):
        literal.validate_candidates(sources, rows)
    locations, literal_moves, extra, literal_clear = literal.allocate(rows, prepared, locations, loans)
    # Clear each changed owned span first, then insert any borrowed strings.
    for address in set(prepared) | literal_clear:
        at = elf.offset(address)
        output[at:at + rows[address].span] = bytes(rows[address].span)
    writes = dict(prepared)
    writes.update({address: (raw, None) for address, raw in extra.items()})
    for address, (raw, position) in writes.items():
        at = elf.offset(locations[address])
        output[at:at + len(raw) + 1] = raw + b"\0"
        if address == 0x683000:
            pointer_pair(output, elf, 0x1ee07c, 0x1ee09c, locations[address])
            for instruction, old, digit in ((0x1ee4fc, 0xa2c30002, 0), (0x1ee504, 0xa2c60003, 1)):
                patch_word(output, elf, instruction, old, old & 0xffff0000 | (position + digit))
        elif address == 0x3e6f88:
            pointer_pair(output, elf, 0x2295ac, 0x2295b0, locations[address])
            for instruction, old, digit in ((0x2295e0, 0xa0c30012, 0), (0x2295ec, 0xa0c20013, 1)):
                patch_word(output, elf, instruction, old, old & 0xffff0000 | (position + digit))
        elif address == 0x696998:
            target = locations[address]
            if -0x8000 <= target - elf.gp < 0x8000:
                pointer_pair(output, elf, 0x1fa458, 0x1fa460, target)
                patch_word(output, elf, 0x1fa480, 0x2785a4a8, 0x27850000 | (target - elf.gp) & 0xffff)
                for instruction, old, digit in ((0x1fa474, 0xa0430002, 0), (0x1fa47c, 0xa0470003, 1)):
                    patch_word(output, elf, instruction, old, old & 0xffff0000 | (position + digit))
            else:
                start = elf.offset(0x1fa458)
                output[start:start + 0x44] = short_count_block(target, position)
    literal_sites = literal.patch_references(output, elf, locations)
    for address, (raw, _) in writes.items():
        require(audit.control_text(output, elf.offset(locations[address]))[0] == raw,
                "EXP embedded relocated text readback differs")
    boot = bytearray(sources["OV02.OVL"])
    changed_boot = [address for address in prepared if address in BOOT]
    if changed_boot:
        boot[0x1000:0x46e8] = output[elf.offset(0xa80000):elf.offset(0xa836e7) + 1]
    require(len(output) == len(sources["EXP.ELF"]) and len(boot) == len(sources["OV02.OVL"]),
            "EXP embedded build changed executable lengths")
    require(output[elf.offset(0xa80000):elf.offset(0xa836e7) + 1]
            == boot[0x1000:0x46e8], "EXP boot copies diverged")
    return bytes(output), bytes(boot), {
        "changed": [{"address": address, "target": locations[address], "bytes": len(raw), "number_at": number}
                    for address, (raw, number) in sorted(prepared.items())],
        "tail_loans": loans, "paired_ov02": bool(changed_boot), "editors": len(rows),
        "literal_relocations": literal_moves, "literal_reference_sites": literal_sites,
        "relocated_untranslated": [address for address in sorted(extra) if address not in prepared and locations[address] != address],
        "elf_and_ov02_size_unchanged": True,
        "scope": "In-memory builder; 46 reviewed literals may reuse owned same-bank text spans; six formatted/composed texts stay fixed; three numeric strings keep dedicated writers; no game outputs written"}
