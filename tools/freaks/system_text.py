"""Audited launcher/ADV memory-card messages and option descriptions."""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import struct

from adv_format import control_signature, encode_text, render_text, require, text_end, tokens
from database_text import width
from elf_probe import Elf
from elf_text_slots import allocate
from unpack_contents import digest

from build_paths import DATA, REPO, WORK


COMPONENTS = {
    "LNCH": {"file": "SLPS_253.53", "message": (0x156650, 39), "option": (0x1568c8, 6),
             "getter": 0x122648, "option_entry": 0x128288, "option_gp": -0x7bf0,
             "option_draw": 0x129980, "parser": 0x1234e8, "bios": 0x15bbe0, "bios_copy": 0x11bd60},
    "ADV": {"file": "ADV/ADV.ELF", "message": (0x1783b0, 37), "option": (0x179758, 8),
            "getter": 0x128b88, "option_entry": 0x13d2a8, "option_gp": -0x7a10,
            "option_draw": 0x126ed8, "parser": 0x1274e8, "bios": 0x17f040, "bios_copy": 0x11bec0},
}
BLOCK = re.compile(r"^\[([0-9A-F]{8})\]\n\[JPN\]\n(.*?)\n\[KOR\]\n(.*?)\n\[END\](?:\n|$)", re.M | re.S)


@dataclass(frozen=True)
class Text:
    address: int
    end: int
    raw: bytes
    kind: str
    references: tuple


def original_data(component):
    name = COMPONENTS[component]["file"]
    inventory = json.loads((DATA / "disc-inventory.json").read_text(encoding="utf-8"))
    expected = next(r["sha256"] for r in inventory["files"] if r["path"] == name)
    data = (REPO / "unpacked" / name).read_bytes()
    require(digest(data) == expected, f"Original system ELF hash changed: {component}")
    return data


def buffer_shape(data, component):
    elf = Elf(data)
    entry = COMPONENTS[component]["parser"]
    word = lambda address: struct.unpack_from("<I", data, elf.offset(address))[0]
    from elf_probe import signed16
    require(word(entry + 0xd0) >> 16 == 0x3c0a and word(entry + 0xd8) >> 16 == 0x2547,
            "System text buffer setup differs")
    base = ((word(entry + 0xd0) & 65535) << 16) + signed16(word(entry + 0xd8))
    require(word(entry + 0xf0) >> 16 == 0x28a3, "System text row limit differs")
    rows = word(entry + 0xf0) & 65535
    require(rows == (7 if component == "LNCH" else 5), "Unexpected system text row budget")
    return base, rows


def catalogue(data, component):
    elf = Elf(data)
    settings = COMPONENTS[component]
    result = []
    for kind in ("message", "option"):
        table, count = settings[kind]
        pointers = struct.unpack_from(f"<{count}I", data, elf.offset(table))
        require(struct.unpack_from("<I", data, elf.offset(table + count * 4))[0] == 0,
                "System text table sentinel differs")
        unique = {}
        for index, address in enumerate(pointers):
            offset = elf.offset(address)
            raw = data[offset:text_end(data, offset)]
            if raw == b"\x0b\x0c":
                continue
            require(raw.startswith(b"\x0b\x0c" if kind == "message" else b"\x0b"),
                    "System text does not have the expected display controls")
            unique.setdefault(address, []).append(table + index * 4)
        addresses = sorted(unique)
        last = addresses[-1]
        high = (text_end(data, elf.offset(last)) - elf.offset(last) + last + 8) & ~7
        for address, end in zip(addresses, addresses[1:] + [high]):
            offset = elf.offset(address)
            stop = text_end(data, offset)
            require(stop < elf.offset(end - 1) + 1 and not any(data[stop + 1:elf.offset(end - 1) + 1]),
                    "System text pool contains non-string data")
            result.append(Text(address, end, data[offset:stop], kind, tuple(unique[address])))
    address = settings["bios"]
    stop = text_end(data, elf.offset(address))
    end = address + 24
    require(stop - elf.offset(address) == 20 and not any(data[stop + 1:elf.offset(end - 1) + 1]),
            "Original memory-card title allocation differs")
    result.append(Text(address, end, data[elf.offset(address):stop], "bios", ()))
    require(len({item.address for item in result}) == len(result), "System text IDs overlap")
    return sorted(result, key=lambda item: item.address)


def export_text(data, component):
    lines = ["# FREAKS-SYSTEM: JPN-KOR v1", f"# COMPONENT: {component}", f"# SHA256: {digest(data)}",
             "# Empty KOR keeps original. Preserve controls; BIOS titles cannot use custom Hangul codes.", ""]
    for item in catalogue(data, component):
        lines.extend((f"# {item.kind.upper()} / {len(item.raw)} original bytes / {item.end-item.address-1} local bytes",
                      f"[{item.address:08X}]", "[JPN]", render_text(item.raw), "[KOR]", "", "[END]", ""))
    return "\n".join(lines)


