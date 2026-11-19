"""One word gap after a target-word run; keep all existing tile dimensions."""

import mini_conditions as conditions
from mini_korean_runtime import Assembly, immediate as ins, jump, register as rr
from unpack_disc import require


GAP_PIXELS = 6
WORD_SPRITE = 471
FORMAT_ADDRESS = conditions.BASES[0] + 9 * 80
SITES = {
    "total_width": (0x12c9f0, (ins(35, 2, 18, 12), 2 << 16 | 2 << 11 | 5 << 6), 0x12c9f8),
    "prefix_width": (0x12cc28, (ins(4, 0, 0, -18), rr(33, 16, 3, 16)), 0x12cbe4),
    "draw_advance": (0x12d350, (ins(4, 0, 0, 176), ins(35, 4, 29, 24)), 0x12d614),
}


def _boundary(a, goal, tokens):
    # Only the nonempty -3 run immediately followed by the Korean word label.
    a.emit(ins(35, 3, goal, 12))
    a.branch(6, 3, 0, "resume")
    a.emit(0, ins(35, 3, tokens, 4), 3 << 16 | 3 << 11 | 8 << 6 | 2,
           ins(9, 3, 3, -WORD_SPRITE))
    a.branch(5, 3, 0, "resume")
    a.emit(0)


def _scaled_gap(a, context, cursor):
    # The original coordinates use 16.16 units and a 256-based UI scale.
    a.emit(ins(35, 3, context, 0x4c), 3 << 16 | 2 << 11 | 1 << 6,
           rr(33, 3, 3, 2), 3 << 16 | 3 << 11 | 9 << 6,
           rr(33, cursor, cursor, 3))


def install(image):
    """Attach to runtime_958.Image before finish(); never write files or resources."""
    at = image.elf.offset(FORMAT_ADDRESS)
    actual = image.original[at:at + 80]
    report = {"enabled": False, "gap_pixels": GAP_PIXELS,
              "format_address": FORMAT_ADDRESS, "word_sprite": WORD_SPRITE, "hooks": {}}
    if actual == conditions.packed(conditions.ORIGINAL[9]):
        return report
    require(actual == conditions.packed(conditions.KOREAN[9]),
            "Unknown MINI target-word condition format")
    for address, expected, _ in SITES.values():
        require(image.words(address, 2) == expected,
                f"Original MINI condition spacing site differs: {address:#x}")
        require(not any(address + i * 4 in image.changes for i in range(2)),
                "MINI condition spacing hook overlaps another patch")

    for name, (address, expected, continuation) in SITES.items():
        a = Assembly()
        if name == "total_width":
            a.emit(*expected)
            _boundary(a, 18, 17)
            a.emit(ins(9, 2, 2, GAP_PIXELS))
        elif name == "prefix_width":
            a.emit(expected[1])
            _boundary(a, 19, 17)
            _scaled_gap(a, 18, 16)
        else:
            a.emit(expected[1])
            _boundary(a, 23, 4)
            _scaled_gap(a, 20, 21)
        a.label("resume")
        a.emit(jump(continuation), 0)
        report["hooks"][name] = image.hook(address, a.finish())
    report["enabled"] = True
    return report
