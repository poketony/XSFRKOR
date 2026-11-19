"""Execute the saved wide build's native routines before publishing its test pair."""

import struct
from types import SimpleNamespace

from mini_board_native import BoardMachine, simple_queries
from mini_candidate_native import CandidateMachine
from mini_heap_native import HeapMachine
from unpack_disc import require
from verify_codecs import Native
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import (UC_MIPS_REG_0, UC_MIPS_REG_HI, UC_MIPS_REG_LO,
                               UC_MIPS_REG_PC, UC_MIPS_REG_RA, UC_MIPS_REG_V0)

import runtime_958 as runtime
from boot_safety_958 import LibcMachine, install_quad_spills, require_safe


class DrawMachine(Native):
    STACK, STOP = LibcMachine.STACK, LibcMachine.STOP

    def __init__(self, elf):
        super().__init__(elf)
        install_quad_spills(self)
        self.draws = []
        for at in (0x10c630, 0x10c680, 0x18a598):
            self.uc.hook_add(UC_HOOK_CODE, self.capture, begin=at, end=at)

    def capture(self, uc, at, size, user):
        if at == 0x10c630:
            uc.mem_write(self.OUTPUT - 16, b"\xa5" * 112)
            uc.mem_write(self.OUTPUT, b"\0" * 80)
            uc.reg_write(UC_MIPS_REG_V0, self.OUTPUT)
        elif at == 0x18a598:
            pointer = uc.reg_read(UC_MIPS_REG_0 + 5)
            require(pointer == self.OUTPUT, "Unexpected wide draw primitive")
            self.draws.append(bytes(uc.mem_read(pointer, 80)))
            require(bytes(uc.mem_read(pointer - 16, 16)) == b"\xa5" * 16
                    and bytes(uc.mem_read(pointer + 80, 16)) == b"\xa5" * 16,
                    "Wide draw crossed its primitive")
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    call = HeapMachine.call


