"""Actual ADV confirmation-row parser, caller raster and cursor bytecodes.

GS packets are compared with the original under the same supplied texture
registry. A boot-uninitialized registry is explicitly not production VRAM proof.
"""

import hashlib
import struct

from adv_format import require, text_end, tokens
from adv_menu_native import MenuMachine, STATE
from adv_spacing import CHOICE_ADDRESS, CHOICE_RAW, FULL_SPACE, LAYOUT_SPACE
from font_tool import original_font
from system_text import buffer_shape
from ui_labels import catalogue, pair_address
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import (UC_MIPS_REG_GP, UC_MIPS_REG_PC, UC_MIPS_REG_RA,
                               UC_MIPS_REG_SP, UC_MIPS_REG_V0, UC_MIPS_REG_S1,
                               UC_MIPS_REG_S2, UC_MIPS_REG_S4, UC_MIPS_REG_S5,
                               UC_MIPS_REG_S0, UC_MIPS_REG_S3, UC_MIPS_REG_A3)


DIALOG = 0x84a508
REGISTRY = 0x16a3b8
ROW_LOOP = 0x128095
ROW_MODEL = 0x127dd6
CURSOR_SCRIPT = 0x1286cb
CURSOR_READY = 0x1286f7
OWNER_SPEC = 0x178388


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ChoiceMachine(MenuMachine):
    def __init__(self, original, data, font, registry=None):
        super().__init__(data, font)
        dialog_base, self.row_count = buffer_shape(original, "ADV")
        require(dialog_base == DIALOG and self.row_count == 5, "ADV dialog buffer shape differs")
        # Native bytecode indexing and libgcc signed-int-to-float conversion use
        # additional R5900 destination-register multiplies. No font/branch stub.
        extra = (0x1107ac, 0x1525d0, 0x1525dc)
        self.mults += extra
        for address in extra:
            word = struct.unpack_from("<I", data, self.elf.offset(address))[0]
            require(word >> 26 in (0, 0x1c) and word & 63 == 24 and word >> 11 & 31,
                    "Unaudited choice multiply")
            self.uc.mem_write(address, bytes(4))
            self.uc.hook_add(UC_HOOK_CODE, self.capture, begin=address, end=address)
        if registry is not None:
            require(len(registry) == 6, "Choice fixture must supply exactly registry entry zero")
            self.uc.mem_write(REGISTRY, registry)
        self.registry = bytes(self.uc.mem_read(REGISTRY, 6))

    def initialize_context(self, *, vram_cursor=0):
        # Dialogs use their own direct spec, not the pause menu's base-32 spec.
        require(vram_cursor == 0, "The dialog VRAM baseline has not been established")
        spec = struct.unpack_from("<2I10H", self.elf.data, self.elf.offset(OWNER_SPEC))
        require(spec == (0x11e7a0, 0x127e34) + (0,) * 10, "ADV dialog owning spec differs")
        parent = STATE + 0x3000
        self.uc.mem_write(parent, bytes(0x120))
        self.call(0x112748, parent + 0x10)
        for register, value in ((UC_MIPS_REG_S0, parent + 0x10), (UC_MIPS_REG_S2, OWNER_SPEC),
                                (UC_MIPS_REG_S3, parent + 0x110)):
            self.uc.reg_write(register, value)
        self.fragment(0x11d9cc, 0x11da38)
        require(struct.unpack("<H", self.uc.mem_read(parent + 0x30, 2))[0] == 0,
                "Original dialog constructor did not install texture base zero")
        self.uc.mem_write(self.elf.gp - 0x7f0c, struct.pack("<I", parent))
        self.uc.reg_write(UC_MIPS_REG_A3, STATE - 0x10)
        self.fragment(0x10c4d4, 0x10c504)
        require(bytes(self.uc.mem_read(STATE, 256)) == bytes(self.uc.mem_read(parent + 0x10, 256)),
                "Original dialog child copy did not preserve owner state")
        return {"owner_spec": f"{OWNER_SPEC:08X}", "texture_base": 0,
                "constructor": "0011D9CC..0011DA38", "child_copy": "0010C4D4..0010C504",
                "physical_texture_registry": "not established"}

    def raw(self, pointer):
        block = bytes(self.uc.mem_read(pointer, 2048))
        return block[:text_end(block, 0)]

    def resolve(self, item):
        addresses = {struct.unpack("<I", self.uc.mem_read(field, 4))[0] for field in item.pointers}
        addresses.update(pair_address(self.elf.data, self.elf, upper, lower) for upper, lower in item.pairs)
        require(len(addresses) == 1, "Choice source references disagree")
        return addresses.pop()

    def fragment(self, start, stop, count=200000):
        self.uc.reg_write(UC_MIPS_REG_GP, self.elf.gp)
        self.uc.reg_write(UC_MIPS_REG_SP, self.machine.STACK)
        self.uc.reg_write(UC_MIPS_REG_RA, self.machine.STOP)
        hook = self.uc.hook_add(UC_HOOK_CODE, lambda uc, pc, size, _: uc.emu_stop(), begin=stop, end=stop)
        try:
            self.uc.emu_start(start, self.machine.STOP, count=count)
        finally:
            self.uc.hook_del(hook)
        require(self.uc.reg_read(UC_MIPS_REG_PC) == stop,
                f"Choice caller did not reach {stop:08X}; stopped {self.uc.reg_read(UC_MIPS_REG_PC):08X}")

    def character(self, index):
        self.uc.mem_write(self.elf.gp - 0x7a4b, bytes((index,)))
        self.fragment(0x12c3e0, 0x126e28)
        raw = bytes(self.uc.mem_read(self.uc.reg_read(UC_MIPS_REG_SP), 256))
        return raw[:text_end(raw, 0)]

    def parse(self, source):
        raw = self.raw(source)
        output = self.machine.OUTPUT
        cursor, trace = source, []
        for _ in range(len(raw) + 2):
            self.call(0x1274e8, cursor, output, output + 4, count=2000000)
            destination = self.uc.reg_read(UC_MIPS_REG_V0)
            mode, argument = struct.unpack("<2I", self.uc.mem_read(output, 8))
            trace.append([cursor - source, destination - source, mode, argument if mode else 0])
            require(cursor <= destination <= source + len(raw), "Choice parser left its source")
            if mode == 0:
                require(destination == source + len(raw), "Choice parser did not consume its source")
                return trace
            require(destination > cursor, "Choice parser did not advance")
            cursor = destination
        raise ValueError("Choice parser did not terminate")

    def dialog(self, raw):
        self.uc.mem_write(DIALOG - 16, b"\xa5" * (0x2a4 + 32))
        self.uc.mem_write(self.machine.SOURCE, raw + b"\0")
        prompt_trace = self.parse(self.machine.SOURCE)
        before = struct.unpack("<I", self.uc.mem_read(DIALOG + 0x294, 4))[0]
        append_trace = self.parse(CHOICE_ADDRESS)
        index = struct.unpack("<I", self.uc.mem_read(DIALOG + 0x294, 4))[0]
        require(index == before + 1 and index < self.row_count, "Choice append used the wrong row")
        require(bytes(self.uc.mem_read(DIALOG - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(DIALOG + 0x2a4, 16)) == b"\xa5" * 16,
                "Choice formatting crossed the dialog allocation")
        row = self.raw(DIALOG + index * 128)
        expected = b"".join(code for control, code in tokens(self.raw(CHOICE_ADDRESS)) if not control)
        require(row == expected, "Choice append did not retain its padded YES/NO row")
        self.events.clear()
        self.clears.clear()
        self.uc.mem_write(STATE, struct.pack("<I", ROW_LOOP) + bytes(252))
        self.call(0x112660, STATE, count=4000000)
        event = next(event for event in self.events if event["pointer"] == DIALOG + index * 128)
        require(event["accepted"], "Native choice transfer guard rejected its real draw coordinates")
        require(event["registers"]["x"] == 0 and event["registers"]["y"] == 114 + index * 20,
                "Native choice row transfer position differs")
        return {"row": index, "raw": row, "prompt_trace": prompt_trace,
                "append_trace": append_trace, "event": event}

    def cursors(self, row):
        positions = []
        for selection in (0, 1):
            self.uc.mem_write(STATE, struct.pack("<I", CURSOR_SCRIPT) + bytes(252))
            self.uc.mem_write(DIALOG + 0x2a0, struct.pack("<I", selection))
            self.uc.mem_write(DIALOG + 0x294, struct.pack("<I", row))
            for _ in range(20):
                command = struct.unpack("<I", self.uc.mem_read(STATE, 4))[0]
                if command == CURSOR_READY:
                    break
                opcode = self.uc.mem_read(command, 1)[0]
                handler = struct.unpack("<I", self.uc.mem_read(0x16b2f8 + opcode * 4, 4))[0]
                self.call(handler, STATE)
            else:
                raise ValueError("Native cursor coordinates did not finish")
            positions.append(list(struct.unpack("<3f", self.uc.mem_read(STATE + 0x34, 12))))
        require(positions == [[-38.0, 116.0 + row * 20, 0.0], [52.0, 116.0 + row * 20, 0.0]],
                "Native choice cursor coordinates changed")
        return positions

    def selection(self, initial, buttons, edges):
        self.uc.mem_write(DIALOG + 0x2a0, struct.pack("<I", initial))
        self.uc.mem_write(0x16a9b2, struct.pack("<2H", buttons, edges))
        for reg, value in ((UC_MIPS_REG_S1, 0x16a9a8), (UC_MIPS_REG_S2, 1),
                           (UC_MIPS_REG_S4, 0x850000), (UC_MIPS_REG_S5, 0x170000)):
            self.uc.reg_write(reg, value)
        # These calls only request sound/widget presentation. The original
        # selection branches and state stores execute; hardware input is given.
        hooks = [self.uc.hook_add(UC_HOOK_CODE, self.capture, begin=address, end=address)
                 for address in (0x11cc60, 0x11cb38, 0x11dc10)]
        try:
            self.fragment(0x1270c8, 0x127178)
        finally:
            for hook in hooks:
                self.uc.hook_del(hook)
        return struct.unpack("<I", self.uc.mem_read(DIALOG + 0x2a0, 4))[0]


def event_report(event):
    report = {key: value for key, value in event.items() if key not in ("pixels", "packet")}
    report["packet_sha256"] = digest(event["packet"])
    return report


def verify_choice_path(original, output, font, *, registry=None):
    """Compare the final row with the original, not a Python font renderer."""
    # The original register consumers and model resources are the same binary
    # code. The fixture proves the changed text under this unchanged consumer.
    from elf_probe import Elf
    original_elf, built_elf = Elf(original), Elf(output)
    for start, end in ((0x126fc8, 0x127300), (0x1274e8, 0x127c00), (0x127c00, 0x12879c)):
        require(original[original_elf.offset(start):original_elf.offset(end)]
                == output[built_elf.offset(start):built_elf.offset(end)],
                "Choice consumer/cursor/model code was unexpectedly edited")
    old = ChoiceMachine(original, original, original_font(), registry)
    current = ChoiceMachine(original, output, font, registry)
    raw = current.raw(CHOICE_ADDRESS)
    require(len(raw) == len(CHOICE_RAW) and len(list(tokens(raw))) == len(list(tokens(CHOICE_RAW))),
            "Choice byte or timing-token count changed")
    prefixes, original_prefixes = {}, {}
    for name, code, expected in (("YES", b"\x82\x78", 156), ("NO", b"\x82\x6d", 256)):
        old_raw = old.raw(CHOICE_ADDRESS)
        old.uc.mem_write(old.machine.SOURCE, old_raw[2:old_raw.index(code)] + b"\0")
        old.call(0x1017c0, old.machine.SOURCE)
        original_prefixes[name] = old.uc.reg_read(UC_MIPS_REG_V0)
        require(original_prefixes[name] == expected, "Original native choice origin differs")
        prefix = raw[2:raw.index(code)]
        current.uc.mem_write(current.machine.SOURCE, prefix + b"\0")
        current.call(0x1017c0, current.machine.SOURCE)
        pixels = current.uc.reg_read(UC_MIPS_REG_V0)
        prefixes[name] = pixels
        require(pixels == expected, f"Choice {name} origin {pixels}px != original {expected}px")
    for code, expected in ((FULL_SPACE, 11), (LAYOUT_SPACE, 20), (b"\xa0", 16), (b" ", 10)):
        for style in ((0,) if code == b"\xa0" else (0, 1)):
            glyph = current.lookup(code, style)
            require(glyph["left"] + glyph["advance"] == expected
                    and not any(current.font.glyph(glyph["face"], glyph["index"])),
                    "Choice padding font metric or transparency differs")
    dialogs = []
    items = catalogue(original)
    for index in range(8):
        if index < 7:
            prompt = current.character(index)
            old_prompt = old.character(index)
            name = f"character-{index}"
        else:
            item = next(item for item in items if item.kind == "quit")
            prompt = current.raw(current.resolve(item))
            old_prompt = old.raw(old.resolve(item))
            name = "quit"
        before = old.dialog(old_prompt)
        after = current.dialog(prompt)
        require(before["append_trace"] == after["append_trace"], "Choice append timing/control flow differs")
        require(before["row"] == after["row"], "Choice current row differs from the original")
        require(before["event"]["pixels"] == after["event"]["pixels"], "Choice raster differs from original")
        require(before["event"]["packet"] == after["event"]["packet"], "Choice GS packet differs from original")
        cursor = current.cursors(after["row"])
        require(cursor == old.cursors(before["row"]), "Choice cursor differs from original")
        dialogs.append({"name": name, "row": after["row"], "prompt_sha256": digest(prompt),
                        "original_prompt_sha256": digest(old_prompt),
                        "append_trace": after["append_trace"], "cursor_positions": cursor,
                        "event": event_report(after["event"])})
    model = current.primitive(ROW_MODEL)
    require(model == old.primitive(ROW_MODEL), "Choice text model sampling differs from original")
    require(model["uv_bounds"] == [0, 114, 520, 214], "Choice native text model UV bounds differ")
    selection = []
    for initial, buttons, edges, expected in ((0, 0x8000, 0x8000, 0), (1, 0x8000, 0x8000, 0),
                                               (0, 0x2000, 0x2000, 1), (1, 0x2000, 0x2000, 1),
                                               (0, 0x2000, 0, 0), (1, 0x8000, 0, 1)):
        actual = current.selection(initial, buttons, edges)
        require(actual == expected == old.selection(initial, buttons, edges),
                "Native YES/NO left/right selection changed")
        selection.append({"initial": initial, "buttons": buttons, "edges": edges, "selected": actual})
    return {"literal": f"{CHOICE_ADDRESS:08X}", "literal_hex": raw.hex(),
            "original_relative_x": original_prefixes, "current_relative_x": prefixes,
            "dialogs": dialogs, "text_model": model, "selection_inputs": selection,
            "native_owner_context": current.context,
            "registry_prefix_hex": current.registry.hex(),
            "registry_provenance": "caller-supplied" if registry is not None else "boot-uninitialized",
            "verdict": "original-equivalent raster, packet, model, cursor and append timing",
            "limitations": "No boot resource loader, VRAM/GPU composition, animation frame or PCSX2 run. "
                           "Sound/widget-request callees are skipped for selection branch checks. "
                           "An uninitialized registry proves relational equivalence, not physical VRAM backing."}
