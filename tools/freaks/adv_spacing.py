"""Preserve fixed ADV layout padding without widening dialogue indentation."""

from adv_format import parse, require, text_end, tokens
from elf_probe import Elf
from gothic import Gothic, available_codes, metric_records
import struct


FULL_SPACE = b"\x81\x40"
LAYOUT_SPACE = b"\xf0\x41"
CHOICE_ADDRESS = 0x17e3e0
CHOICE_RAW = bytes.fromhex("0a 0c 81 40 20 81 40 81 40 81 40 81 40 81 40 81 40 "
                           "82 78 82 64 82 72 81 40 81 40 82 6d 82 6e 0d")


def preserve_runs(raw):
    """Keep 2+ fixed-face spaces at 20px without changing byte or token counts."""
    if FULL_SPACE * 2 not in raw:
        return raw
    items = list(tokens(raw))
    output, at, style = bytearray(), 0, 0
    while at < len(items):
        control, code = items[at]
        if not control and code == FULL_SPACE:
            end = at + 1
            while end < len(items) and items[end] == (False, FULL_SPACE):
                end += 1
            count = end - at
            output.extend(LAYOUT_SPACE * count if count >= 2 and style == 0 else FULL_SPACE * count)
            at = end
            continue
        if control and code[0] == 1:
            style = code[1]
        elif code == b"\n":
            style = 0
        output.extend(code)
        at += 1
    require(len(output) == len(raw), "ADV spacing compensation changed a text allocation")
    return bytes(output)


def install_layout_space(data):
    font = Gothic(data)
    require(LAYOUT_SPACE in available_codes(font), "ADV layout-space code is already in use")
    bitmap = font.bitmap(1)
    images = {code: bitmap[i * 200:(i + 1) * 200] for i, code in enumerate(font.codes(1))}
    images[LAYOUT_SPACE] = bytes(200)
    require(font.codes(0) == font.codes(2) and font.bitmap(0) == font.bitmap(2)
            and font.codes(1) == font.codes(3) and font.bitmap(1) == font.bitmap(3),
            "ADV spacing expects shared fixed/proportional glyph bitmaps")
    output = bytearray(data[:1176])

    def append(raw):
        at = len(output)
        output.extend(raw)
        return at

    half_codes = append(b"".join(font.codes(0)))
    full_codes = append(b"".join(sorted(images)))
    half_pixels = append(font.bitmap(0))
    full_pixels = append(b"".join(images[code] for code in sorted(images)))
    for index, face in enumerate(font.faces):
        records = metric_records(data, face.metrics_offset)
        if index % 2:
            records.append(LAYOUT_SPACE + b"\x00\x14")
        metrics = append(b"".join(records) + bytes(4))
        fields = (metrics, len(images), full_codes, full_pixels) if index % 2 else (metrics, face.count, half_codes, half_pixels)
        struct.pack_into("<4I", output, face.descriptor + 4, *fields)
    Gothic(bytes(output))
    return bytes(output)


def script_layout(data):
    commands, branches = parse(data)
    output, changed = bytearray(data), []
    for command in commands:
        if command.text_start is None:
            continue
        raw = data[command.text_start:command.text_end]
        fixed = preserve_runs(raw)
        if fixed != raw:
            require(len(tuple(tokens(fixed))) == len(tuple(tokens(raw))), "ADV spacing changed printable timing units")
            output[command.text_start:command.text_end] = fixed
            changed.append(command.offset)
    require(parse(output) == (commands, branches), "Spacing compensation changed ADV command/branch boundaries")
    return bytes(output), changed


def ui_layout(original, output, addresses):
    elf, changed = Elf(original), []
    for address in sorted(set(addresses)):
        at = elf.offset(address)
        end = text_end(output, at)
        raw = bytes(output[at:end])
        fixed = preserve_runs(raw)
        if fixed != raw:
            output[at:end] = fixed
            changed.append(f"{address:08X}")
    at = elf.offset(CHOICE_ADDRESS)
    require(original[at:at + len(CHOICE_RAW) + 1] == CHOICE_RAW + b"\0"
            and output[at:at + len(CHOICE_RAW)] == CHOICE_RAW,
            "Original ADV YES/NO padding template differs")
    # The original prefix also had one 16px ASCII space. Existing blank A0
    # retains that width; regular ASCII word spaces intentionally stay 10px.
    fixed = CHOICE_RAW.replace(FULL_SPACE, LAYOUT_SPACE).replace(b" ", b"\xa0", 1)
    require(len(fixed) == len(CHOICE_RAW), "YES/NO compensation changed its owned allocation")
    output[at:at + len(fixed)] = fixed
    return {"texts": changed, "yes_no_template": f"{CHOICE_ADDRESS:08X}",
            "layout_space_px": 20, "dialogue_indent_px": 11, "allocation_lengths_preserved": True}
