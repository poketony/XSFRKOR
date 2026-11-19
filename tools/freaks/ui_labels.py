"""Launcher notice and ADV labels with typed references and shared allocation."""

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import struct

from adv_format import control_signature, encode_text, render_text, require, text_end
from database_text import width
from elf_probe import Elf, signed16
from elf_text_slots import allocate
import system_text as system
from unpack_contents import digest

from build_paths import WORK


TABLES = ((0x178500, 4, 4, 0, "prefix", 200),
          (0x178580, 7, 4, 0, "name", 140),
          (0x178da8, 18, 12, 4, "place", 200),
          (0x1791b0, 4, 4, 0, "travel", 400))
DIRECT = ((0x17f0a8, 0x12a748, 0x12a74c, "no_data", 200),
          (0x17f0f8, 0x12c250, 0x12c258, "prompt", 500),
          (0x17f190, 0x12c40c, 0x12c418, "confirm", 500),
          (0x17f648, 0x12e054, 0x12e074, "quit", 600))
COMMANDS = ((0x1376a2, 0x136114), (0x1376ab, 0x13611d),
            (0x1376b4, 0x136126), (0x1376bd, 0x136131))
NOTICE_FRAME = 0x123b86
NOTICE_FRAME_PADDING = 24
NOTICE_FRAME_REFERENCES = (0x1242f5, 0x12435c, 0x1243bc, 0x1243f1, 0x12442c)
NOTICE_FRAME_STORAGE = 0x15e188
NOTICE_FRAME_POSITIONS = ((0x1242d6, -1, -264), (0x1242ee, -1, -20),
                          (0x124355, -1, -20), (0x1243cb, -1, -264),
                          (0x124401, -1, -20), (0x1244a3, 1, 11))


@dataclass(frozen=True)
class Label:
    address: int
    end: int
    raw: bytes
    kind: str
    budget: int
    pointers: tuple = ()
    pairs: tuple = ()
    commands: tuple = ()


def pair_address(data, elf, upper, lower):
    hi, lo = (struct.unpack_from("<I", data, elf.offset(address))[0] for address in (upper, lower))
    register = hi >> 16 & 31
    require(hi >> 26 == 15 and lo >> 26 == 9 and lo >> 21 & 31 == register
            and lo >> 16 & 31 == register, "UI address pair is not LUI/ADDIU in the same register")
    return ((hi & 65535) << 16) + signed16(lo)


def catalogue(data, component="ADV"):
    elf = Elf(data)
    if component == "LNCH":
        address, end, command = 0x123c3c, 0x123c67, 0x1244b9
        at = elf.offset(command)
        require(data[at] == 0x77 and struct.unpack_from("<I", data, at + 5)[0] == address
                and data[at + 13:at + 15] == b"\x19\x19", "Original launcher notice command differs")
        raw_at = elf.offset(address)
        require(text_end(data, raw_at) == elf.offset(end - 1), "Launcher notice literal boundary differs")
        return [Label(address, end, data[raw_at:elf.offset(end - 1)], "notice", 500,
                      (command + 5,), (), (command,))]
    require(component == "ADV", "Unknown UI label component")
    records = {}
    for table, count, stride, field, kind, budget in TABLES:
        for index in range(count):
            reference = table + index * stride + field
            address = struct.unpack_from("<I", data, elf.offset(reference))[0]
            at = elf.offset(address)
            raw = data[at:text_end(data, at)]
            if raw == b"\x03\x01":
                continue
            record = records.setdefault(address, [kind, budget, [], [], []])
            require(record[:2] == [kind, budget], "UI label has conflicting roles")
            record[2].append(reference)
    for address, upper, lower, kind, budget in DIRECT:
        require(pair_address(data, elf, upper, lower) == address, "Original UI direct address differs")
        records[address] = [kind, budget, [], [(upper, lower)], []]
    for command, address in COMMANDS:
        at = elf.offset(command)
        require(data[at] == 0x74 and struct.unpack_from("<I", data, at + 5)[0] == address,
                "Original text-draw command differs")
        records[address] = ["main_menu", 140, [command + 5], [], [command]]
    result = []
    for address, (kind, budget, pointers, pairs, commands) in sorted(records.items()):
        at = elf.offset(address)
        stop = text_end(data, at)
        end = address + stop - at + 1
        if kind != "main_menu":
            end = (end + 7) & ~7
        require(not any(data[stop + 1:elf.offset(end - 1) + 1]), "UI label padding contains other data")
        result.append(Label(address, end, data[at:stop], kind, budget, tuple(pointers), tuple(pairs), tuple(commands)))
    require(len(result) == 39, "ADV UI label catalogue count differs")
    return result


