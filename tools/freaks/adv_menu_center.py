"""Center ADV menu rasters using valid PSMT8 texture-upload coordinates."""

import struct

from adv_format import require, text_end, tokens
from database_text import width
from elf_probe import Elf


BLANKS = (b" ", b"\x81\x40", b"\xa0", b"\xf0\x41")


def _span(raw, measure):
    parts = list(tokens(raw))
    require(all(not control for control, _ in parts), "ADV menu label contains display controls")
    visible = [i for i, (_, code) in enumerate(parts) if code not in BLANKS]
    require(visible, "ADV menu label is blank")
    first, last = visible[0], visible[-1] + 1
    leading = measure(b"".join(code for _, code in parts[:first]))
    pixels = measure(b"".join(code for _, code in parts[first:last]))
    require(isinstance(leading, int) and isinstance(pixels, int) and leading >= 0 and pixels > 0,
            "ADV menu width callback returned invalid pixels")
    return leading, pixels


def recenter(original, output, items, positions, *, measure):
    """Patch an allocated mutable ELF; return per-command layout evidence.

    Call after string allocation, pointer relocation and spacing compensation.
    The original fixed-face labels establish the common visible center. Current
    padding stays in the strings, but is excluded from the centered span.
    cmd74 x/y are texture-upload coordinates, not screen positions. Native
    PSMT8 uploads require even x; nearest two-pixel ties round right. The
    original 100x80 display primitive and all four upload y values stay fixed.
    """
    from ui_labels import COMMANDS

    require(len(output) == len(original), "ADV menu ELF size changed")
    elf = Elf(original)
    commands = [(command, item) for item in items if item.kind == "main_menu" for command in item.commands]
    require(sorted((command, item.address) for command, item in commands) == sorted(COMMANDS),
            "ADV menu command catalogue differs")
    planned = []
    for command, item in sorted(commands):
        at = elf.offset(command)
        require(original[at] == output[at] == 0x74
                and struct.unpack_from("<I", original, at + 5)[0] == item.address,
                "Original ADV menu text command differs")
        require(item.address in positions, "ADV menu string position is missing")
        destination = positions[item.address]
        require(struct.unpack_from("<I", output, at + 5)[0] == destination,
                "ADV menu relocated string pointer differs")
        old_x, y = struct.unpack_from("<HH", original, at + 1)
        before_x, current_y = struct.unpack_from("<HH", output, at + 1)
        require(current_y == y, "ADV menu text y changed")
        source = elf.offset(item.address)
        require(original[source:text_end(original, source)] == item.raw, "Original ADV menu label differs")
        source_leading, source_width = _span(item.raw, width)
        center_x2 = 2 * (old_x + source_leading) + source_width
        start = elf.offset(destination)
        raw = bytes(output[start:text_end(output, start)])
        leading, pixels = _span(raw, measure)
        # 0x105230 rejects odd destination x for PSM 0x13 before packet output.
        new_x = 2 * ((center_x2 - pixels - 2 * leading + 2) // 4)
        require(0 <= new_x <= 0xffff, "ADV menu text x exceeds its unsigned operand")
        require(784 <= new_x + leading and new_x + leading + pixels <= 884,
                "ADV menu visible span exceeds its 100px texture primitive")
        planned.append((at, {"command": f"{command:08X}", "address": f"{item.address:08X}",
                             "position": f"{destination:08X}", "original_x": old_x,
                             "old_x": before_x, "new_x": new_x, "y": y,
                             "original_leading_width": source_leading, "original_width": source_width,
                             "leading_width": leading, "width": pixels, "full_width": measure(raw),
                             "target_center_x2": center_x2,
                             "old_center_x2": 2 * (before_x + leading) + pixels,
                             "new_center_x2": 2 * (new_x + leading) + pixels}))
    require(len({row["target_center_x2"] for _, row in planned}) == 1,
            "Original ADV menu labels do not share a center")
    # Validate all four commands before mutating any output bytes.
    for at, row in planned:
        struct.pack_into("<H", output, at + 1, row["new_x"])
    return {"center_x2": planned[0][1]["target_center_x2"],
            "rounding": "nearest 2px PSMT8 upload coordinate; ties right",
            "upload_alignment": 2, "commands": [row for _, row in planned]}