def parse_editor(content, data, component):
    content = content.removeprefix("\ufeff").replace("\r\n", "\n")
    require("\r" not in content and content.startswith("# FREAKS-SYSTEM: JPN-KOR v1\n"), "Wrong system editor format")
    require(re.findall(r"^# COMPONENT: (.+)$", content, re.M) == [component]
            and re.findall(r"^# SHA256: (.+)$", content, re.M) == [digest(data)], "System editor/source mismatch")
    matches = list(BLOCK.finditer(content))
    expected = catalogue(data, component)
    require(len(matches) == len(expected), "Missing, duplicate or malformed system text blocks")
    result, cursor = {}, 0
    for match, item in zip(matches, expected):
        require(all(not line or line.startswith("#") for line in content[cursor:match.start()].splitlines()),
                "Unexpected content outside system text block")
        require(int(match[1], 16) == item.address, "System text IDs changed or reordered")
        require(match[2] == render_text(item.raw), f"System JPN changed: {item.address:08X}")
        result[item.address] = match[3]
        cursor = match.end()
    require(all(not line or line.startswith("#") for line in content[cursor:].splitlines()), "Unparsed system editor tail")
    return result


def validate(raw, item, data, component, *, measure=width):
    require(control_signature(raw) == control_signature(item.raw), "System display controls changed")
    if item.kind == "bios":
        require(len(raw) < 24 and all(not control for control, _ in tokens(raw)), "Memory-card title exceeds its fixed allocation")
        return
    _, rows = buffer_shape(data, component)
    lines = raw.split(b"\n")
    require(len(lines) <= (rows if item.kind == "message" else 1), "System text has too many lines")
    require(all(len(line) < 127 for line in lines), "System line exceeds its 128-byte buffer")
    budget = 500 if item.kind == "message" else (460 if component == "LNCH" else 440)
    require(all(measure(line) <= budget for line in lines), f"System line exceeds {budget}px; insert a line break")


def encode_changes(data, component, translations, mapping=None, *, measure=width):
    items = catalogue(data, component)
    require(set(translations) <= {item.address for item in items}, "Unknown system text ID")
    replacements, bios = {}, {}
    for item in items:
        text = translations.get(item.address, "")
        if not text or text == render_text(item.raw):
            continue
        if item.kind == "bios":
            try:
                raw = text.encode("cp932")
            except UnicodeEncodeError as error:
                raise ValueError("Memory-card title uses the PS2 system font; use CP932/ASCII, not custom Hangul codes") from error
            bios[item.address] = raw
        else:
            raw = encode_text(text, mapping)
            replacements[item.address] = raw
        validate(raw, item, data, component, measure=measure)
    return items, replacements, bios


def apply_changes(data, encoded, output, positions):
    items, replacements, bios = encoded
    elf = Elf(data)
    for item in items:
        if item.kind == "bios":
            if item.address in bios:
                raw = bios[item.address]
                output[elf.offset(item.address):elf.offset(item.end - 1) + 1] = raw + bytes(24 - len(raw))
            continue
        address = positions[item.address]
        for field in item.references:
            struct.pack_into("<I", output, elf.offset(field), address)
        raw = replacements.get(item.address, item.raw)
        require(output[elf.offset(address):text_end(output, elf.offset(address))] == raw, "System text readback differs")
    require(len(output) == len(data), "System ELF size changed")
    return bytes(output), {"changed": [f"{a:08X}" for a in sorted(replacements.keys() | bios.keys())],
                           "moved": [{"from": f"{item.address:08X}", "to": f"{positions[item.address]:08X}"}
                                     for item in items if item.kind != "bios" and positions[item.address] != item.address],
                           "texts": len(items)}


def rebuild(data, component, translations, mapping=None):
    encoded = encode_changes(data, component, translations, mapping)
    items, replacements, _ = encoded
    elf = Elf(data)
    slots = {item.address: item.end for item in items if item.kind != "bios"}
    output, positions = allocate(data, elf, slots, replacements, "system")
    return apply_changes(data, encoded, output, positions)


def editor_path(component):
    return WORK / "text" / f"SYSTEM_{component}.txt"


def load_editors():
    return {component: editor_path(component).read_text(encoding="utf-8") for component in COMPONENTS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("export", "verify"))
    parser.add_argument("--font", type=Path)
    args = parser.parse_args()
    if args.mode == "verify":
        from rebuild_pipeline import execute
        execute("verify", args.font)
        return
    for component in COMPONENTS:
        data = original_data(component)
        path = editor_path(component)
        if args.mode == "export":
            content = export_text(data, component)
            require(not path.exists() or path.read_text(encoding="utf-8") == content,
                    f"Existing system translation preserved: {path}")
            if not path.exists():
                from font_tool import write_generated
                write_generated(path, content.encode("utf-8"))
        translated = parse_editor(path.read_text(encoding="utf-8"), data, component)
        print(f"[PASS] {component}: {len(translated)} texts; {sum(bool(t) for t in translated.values())} KOR blocks")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"[ERROR] {error}")