def export_text(data, component="ADV"):
    lines = ["# FREAKS-LABELS: JPN-KOR v1", f"# SHA256: {digest(data)}",
             "# Empty KOR keeps original. Preserve display controls; references are relocated automatically.", ""]
    for item in catalogue(data, component):
        lines.extend((f"# {item.kind.upper()} / {item.budget}px", f"[{item.address:08X}]", "[JPN]",
                      render_text(item.raw), "[KOR]", "", "[END]", ""))
    return "\n".join(lines)


def parse_editor(content, data, component="ADV"):
    content = content.removeprefix("\ufeff").replace("\r\n", "\n")
    require("\r" not in content and content.startswith("# FREAKS-LABELS: JPN-KOR v1\n"), "Wrong UI label editor format")
    require(re.findall(r"^# SHA256: (.+)$", content, re.M) == [digest(data)], "UI label source hash differs")
    items = catalogue(data, component)
    matches = list(system.BLOCK.finditer(content))
    require(len(matches) == len(items), "Missing, duplicate or malformed UI label blocks")
    cursor, result = 0, {}
    for match, item in zip(matches, items):
        require(all(not line or line.startswith("#") for line in content[cursor:match.start()].splitlines()),
                "Unexpected content outside UI label block")
        require(int(match[1], 16) == item.address and match[2] == render_text(item.raw), "UI label ID/JPN changed")
        result[item.address] = match[3]
        cursor = match.end()
    require(all(not line or line.startswith("#") for line in content[cursor:].splitlines()), "Unparsed UI label editor tail")
    return result


def validate(raw, item, *, measure=width):
    require(control_signature(raw) == control_signature(item.raw), "UI label display controls changed")
    lines = raw.split(b"\n")
    require(len(lines) <= (5 if item.kind in ("confirm", "quit") else 1), "UI label has too many lines")
    require(all(len(line) < 127 and measure(line) <= item.budget for line in lines),
            f"UI label exceeds its line/width budget: {item.address:08X}")