def verify_drawing(elf, report):
    machine = DrawMachine(elf)
    base = report["patch"]["font_glyphs"]
    for token in range(958):
        expected = bytes(machine.uc.mem_read(base + token * 16, 16))
        for first, size in ((runtime.SMALL, 32), (runtime.LARGE, 48)):
            machine.call(0x10b1e8, (first + token) << 8, 100, 120, 47)
            raw = machine.draws[-1]
            require(struct.unpack_from("<I", raw, 12)[0] == 17
                    and struct.unpack_from("<2i2H", raw, 0x1c) == (100 - size // 2, 120 - size // 2, size, size)
                    and raw[0x28:0x38] == expected and raw[0x38:0x3c] == bytes(4),
                    f"Wide font draw/filter differs for ID {token}")
    return {"font_primitive_checks": len(machine.draws), "all_958_ids_both_sizes": True,
            "scope": "Actual saved runtime conversion before original ordering-table insertion; no GPU rasterization."}


def verify_piece_drawing(elf, report):
    machine = DrawMachine(elf)
    machine.uc.mem_write(report["patch"]["state"], struct.pack("<I", 1))
    for token in range(958):
        piece = bytearray(0x6c)
        struct.pack_into("<H", piece, 0, token)
        piece[4] = 1
        struct.pack_into("<2I", piece, 8, 100, 120)
        struct.pack_into("<H", piece, 0x14, 47)
        machine.uc.mem_write(machine.SOURCE, bytes(piece))
        first = len(machine.draws)
        machine.call(0x11f178, machine.SOURCE)
        draws = machine.draws[first:]
        fonts = [r for r in draws if struct.unpack_from("<I", r, 12)[0] == 17]
        backgrounds = [r for r in draws if struct.unpack_from("<I", r, 12)[0] == 12]
        require(len(fonts) == 1 and len(backgrounds) == 1,
                f"Live piece {token} did not draw exactly one Hangul glyph and one original tile")
        at = report["patch"]["font_glyphs"] + token * 16
        require(fonts[0][0x28:0x38] == bytes(machine.uc.mem_read(at, 16)),
                "Actual live-piece renderer selected another font glyph")
    return {"all_958_live_piece_glyphs_and_original_tile_backgrounds": True,
            "scope": "Actual 11F178 live-piece draw path through handle selection and primitive conversion; no GPU."}


def verify_lookup_alphabet(elf, report):
    machine = DrawMachine(elf)
    keys = sorted({(i, (i + 1) % 958) for i in range(958)} |
                  {tuple([i] + [(i + j * 101) % 958 for j in range(1, 9)]) for i in range(958)},
                  key=lambda key: (len(key), key))
    index = 0x1100000
    data = struct.pack("<I", len(keys)) + b"".join(
        struct.pack("<IH9H", i + 1, len(key), *key, *([0] * (9 - len(key)))) for i, key in enumerate(keys))
    machine.uc.mem_write(index, data)
    for i, key in enumerate(keys):
        machine.uc.mem_write(machine.SOURCE, struct.pack(f"<{len(key)}H", *key))
        require(machine.call(report["patch"]["lookup"], machine.SOURCE, len(key), index) == index + 4 + i * 24,
                "Saved wide lookup truncated or confused a full-range key")
    for key in ((0, 0), (957, 957), (255, 255), (256, 256)):
        require(key not in keys, "Negative wide lookup key exists")
        machine.uc.mem_write(machine.SOURCE, struct.pack("<2H", *key))
        require(machine.call(report["patch"]["lookup"], machine.SOURCE, 2, index) == 0,
                "Wide lookup accepted an absent key")
    require(bytes(machine.uc.mem_read(index, len(data))) == data, "Wide lookup changed its index")
    return {"synthetic_full_range_keys": len(keys), "absent_keys_rejected": 4}


def verify_return_animation_copy(elf):
    machine = DrawMachine(elf)
    reached = []

    def stop(uc, at, size, user):
        reached.append(at)
        uc.emu_stop()

    machine.uc.hook_add(UC_HOOK_CODE, stop, begin=0x135c74, end=0x135c74)
    for token in range(958):
        machine.uc.mem_write(machine.SOURCE, struct.pack("<H", token) + b"\0" * 106)
        machine.uc.mem_write(machine.OUTPUT, b"\xa5" * 256)
        for register, value in ((18, machine.SOURCE), (11, machine.OUTPUT), (9, 1),
                                (23, 47), (10, 0), (3, 0), (29, machine.STACK)):
            machine.uc.reg_write(UC_MIPS_REG_0 + register, value)
        machine.uc.emu_start(0x135c48, machine.STOP, count=100)
        require(len(reached) == token + 1
                and bytes(machine.uc.mem_read(machine.OUTPUT + 0x34, 2)) == struct.pack("<H", token)
                and bytes(machine.uc.mem_read(machine.OUTPUT + 0x3b, 1)) == b"\0",
                "Return animation truncated a wide letter or retained its special flag")
    return {"full_id_copy_checks": 958, "scope": "Native return-animation record construction fragment"}


class WideMachine(BoardMachine):
    STACK, STOP = LibcMachine.STACK, LibcMachine.STOP
    libc_external = LibcMachine.libc_external
    libc_multiply = LibcMachine.libc_multiply
    break_request = LibcMachine.break_request
    aligned_copy = CandidateMachine.copy

    def __init__(self, elf, rom):
        super().__init__(elf, rom)
        self.uc.mem_unmap(0x2000000, 0x100000)
        LibcMachine.install_libc_checks(self)
        # The original resource reader can choose the EE quadword memcpy path
        # depending on heap alignment. Generic Unicorn cannot execute it.
        self.copy_calls = 0
        self.uc.hook_add(UC_HOOK_CODE, self.aligned_copy, begin=0x1a8644, end=0x1a8644)
        self.uc.hook_add(UC_HOOK_CODE, self.random_multiply, begin=0x114704, end=0x114704)
        geometry = [*range(0x120840, 0x1208c8, 4), 0x120c18, 0x120c28, 0x120cb0, 0x120cb8,
                    *range(0x120d80, 0x120f48, 4),
                    0x121828, 0x121854, 0x121888, 0x1218b0]
        for at in geometry:
            word = struct.unpack_from("<I", elf, self.elf.offset(at))[0]
            if word >> 26 == 0 and word & 63 in (24, 25) and word >> 11 & 31 and at != 0x114704:
                self.uc.hook_add(UC_HOOK_CODE, self.destination_multiply, begin=at, end=at)
        # The fixed-point rotation helper uses both EE multiply units. Only
        # its GPR results are read; Unicorn does not implement MULT1.
        for at, expected in ((0x113304, 0x02501818), (0x113308, 0x72308018),
                             (0x11330c, 0x72228818), (0x113310, 0x02429018)):
            word, = struct.unpack_from("<I", elf, self.elf.offset(at))
            require(word == expected, "Original MINI fixed-point rotation differs")
            self.uc.hook_add(UC_HOOK_CODE, self.destination_multiply, begin=at, end=at)

    def arena_request(self, uc, at, size, user):
        require(uc.reg_read(UC_MIPS_REG_0 + 4) == 64, "Outer heap alignment differs")
        self.arena_size = uc.reg_read(UC_MIPS_REG_0 + 5) & 0xffffffff

    def initialize(self):
        # Execute the original outer allocator too, instead of placing its result
        # at the budget constant. That substitution hid the startup heap collision.
        self.uc.reg_write(UC_MIPS_REG_0 + 28, self.elf.gp)
        self.uc.reg_write(UC_MIPS_REG_0 + 29, self.STACK)
        self.uc.emu_start(0x1078d4, 0x10790c, count=2_000_000)
        require(self.uc.reg_read(UC_MIPS_REG_PC) == 0x10790c, "Outer heap init did not finish")
        self.arena_start = self.uc.reg_read(UC_MIPS_REG_V0) & 0xffffffff
        require(self.arena_start and self.arena_start + self.arena_size <= 0x1ef0000,
                "Actual outer heap allocation failed or exceeded its kernel limit")
        self.uc.mem_write(self.arena_start + self.arena_size, b"\xa5" * 16)
        slots = struct.unpack_from("<I", self.elf.data, self.elf.offset(0x207704))[0]
        self.call(0x185680, self.arena_start, self.arena_start + self.arena_size - 1, slots, 0,
                  count=300_000_000)
        self.root = self.u32(self.elf.gp - 0x7f70)
        require(self.root == (self.arena_start + 255) & ~255, "Arena root differs")
        slots, stride = struct.unpack_from("<2I", self.elf.data, self.elf.offset(0x207708))
        require(self.call(0x186810, 0x1bf4b0 + 0x2bc, 0x2138f0, slots, stride) == 1,
                "Native MINI main pool allocation failed")

    def destination_multiply(self, uc, at, size, user):
        word = struct.unpack_from("<I", self.elf.data, self.elf.offset(at))[0]
        x, y = (uc.reg_read(UC_MIPS_REG_0 + r) & 0xffffffff for r in (word >> 21 & 31, word >> 16 & 31))
        if word & 63 == 24:
            x = x if x < 0x80000000 else x - 0x100000000
            y = y if y < 0x80000000 else y - 0x100000000
        result = x * y & 0xffffffffffffffff
        low, high = result & 0xffffffff, result >> 32
        extend = lambda v: v if v < 0x80000000 else v | 0xffffffff00000000
        uc.reg_write(UC_MIPS_REG_0 + (word >> 11 & 31), extend(low))
        if word >> 26 == 0:
            uc.reg_write(UC_MIPS_REG_LO, extend(low))
            uc.reg_write(UC_MIPS_REG_HI, extend(high))
        uc.reg_write(UC_MIPS_REG_PC, at + 4)

    def random_multiply(self, uc, at, size, user):
        x, y = (uc.reg_read(UC_MIPS_REG_0 + r) & 0xffffffff for r in (2, 16))
        uc.reg_write(UC_MIPS_REG_V0, x * y)
        uc.reg_write(UC_MIPS_REG_PC, at + 4)


def verify_supply(machine):
    descriptor = machine.call(0x185b98, 0x215790, 0x3000)
    require(descriptor, "Wide supply test allocation failed")
    base = machine.u32(descriptor)
    supply, controller, letters = base, base + 0x800, base + 0x1000
    original = struct.pack("<959H", *range(958), 0xffff)
    for token in range(958):
        machine.uc.mem_write(supply, b"\0" * 0x738)
        machine.uc.mem_write(controller, b"\0" * 0x80)
        machine.uc.mem_write(controller + 0x34, struct.pack("<I", supply))
        machine.uc.mem_write(supply + 2, struct.pack("<HH", 958, 958))
        machine.uc.mem_write(supply + 12, struct.pack("<II", letters, token))
        machine.uc.mem_write(supply + 0x2d0, struct.pack("<I", supply + 0x18))
        machine.uc.mem_write(letters, original)
        require(machine.call(0x128050, controller, 0) == token, f"Supply selection truncated ID {token}")
        machine.call(0x1281e8, controller)
        require(machine.read(letters, 958 * 2) == original[:token * 2] + original[(token + 1) * 2:],
                f"Wide supply removal differs at ID {token}")
        require(machine.call(0x128388, controller) == 0 and machine.read(letters, len(original)) == original
                and struct.unpack("<H", machine.read(supply + 4, 2))[0] == 958,
                f"Wide supply undo differs at ID {token}")
    source = bytes((0, 23, 69, 71))
    machine.uc.mem_write(letters, source)
    machine.uc.reg_write(UC_MIPS_REG_0 + 8, len(source))
    machine.uc.reg_write(UC_MIPS_REG_0 + 9, letters)
    machine.call(0x11d368, supply, 1, 0, 8)
    require(machine.read(machine.u32(supply + 8), 10) == struct.pack("<5H", *source, 0xffff),
            "Generated Japanese supply changed its source letters")
    machine.call(0x11d438, supply)
    machine.call(0x186030, descriptor)
    return {"selection_remove_undo_cases": 958, "full_sequence_and_sentinel_restored": True,
            "generated_japanese_supply_normalization": True}


def verify_route(machine, profile, record, report, stage, variant=0, *, letters=None, require_clear=True):
    require(profile["index"] == stage.index and 0 <= variant < len(stage.goals),
            "Route stage or variant differs")
    descriptor = machine.call(0x185b98, 0x215780, 0x90)
    require(descriptor, "1P load request allocation failed")
    request = machine.u32(descriptor)
    machine.uc.mem_write(request, bytes(0x90))
    machine.uc.mem_write(request + 12, struct.pack("<2I", stage.index, variant))

    def setup_ui(uc, at, size, user):
        uc.reg_write(UC_MIPS_REG_V0, 0)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    handle = machine.uc.hook_add(UC_HOOK_CODE, setup_ui, begin=0x143278, end=0x143278)
    try:
        result = machine.call(0x143460, request, count=10_000_000)
    finally:
        machine.uc.hook_del(handle)
    root = machine.u32(0x272b88)
    machine.call(0x186030, descriptor)
    require(result == 0 and root, "Original 1P caller failed to load its selected stage")
    require(root and machine.u32(report["patch"]["state"]) == record["profile"], "Korean stage was not selected")
    goal = stage.goals[variant]
    target = root + 0x24a7c
    require((machine.u32(target), machine.u32(target + 4), machine.u32(target + 12)) == tuple(goal[:3])
            and machine.read(target + 16, 12) == goal[4:], "Loaded stage goal differs")
    require(machine.u32(root + 0x24b60) == struct.unpack_from("<H", stage.data, 0x5c + variant * 2)[0],
            "Loaded stage time differs")
    hud, event = machine.STACK - 0x2400, machine.STACK - 0x2200
    wide_goal = goal[0] in (9, 10)
    count_goal = wide_goal or goal[0] in (2, 3, 4)
    placed_goal = goal[0] in (0, 1, 8)
    goal_word = "".join(profile["goal_targets"][variant].split()) if wide_goal else None
    if count_goal:
        machine.uc.mem_write(hud, bytes(0x100))
        machine.uc.mem_write(hud + 0x34, struct.pack("<I", target))
        machine.uc.mem_write(target + 8, struct.pack("<I", goal[1]))
    remaining_goals = goal[1]
    if placed_goal:
        history = hud + 0x100
        machine.uc.mem_write(hud, bytes(0x100))
        machine.uc.mem_write(hud + 0x34, struct.pack("<I", target))
        machine.uc.mem_write(hud + 0x58, struct.pack("<2I", history, history))
        machine.call(0x12c0b8, hud, root)
        preplaced = {(x, y) for x, y, _ in record["preplaced"]}
        if goal[0] != 8:
            remaining_goals = sum((row[1], row[2]) not in preplaced and (goal[0] == 0 or row[0] == 2)
                                  for row in stage.board.cells)
        require(machine.u32(target + 8) == remaining_goals, "Original placement goal initialized differently")
    words_by_id = {value: word for word, value in report["korean_word_ids"].items()}
    alphabet = report["alphabet"]
    for x, y, expected in record["preplaced"]:
        cell = machine.call(0x121380, root, x, y)
        piece = machine.u32(cell + 0x14)
        require(struct.unpack("<H", machine.read(piece, 2))[0] == expected, "Wide preplaced letter differs")
    supply = root + 0x23c0c
    actual = machine.read(machine.u32(supply + 8), record["inventory_count"] * 2)
    require(actual == struct.pack(f"<{record['inventory_count']}H",
                                 *(alphabet.index(c) for c in profile["inventory"])), "Loaded wide supply differs")
    board_controller = machine.call(0x122638, root, 0, count=3_000_000)
    require(board_controller, "Native board controller did not initialize")
    menu = machine.call(0x126a78, supply, count=3_000_000)
    require(menu, "Native supply menu did not initialize")
    machine.uc.reg_write(UC_MIPS_REG_0 + 8, 0)
    cursor = machine.call(0x12ae38, 1, 0, 100, 100, count=3_000_000)
    require(cursor, "Native picked-letter cursor did not initialize")
    steps = []
    require(0 < len(profile["route"]) <= len(profile["inventory"]), "Route exceeds available supply")
    remaining_stock = list(profile["inventory"])
    chosen = profile["inventory"][:len(profile["route"])] if letters is None else letters
    require(len(chosen) == len(profile["route"]), "Branch letters and placements differ")
    for char, move_to in zip(chosen, profile["route"]):
        token = alphabet.index(char)
        require(char in remaining_stock, "Branch needs a letter absent from the live supply")
        selected = 0 if letters is None else remaining_stock.index(char)
        require(remaining_stock[selected] == char, "Route prefix and supply differ")
        # Set the existing menu's selected row, not a replacement letter bank.
        machine.uc.mem_write(supply + 0x10, struct.pack("<I", selected))
        row_argument = selected if machine.read(supply, 1) == b"\x01" else 0
        require(machine.call(0x128050, menu, row_argument) == token, "Live menu selected another letter")
        machine.uc.reg_write(UC_MIPS_REG_0 + 8, move_to["y"])
        machine.call(0x12b020, cursor, 3, token, move_to["x"])
        temporary = machine.u32(cursor + 0xa0)
        require(temporary and struct.unpack("<H", machine.read(temporary, 2))[0] == token,
                "Picked-letter cursor truncated its letter")
        query = machine.call(0x1212b8, root, temporary, move_to["x"], move_to["y"], count=3_000_000)
        require(query, f"Korean move rejected: {char} at {move_to}")
        count = machine.u32(query + 0xe000)
        require(0 < count <= 1024, "Wide query count exceeds original buffer")
        found = []
        for i in range(count):
            pointer = machine.u32(query + i * 56 + 0x28)
            if pointer:
                wid = machine.u32(pointer) & 0xfffff
                word = words_by_id[wid]
                found.append(word)
                if count_goal:
                    if wide_goal:
                        machine.uc.mem_write(event - 4, struct.pack("<I", query + i * 56))
                        matches = word == goal_word if goal[0] == 9 else goal_word in word
                    else:
                        machine.uc.mem_write(event + 4, struct.pack("<I", machine.u32(query + i * 56 + 0x24)))
                        matches = goal[0] == 2 or len(word) == goal[2]
                        matches |= goal[0] == 4 and len(word) > goal[2]
                    check_goal_event(machine, hud, event, wide_goal)
                    if matches:
                        remaining_goals = max(remaining_goals - 1, 0)
                    require(machine.u32(target + 8) == remaining_goals,
                            "Native goal counted a different wide word")
        require(found, "Accepted wide placement has no dictionary word")
        machine.call(0x124468, query)
        machine.uc.reg_write(UC_MIPS_REG_0 + 8, 0)
        installed = machine.call(0x121410, root, temporary, move_to["x"], move_to["y"], count=3_000_000)
        require(installed, "Native wide piece installation failed")
        cell = machine.call(0x121380, root, move_to["x"], move_to["y"])
        piece = machine.u32(cell + 0x14)
        require(struct.unpack("<H", machine.read(piece, 2))[0] == token, "Installed wide letter was truncated")
        if placed_goal:
            machine.call(0x12c010, hud, cell)
            if goal[0] != 1 or machine.read(cell, 1) == b"\x02":
                remaining_goals -= 1
            require(remaining_goals >= 0 and machine.u32(target + 8) == remaining_goals,
                    "Original placement counter did not count the installed cell")
        machine.call(0x1281e8, menu)
        remaining_stock.pop(selected)
        steps.append({"tile": char, "id": token, **move_to, "words": found,
                      "supply_index": selected, "remaining_goal": remaining_goals})
        call_without_sound(machine, 0x1227e8, board_controller, installed)
        for _ in range(120):
            if machine.u32(root + 0x232b4) == 1:
                break
            machine.call(0x122c10, board_controller, count=3_000_000)
        require(machine.u32(root + 0x232b4) == 1, "Original board motion did not finish")
    remaining = record["inventory_count"] - len(steps)
    expected = [alphabet.index(c) for c in remaining_stock]
    require(struct.unpack("<H", machine.read(supply + 4, 2))[0] == remaining
            and machine.read(machine.u32(supply + 12), (remaining + 1) * 2)
            == struct.pack(f"<{remaining + 1}H", *expected, 0xffff),
            "Route left a different live supply")
    if require_clear and goal[0] == 1:
        stars = {(row[1], row[2]) for row in stage.board.cells if row[0] == 2}
        covered = {(x, y) for x, y, _ in record["preplaced"]} | {(p["x"], p["y"]) for p in steps}
        require(stars and stars <= covered, "Verified route leaves an original star cell empty")
    if require_clear and (count_goal or placed_goal):
        require(remaining_goals == 0 and machine.call(0x12c6e0, hud) == 1,
                "Verified route did not clear the original target counter")
    machine.call(0x12af58, cursor)
    machine.call(0x126bf0, menu)
    machine.call(0x122730, board_controller)
    machine.call(0x11cde0, root)
    machine.uc.mem_write(0x272b88, bytes(4))
    return steps


def check_goal_event(machine, hud, event, wide_goal):
    # The live query row/length is real; model only the event slot and sound call.
    old_hud = machine.read(0x272bb4, 4)
    machine.uc.mem_write(0x272bb4, struct.pack("<I", hud))
    try:
        if wide_goal:
            call_without_sound(machine, 0x146978, event)
        else:
            call_without_sound(machine, 0x12c138, hud, event)
    finally:
        machine.uc.mem_write(0x272bb4, old_hud)


def call_without_sound(machine, entry, *args):
    def sound(uc, *_):
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    hook = machine.uc.hook_add(UC_HOOK_CODE, sound, begin=0x1126a8, end=0x1126a8)
    try:
        return machine.call(entry, *args)
    finally:
        machine.uc.hook_del(hook)


def verify_candidates(elf, rom, dictionary, report):
    machine = CandidateMachine(elf, rom, dictionary)
    machine.prepare()
    total = 0
    seen = set()
    for record in report["patch"]["profiles"]:
        machine.index = record["index"]
        if machine.index in seen:
            continue
        seen.add(machine.index)
        for i in range(record["words"]):
            offset = 4 + i * 24
            wid = machine.u32(machine.index + offset) & 0xfffff
            length = struct.unpack("<H", machine.read(machine.index + offset + 4, 2))[0]
            scratch = machine.STACK - 0x1000
            machine.uc.mem_write(scratch, machine.read(machine.index + offset + 6, length * 2))
            require(machine.call(report["patch"]["lookup"], scratch, length, machine.index) == machine.index + offset,
                    "Korean stage lookup returned another record")
            word_id, pointer = machine.choose(SimpleNamespace(offset=offset, word_ids=range(wid, wid + 1)))
            require(machine.read_word(word_id, pointer) == dictionary.words[wid - 1].raw,
                    "Wide dictionary candidate/definition differs")
            total += 1
    return {"native_candidate_and_definition_reads": total, "unique_word_indexes": len(seen)}


def verify_japanese(machine, report):
    root = machine.call(0x11cd28, 0, 36, 0, count=3_000_000)
    require(root and machine.u32(report["patch"]["state"]) == 0, "Japanese stage kept Korean lookup state")
    stage = machine.stages.stages[36]
    require(machine.read(machine.u32(root + 0x23c0c + 8), len(stage.inventory) * 2)
            == struct.pack(f"<{len(stage.inventory)}H", *stage.inventory), "Original Japanese supply was not normalized")
    flag_checks = 0
    for flag in (1, 0):
        machine.call(0x11d7f0, root, 36, flag, count=3_000_000)
        for group in stage.board.pieces:
            for row in group:
                cell = machine.call(0x121380, root, row[1], row[2])
                piece = machine.u32(cell + 0x14)
                require(struct.unpack("<H", machine.read(piece, 2))[0] == row[0]
                        and machine.read(piece + 7, 1) == bytes((flag,)),
                        "Preplaced reload lost its full letter or special flag")
                flag_checks += 1
    occupied = {(r[1], r[2]) for g in stage.board.pieces for r in g}
    checked = 0
    for row in stage.board.cells:
        x, y = row[1:3]
        if (x, y) in occupied:
            continue
        for token in (0, 69, 71):
            expected = simple_queries(stage.board, token, x, y)
            require(sorted(machine.queries(root, token, x, y)) == sorted(expected), "Japanese query changed")
            machine.queries(root, token, x, y, judge=True)
            checked += 1
    machine.call(0x11cde0, root)
    return {"original_japanese_placement_checks": checked, "preplaced_reload_special_flags": flag_checks}


def verify_versus(machine, profile, record, report, stage):
    uc = machine.uc
    request = machine.STACK - 0x1000
    uc.mem_write(request, struct.pack("<III", 0, 0, stage.index))

    def setup_ui(uc, *_):
        uc.reg_write(UC_MIPS_REG_V0, 0)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    hooks = [uc.hook_add(UC_HOOK_CODE, setup_ui, begin=at, end=at) for at in (0x147088, 0x10a670)]
    try:
        require(machine.call(0x1472c0, request, count=10_000_000) == 0,
                f"Original VS caller failed: {stage.index}")
    finally:
        for hook in hooks:
            uc.hook_del(hook)
    root = machine.u32(0x272d94)
    state = report["patch"]["state"]
    require(root and struct.unpack("<2I", machine.read(state, 8)) == (record["profile"], root),
            "Wrong live Korean VS board")
    alphabet = report["alphabet"]
    expected = [alphabet.index(c) for c in profile["inventory"]]
    supplies = (root + 0x23c0c, root + 0x24344)
    stocks = [machine.u32(s + 8) for s in supplies]
    menus = [machine.call(0x126a78, supply, count=3_000_000) for supply in supplies]
    stock = struct.pack("<73H", *expected, 0xffff)
    require(all(menus) and stocks[0] != stocks[1]
            and all(machine.read(p, len(stock)) == stock for p in stocks), "VS supplies differ or share storage")
    positions = []
    for i, row in enumerate(stage.board.cells):
        cell = root + 0x1c + i * 0x6c
        require(machine.read(cell, 6) == row[:6], "Native VS board geometry changed")
        if not machine.u32(cell + 0x14):
            positions.append((row[1], row[2]))
    for x, y, token in record["preplaced"]:
        cell = machine.call(0x121380, root, x, y)
        actual, = struct.unpack("<H", machine.read(machine.u32(cell + 0x14), 2))
        require((actual in expected if token == 0xffff else token == actual), "Native VS preplaced tile differs")
    first = None
    for index, token in enumerate(expected):
        if token in expected[:index]:
            continue
        for x, y in positions:
            query = machine.call(0x123f98, root, token, x, y, count=3_000_000)
            if not query:
                continue
            count = machine.u32(query + 0xe000)
            word = next((machine.u32(query + i * 56 + 0x28) for i in range(count)
                         if machine.u32(query + i * 56 + 0x28)), 0)
            machine.call(0x124468, query)
            if word:
                first = (index, token, x, y, machine.u32(word) & 0xfffff)
                break
        if first:
            break
    require(first is not None, f"Native VS board has no opening move: {stage.index}")
    index, token, x, y, wid = first
    require(machine.call(0x128050, menus[0], index) == token, "Korean VS menu selected another letter")
    machine.call(0x1281e8, menus[0])
    require(machine.read(machine.u32(supplies[1] + 12), len(stock)) == stock,
            "First VS player's choice changed the second player's supply")
    for menu in reversed(menus):
        machine.call(0x126bf0, menu)
    machine.call(0x11cde0, root)
    uc.mem_write(0x272d94, bytes(4))
    require(machine.read(state, 8) == bytes(8), "VS board leaked its Korean profile")
    return {"stage": stage.index, "first_word_id": wid, "tile": alphabet[token], "position": [x, y]}


def verify_demo_boards(machine, report, dictionary=None):
    if report["patch"].get("korean_demos"):
        from demo_native_958 import verify as verify_korean_demos
        require(dictionary is not None, "Korean demo verification needs the actual production dictionary")
        return verify_korean_demos(machine, report, dictionary)
    uc, patch = machine.uc, report["patch"]
    checked = []
    for source, backup in patch.get("demo_remaps", {}).items():
        versus = source in (103, 105)
        entry = 0x16b834 if versus else 0x16b6d4
        native = 0x1472c0 if versus else 0x143460
        hook_word, = struct.unpack("<I", machine.read(entry, 4))
        thunk = (hook_word & 0x03ffffff) << 2
        request = machine.STACK - 0x1000
        uc.mem_write(request, bytes(0x90))
        uc.mem_write(request + (8 if versus else 12), struct.pack("<I", source))
        seen = []

        def setup_ui(uc, *_):
            uc.reg_write(UC_MIPS_REG_V0, 0)
            uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

        def observe(uc, *_):
            seen.append((uc.reg_read(UC_MIPS_REG_0 + 4), machine.u32(patch["state"])))

        hooks = [uc.hook_add(UC_HOOK_CODE, setup_ui, begin=at, end=at)
                 for at in ((0x147088, 0x10a670) if versus else (0x143278,))]
        hooks.append(uc.hook_add(UC_HOOK_CODE, observe, begin=0x11d120, end=0x11d120))
        try:
            require(machine.call(thunk, request, count=10_000_000) == 0,
                    f"Original demo caller failed at {native:#x}")
        finally:
            for hook in hooks:
                uc.hook_del(hook)
        address = 0x272d94 if versus else 0x272b88
        root = machine.u32(address)
        require(root and seen == [(backup, 0)] and machine.read(patch["state"], 12) == bytes(12),
                "Original demo did not load its original board with Japanese judgement")
        machine.call(0x11cde0, root)
        uc.mem_write(address, bytes(4))
        checked.append(source)
    return checked


def verify_cursor_transfers(elf):
    machine = DrawMachine(elf)
    values = []

    def receive(uc, *_):
        values.append(uc.reg_read(UC_MIPS_REG_0 + 6))
        uc.emu_stop()

    machine.uc.hook_add(UC_HOOK_CODE, receive, begin=0x12b020, end=0x12b020)
    tokens = (69, 0x123, 957)
    for at in runtime.CURSOR_TILE_READS:
        for token in tokens:
            machine.uc.mem_write(machine.SOURCE, struct.pack("<H", token))
            machine.uc.reg_write(UC_MIPS_REG_V0, machine.SOURCE)
            machine.uc.emu_start(at - 4, machine.STOP, count=4)
    require(values == list(tokens) * len(runtime.CURSOR_TILE_READS),
            "Original cursor caller truncated a Korean tile")
    return len(values)


def verify_history(elf, report):
    import discovery_958 as d
    history = report["patch"].get("discovery")
    require(history and history["word_count"] == report["dictionary_word_count"],
            "Play/save discovery capacity was not compiled from the dictionary")
    if history.get("expanded") is False:
        require(history["word_count"] // 8 + 1 <= d.LEGACY_BYTES,
                "Legacy discovery history is too small")
        return {"word_count": history["word_count"], "expanded": False}
    machine = DrawMachine(elf)
    uc = machine.uc

    def log(uc, *_):
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    uc.hook_add(UC_HOOK_CODE, log, begin=0x185138, end=0x185138)
    size, bank = history["play_bytes"], history["bank"]
    source = machine.SOURCE
    uc.mem_write(source - 16, b"\xa5" * (size + 32))
    uc.mem_write(source, bytes(size))
    flags = bytes((i * 17 + 5) & 255 for i in range(0x187))
    uc.mem_write(d.PREFIX + d.LEGACY_BYTES, flags)
    first = d.LEGACY_BYTES * 8 - 8
    for wid in range(first, history["word_count"] + 1):
        machine.call(0x1155c0, source, wid)
    expected = bytes(uc.mem_read(source, size))
    require(all(expected[(wid - 1) // 8] & 1 << ((wid - 1) & 7)
                for wid in range(first, history["word_count"] + 1)), "High word history bits were lost")
    require(bytes(uc.mem_read(source - 16, 16)) == b"\xa5" * 16
            and bytes(uc.mem_read(source + size, 16)) == b"\xa5" * 16,
            "Play history crossed its allocation")
    machine.call(0x11c7e8, source)
    require(machine.call(0x11c7d8) == bank and bytes(uc.mem_read(bank, size)) == expected,
            "Play history did not commit to the persistent bitmap")
    require(bytes(uc.mem_read(d.PREFIX + d.LEGACY_BYTES, len(flags))) == flags,
            "History commit changed legacy unlock/score fields")
    machine.call(0x117180, 0x247028)
    machine.call(0x1177a8)
    payload = bytes(uc.mem_read(0x23d180, d.SAVE_BYTES))
    require(struct.unpack_from("<H", payload)[0] == 0x1023
            and machine.call(0x117450, 0x23d184, 0x33fc) == struct.unpack_from("<H", payload, 2)[0],
            "Expanded history did not pass the original save checksum")
    uc.mem_write(d.SAVE, payload)
    uc.mem_write(history["tail"], bytes(d.TAIL_BYTES))
    uc.mem_write(0x1c7420, struct.pack("<I", 1))
    machine.call(0x11c100)
    machine.call(0x11c7d8)
    require(bytes(uc.mem_read(bank, size)) == expected, "Save reload lost expanded history")
    return {"word_count": history["word_count"], "play_bytes": size,
            "high_word_ids_checked": history["word_count"] - first + 1,
            "original_save_bytes": d.SAVE_BYTES, "legacy_fields_unchanged": True,
            "native_checksum_and_reload": True}


def verify(elf, rom, dictionary, stages, report):
    boot = require_safe(elf)
    history = verify_history(elf, report)
    drawing = verify_drawing(elf, report)
    live_drawing = verify_piece_drawing(elf, report)
    lookups = verify_lookup_alphabet(elf, report)
    animation = verify_return_animation_copy(elf)
    cursor_transfers = verify_cursor_transfers(elf)
    print("[CHECK] 1,916 real font-primitive conversions", flush=True)
    machine = WideMachine(elf, rom)
    heap = machine.initialize_search()
    free = machine.u32(machine.root + 0x20)
    supply = verify_supply(machine)
    print("[CHECK] 958 native supply selection/removal/undo cases", flush=True)
    routes, versus = [], []
    for profile, record in zip(report["profiles"], report["patch"]["profiles"]):
        stage = stages.stages[profile["index"]]
        if profile.get("mode", "solo") == "versus":
            versus.append(verify_versus(machine, profile, record, report, stage))
        else:
            for variant in range(3):
                routes.extend(verify_route(machine, profile, record, report, stage, variant))
    demos = verify_demo_boards(machine, report, dictionary)
    japanese = verify_japanese(machine, report)
    require(machine.u32(machine.root + 0x20) == free, "Wide stage/supply tests leaked native heap")
    require(machine.read(machine.arena_start + machine.arena_size, 16) == b"\xa5" * 16,
            "Wide build crossed the native arena")
    candidates = verify_candidates(elf, rom, dictionary, report)
    segment = report["patch"]["segment_address"]
    segment_size = report["patch"]["segment_bytes"]
    segment_offset = machine.elf.offset(segment)
    # Only the selected profile and its owning live board may change.
    require(machine.read(segment + 8, segment_size - 8) == elf[segment_offset + 8:segment_offset + segment_size],
            "Runtime initialization/gameplay wrote over injected code or tables")
    return {"boot_memory": boot, "discovery_history": history, "drawing": drawing, "live_piece_drawing": live_drawing,
            "lookup": lookups, "return_animation": animation,
            "supply": supply, "native_route_steps": routes,
            "candidates": candidates, "japanese": japanese, "versus": versus,
            "original_demo_boards": [] if report["patch"].get("korean_demos") else demos,
            "korean_demo_replays": demos if report["patch"].get("korean_demos") else [],
            "cursor_transfers": cursor_transfers,
            "heap": heap, "allocations_released": True,
            "one_player_entry": "143460 -> 11CD28 with the requested profile index. UI setup at 143278 modeled.",
            "scope": "Saved binary resource load, full-ID supply undo, real board installs and word judgement. PCSX2 gameplay/GPU not run."}