def resize_notice_frame(original, output, pixels):
    elf = Elf(original)
    at = elf.offset(NOTICE_FRAME)
    require(original[at:at + 18] == struct.pack("<9H", 1, 0x60, 0, 0, 390, 40, 0x200, 488, 140),
            "Original notice frame rectangle differs")
    half = max(195, (pixels + NOTICE_FRAME_PADDING + 1) // 2)
    require(half <= 320, "Notice frame exceeds screen width")
    for command in NOTICE_FRAME_REFERENCES:
        require(original[elf.offset(command):elf.offset(command) + 5]
                == b"\x47" + struct.pack("<I", NOTICE_FRAME), "Original notice model reference differs")
    model = NOTICE_FRAME
    if half > 195:
        # The compact rectangle derives UV span from XY span. Use the original
        # triangle strip's explicit-UV form, keeping every texture sample fixed.
        primitive = struct.pack("<19H", 1, 0x65, 0, 0, half * 2, 0, 0, 40, half * 2, 40,
                                0x200, 488, 140, 878, 140, 488, 180, 878, 180)
        # Use the verified 120-byte .rodata alignment gap before .lit4. This is
        # data in the existing PT_LOAD, not executable slack or a live C string.
        section = elf.named[".rodata"]
        following = elf.named[".lit4"]
        require(section[3] + section[5] == NOTICE_FRAME_STORAGE and following[3] == 0x15e200
                and section[4] + section[5] + 120 == following[4],
                "Original notice model storage padding differs")
        model = NOTICE_FRAME_STORAGE
        model_at = section[4] + section[5]
        require(not any(original[model_at:following[4]])
                and output[model_at:following[4]] == original[model_at:following[4]],
                "Notice model storage padding is occupied")
        phoff = struct.unpack_from("<I", original, 28)[0]
        stride, count = struct.unpack_from("<HH", original, 42)
        segments = [struct.unpack_from("<8I", original, phoff + i * stride) for i in range(count)]
        require(any(p[0] == 1 and p[2] <= model and model + len(primitive) <= p[2] + p[4]
                    and p[1] + model - p[2] == model_at for p in segments),
                "Notice model is outside an existing file-backed PT_LOAD")
        output[model_at:model_at + len(primitive)] = primitive
        shoff = struct.unpack_from("<I", original, 32)[0]
        shsize = struct.unpack_from("<H", original, 46)[0]
        struct.pack_into("<I", output, shoff + elf.sections.index(section) * shsize + 20,
                         section[5] + len(primitive))
        for command in NOTICE_FRAME_REFERENCES:
            struct.pack_into("<I", output, elf.offset(command) + 1, model)
    # Move both animation origins and the opposite-edge child together.
    for command, sign, y in NOTICE_FRAME_POSITIONS:
        at = elf.offset(command)
        require(original[at:at + 7] == b"\x53" + struct.pack("<3h", sign * 195, y, 0),
                "Original notice frame position command differs")
        struct.pack_into("<h", output, at + 1, sign * half)
    return {"width": half * 2, "height": 40, "center_x": 0, "text_width": pixels,
            "minimum_side_padding": (half * 2 - pixels) // 2, "model": f"{model:08X}",
            "uv_span": [390, 40], "model_references": len(NOTICE_FRAME_REFERENCES)}


def rebuild(data, system_translations, translations, mapping=None, component="ADV", *, measure=width):
    elf = Elf(data)
    items = catalogue(data, component)
    require(set(translations) <= {item.address for item in items}, "Unknown UI label ID")
    encoded = system.encode_changes(data, component, system_translations, mapping, measure=measure)
    system_items, replacements, _ = encoded
    replacements = dict(replacements)
    label_changes = {}
    for item in items:
        text = translations.get(item.address, "")
        if text and text != render_text(item.raw):
            raw = encode_text(text, mapping)
            validate(raw, item, measure=measure)
            replacements[item.address] = label_changes[item.address] = raw
    if component == "ADV":
        tail = next(item for item in items if item.kind == "confirm")
        tail_raw = label_changes.get(tail.address, tail.raw)
        for item in items:
            if item.kind == "name":
                combined = label_changes.get(item.address, item.raw) + tail_raw
                require(len(combined) < 256 and len(combined.split(b"\n")) <= 5,
                        "Character confirmation exceeds the 256-byte stack buffer")
                require(all(len(line) < 127 and measure(line) <= 500 for line in combined.split(b"\n")),
                        "Character confirmation exceeds its combined line/width budget")
    slots = {item.address: item.end for item in system_items if item.kind != "bios"}
    for item in items:
        require(item.address not in slots, "System and UI text slots overlap")
        slots[item.address] = item.end
    ordered = sorted(slots.items())
    require(all(end <= following for (_, end), (following, _) in zip(ordered, ordered[1:])), "UI string allocations overlap")
    output, positions = allocate(data, elf, slots, replacements, "system/UI")
    _, system_report = system.apply_changes(data, encoded, output, positions)
    for item in items:
        destination = positions[item.address]
        for pointer in item.pointers:
            struct.pack_into("<I", output, elf.offset(pointer), destination)
        for upper, lower in item.pairs:
            for address, value in ((upper, (destination + 0x8000) >> 16), (lower, destination & 65535)):
                word = struct.unpack_from("<I", data, elf.offset(address))[0]
                struct.pack_into("<I", output, elf.offset(address), word & 0xffff0000 | value)
            require(pair_address(output, elf, upper, lower) == destination, "UI address pair relocation differs")
        expected = label_changes.get(item.address, item.raw)
        require(output[elf.offset(destination):text_end(output, elf.offset(destination))] == expected, "UI label readback differs")
    require(len(output) == len(data), "UI label ELF size changed")
    report = {"changed": [f"{a:08X}" for a in sorted(label_changes)],
              "positions": {f"{item.address:08X}": f"{positions[item.address]:08X}" for item in items},
              "texts": len(items)}
    if component == "LNCH" and label_changes:
        report["notice_frame"] = resize_notice_frame(data, output, measure(next(iter(label_changes.values()))))
    if component == "ADV" and measure(b"\x81\x40") < 20:
        from adv_spacing import ui_layout
        report["layout_spacing"] = ui_layout(data, output,
                                             (positions[address] for address in slots))
    if component == "ADV":
        from adv_menu_center import recenter
        report["menu_center"] = recenter(data, output, items, positions, measure=measure)
    return bytes(output), system_report, report


def editor_path(component):
    return WORK / "text" / f"LABELS_{component}.txt"


def load_editors():
    return {component: editor_path(component).read_text(encoding="utf-8") for component in system.COMPONENTS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("export", "verify"))
    parser.add_argument("--font", type=Path)
    args = parser.parse_args()
    if args.mode == "verify":
        from rebuild_pipeline import execute
        execute("verify", args.font)
        return
    for component in system.COMPONENTS:
        data = system.original_data(component)
        path = editor_path(component)
        if not path.exists():
            from font_tool import write_generated
            write_generated(path, export_text(data, component).encode("utf-8"))
        parsed = parse_editor(path.read_text(encoding="utf-8"), data, component)
        print(f"[PASS] {component}: {len(parsed)} UI label blocks; existing KOR preserved")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"[ERROR] {error}")
