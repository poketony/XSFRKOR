"""Native ADV label getters, MIPS address pairs, draw operands and confirmation."""

import struct

from adv_format import require, text_end
from system_native import SystemMachine
from ui_labels import (catalogue, pair_address, NOTICE_FRAME, NOTICE_FRAME_PADDING,
                       NOTICE_FRAME_POSITIONS, NOTICE_FRAME_REFERENCES)
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3,
                               UC_MIPS_REG_GP, UC_MIPS_REG_SP, UC_MIPS_REG_RA, UC_MIPS_REG_PC,
                               UC_MIPS_REG_S0, UC_MIPS_REG_S1, UC_MIPS_REG_T0, UC_MIPS_REG_T1,
                               UC_MIPS_REG_T2, UC_MIPS_REG_V0)


STATE = 0x1400000


class LabelMachine(SystemMachine):
    def __init__(self, original, data, font_data):
        super().__init__(original, data, "ADV", font_data)
        self.uc.hook_del(self.option_hook)
        self.labels = catalogue(original)
        self.draws = []
        self.uc.hook_add(UC_HOOK_CODE, self.capture_draw, begin=0x1010d8, end=0x1010d8)

    def capture_draw(self, uc, address, size, _):
        self.draws.append({"pointer": uc.reg_read(UC_MIPS_REG_T0),
                           "x": uc.reg_read(UC_MIPS_REG_A2), "y": uc.reg_read(UC_MIPS_REG_A3)})
        uc.reg_write(UC_MIPS_REG_V0, 0)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def resolve(self, item):
        values = {struct.unpack("<I", self.uc.mem_read(field, 4))[0] for field in item.pointers}
        values.update(pair_address(self.elf.data, self.elf, upper, lower) for upper, lower in item.pairs)
        require(len(values) == 1, "UI label references disagree")
        return values.pop()

    def raw(self, pointer):
        block = bytes(self.uc.mem_read(pointer, 2048))
        return block[:text_end(block, 0)]

    def fragment(self, start, stop):
        self.uc.reg_write(UC_MIPS_REG_GP, self.elf.gp)
        self.uc.reg_write(UC_MIPS_REG_SP, self.machine.STACK)
        self.uc.reg_write(UC_MIPS_REG_RA, self.machine.STOP)
        reached = []

        def boundary(uc, address, size, _):
            reached.append(address)
            uc.emu_stop()

        hook = self.uc.hook_add(UC_HOOK_CODE, boundary, begin=stop, end=stop)
        try:
            self.uc.emu_start(start, self.machine.STOP, count=200000)
        finally:
            self.uc.hook_del(hook)
        require(reached == [stop], "Native UI fragment did not reach expected call boundary")

    def text_width(self, pointer):
        widths = []
        for line in self.raw(pointer).split(b"\n"):
            self.uc.mem_write(self.machine.SOURCE, line + b"\0")
            self.call(0x1017c0, self.machine.SOURCE)
            widths.append(self.uc.reg_read(UC_MIPS_REG_V0))
        return max(widths)

    def getters(self):
        count = 0
        for index in range(18):
            self.call(0x1318b0, index)
            expected = struct.unpack("<I", self.uc.mem_read(0x178dac + index * 12, 4))[0]
            require(self.uc.reg_read(UC_MIPS_REG_V0) == expected, "Native location getter differs")
            count += 1
        for index in range(4):
            self.uc.mem_write(0x84adf4, struct.pack("<I", index))
            self.call(0x132540, STATE)
            actual = struct.unpack("<I", self.uc.mem_read(STATE + 0xa0, 4))[0]
            expected = struct.unpack("<I", self.uc.mem_read(0x1791b0 + index * 4, 4))[0]
            require(actual == expected, "Native travel label getter differs")
            self.uc.reg_write(UC_MIPS_REG_V0, index)
            self.fragment(0x12a764, 0x12a774)
            expected = struct.unpack("<I", self.uc.mem_read(0x178500 + index * 4, 4))[0]
            require(self.uc.reg_read(UC_MIPS_REG_A1) == expected, "Native location prefix getter differs")
            count += 2
        return count

    def confirmation(self, index):
        self.uc.mem_write(self.elf.gp - 0x7a4b, bytes((index,)))
        pointer = struct.unpack("<I", self.uc.mem_read(0x178580 + index * 4, 4))[0]
        tail = next(item for item in self.labels if item.kind == "confirm")
        expected = self.raw(pointer) + self.raw(self.resolve(tail))
        require(len(expected) < 256, "Expected character confirmation exceeds stack buffer")
        base = self.machine.STACK - 0x120
        self.uc.mem_write(base - 16, b"\xa5" * (0x120 + 32))
        self.uc.reg_write(UC_MIPS_REG_S0, 0x11223344)
        self.uc.reg_write(UC_MIPS_REG_S1, 0x55667788)
        self.fragment(0x12c3e0, 0x126e28)
        require(self.uc.reg_read(UC_MIPS_REG_SP) == base and self.uc.reg_read(UC_MIPS_REG_A0) == base,
                "Native character confirmation stack buffer differs")
        require(bytes(self.uc.mem_read(base, 256)) == expected + b"\0" + b"\xa5" * (255 - len(expected)),
                "Native character name/confirmation concatenation differs")
        # strcpy/strcat callees legitimately save registers below this frame.
        require(bytes(self.uc.mem_read(base + 0x100, 24)) == struct.pack("<3Q", 0x11223344, 0x55667788, self.machine.STOP),
                "Native confirmation crossed into saved registers")
        self.uc.mem_write(self.machine.SOURCE, expected + b"\0")
        return self.format_at(self.machine.SOURCE, "message", 500)

    def dialogs_and_draws(self):
        count = 0
        for item in self.labels:
            destination = self.resolve(item)
            require(self.text_width(destination) <= (600 if item.kind in ("confirm", "quit") else item.budget),
                    "Native UI label width exceeds budget")
            if item.kind == "prompt":
                self.fragment(0x12c250, 0x126ed8)
                require(self.uc.reg_read(UC_MIPS_REG_A0) == destination, "Native character prompt address differs")
                self.format_at(destination, "message", item.budget)
                count += 1
            elif item.kind == "quit":
                self.fragment(0x12e050, 0x126e28)
                require(self.uc.reg_read(UC_MIPS_REG_A0) == destination, "Native quit prompt address differs")
                self.format_at(destination, "message", item.budget)
                count += 1
            elif item.kind == "no_data":
                self.fragment(0x12a748, 0x12a750)
                require(self.uc.reg_read(UC_MIPS_REG_V0) == destination, "Native empty-data label address differs")
                count += 1
            for command in item.commands:
                self.uc.mem_write(STATE, struct.pack("<I", command) + bytes(252))
                before = len(self.draws)
                self.call(0x113ed0, STATE)
                require(len(self.draws) == before + 1 and self.draws[-1]["pointer"] == destination,
                        "Native 74 draw command string operand differs")
                x, y = struct.unpack("<HH", self.uc.mem_read(command + 1, 4))
                require((self.draws[-1]["x"], self.draws[-1]["y"]) == (x, y), "UI text coordinates changed")
                require(struct.unpack("<I", self.uc.mem_read(STATE, 4))[0] == command + 9, "UI text command length changed")
                count += 1
        return count

    def choice_layout(self):
        from adv_spacing import CHOICE_ADDRESS, CHOICE_RAW, FULL_SPACE
        raw = self.raw(CHOICE_ADDRESS)
        if raw == CHOICE_RAW:
            self.uc.mem_write(self.machine.SOURCE, FULL_SPACE + b"\0")
            self.call(0x1017c0, self.machine.SOURCE)
            require(self.uc.reg_read(UC_MIPS_REG_V0) >= 20,
                    "Original YES/NO template is uncompensated with the narrow ADV font")
            return {"compensated": False}
        expected = {}
        for label, code, left in (("YES", b"\x82\x78", 156), ("NO", b"\x82\x6d", 256)):
            prefix = raw[2:raw.index(code)]
            self.uc.mem_write(self.machine.SOURCE, prefix + b"\0")
            self.call(0x1017c0, self.machine.SOURCE)
            actual = self.uc.reg_read(UC_MIPS_REG_V0)
            require(actual == left, f"Native {label} no longer matches its original horizontal position")
            expected[label] = actual
        blank = self.lookup(b"\xa0")
        require(blank["left"] + blank["advance"] == 16
                and not any(self.font.glyph(blank["face"], blank["index"])), "YES/NO prefix padding is not a blank 16px glyph")
        # Reset the paragraph as its owning dialog does, then format the row.
        self.uc.mem_write(self.machine.SOURCE, b"\x0b" + raw[2:] + b"\0")
        self.format_at(self.machine.SOURCE, "message", 500)
        return {"compensated": True, "original_relative_x": expected, "cursor_code": "unchanged",
                "blank_padding_glyph": "native 16px, empty bitmap"}


def verify(original, data, font_data):
    machine = LabelMachine(original, data, font_data)
    getters = machine.getters()
    draws = machine.dialogs_and_draws()
    lines = sum(machine.confirmation(index) for index in range(7))
    choice = machine.choice_layout()
    if choice["compensated"]:
        from adv_choice_native import verify_choice_path
        choice["native_path"] = verify_choice_path(original, data, font_data)
    from adv_menu_native import verify as verify_menu_raster
    menu_raster = verify_menu_raster(data, font_data)
    return {"labels": len(machine.labels), "native_getters": getters, "native_draw_and_direct_paths": draws,
            "native_character_confirmations": 7, "confirmation_lines": lines,
            "confirmation_stack_bytes": 256, "saved_registers": "preserved",
            "native_gothic_widths": "pass", "choice_layout": choice, "menu_raster": menu_raster,
            "gpu": "draw arguments captured only"}


class NoticeMachine(SystemMachine):
    def __init__(self, original, data, font_data):
        super().__init__(original, data, "LNCH", font_data)
        self.notice = catalogue(original, "LNCH")[0]
        self.events = []
        for address in (0x1010d8, 0x1015c0):
            self.uc.hook_add(UC_HOOK_CODE, self.capture, begin=address, end=address)

    def capture(self, uc, address, size, _):
        if address == 0x1010d8:
            self.events.append({"kind": "draw", "pointer": uc.reg_read(UC_MIPS_REG_T0),
                                "x": uc.reg_read(UC_MIPS_REG_A2), "y": uc.reg_read(UC_MIPS_REG_A3)})
        else:
            self.events.append({"kind": "placement", "pointer": uc.reg_read(UC_MIPS_REG_A3),
                                "offset_x_u32": uc.reg_read(UC_MIPS_REG_T0) & 0xffffffff,
                                "offset_y": uc.reg_read(UC_MIPS_REG_T1),
                                "rgba": bytes(uc.mem_read(uc.reg_read(UC_MIPS_REG_T2), 4)).hex()})
            # Run the original text-model writer, rather than only its arguments.
            return
        uc.reg_write(UC_MIPS_REG_V0, 0)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def run_notice(self):
        command = self.notice.commands[0]
        pointer = struct.unpack("<I", self.uc.mem_read(command + 5, 4))[0]
        raw_at = self.elf.offset(pointer)
        raw = self.elf.data[raw_at:text_end(self.elf.data, raw_at)]
        require(len(raw) < 127, "Notice exceeds verified line buffer")
        self.call(0x1017c0, pointer)
        pixels = self.uc.reg_read(UC_MIPS_REG_V0)
        require(0 < pixels <= self.notice.budget, "Native notice width exceeds budget")
        text_reference = 0x1244aa
        require(bytes(self.uc.mem_read(text_reference, 5)) == b"\x47" + struct.pack("<I", 0x123c16),
                "Original notice text-model reference differs")
        self.uc.mem_write(STATE, struct.pack("<I", text_reference) + bytes(252))
        self.call(0x1129e8, STATE)
        text_model = struct.unpack("<I", self.uc.mem_read(STATE + 0x10, 4))[0]
        self.uc.mem_write(STATE, struct.pack("<I", command))
        self.call(0x114180, STATE, count=1000000)
        require([e["kind"] for e in self.events] == ["draw", "placement"], "Native notice callbacks differ")
        draw, placement = self.events
        require(draw["pointer"] == placement["pointer"] == pointer, "Native notice did not read relocated text")
        require((draw["x"], draw["y"]) == (0, 210) and placement["rgba"] == "70707080",
                "Notice coordinates or colour changed")
        require(placement["offset_x_u32"] == (-(pixels // 2) & 0xffffffff) and placement["offset_y"] == 0,
                "Native notice centering does not match the new text width")
        require(struct.unpack("<I", self.uc.mem_read(STATE, 4))[0] == command + 19,
                "Native notice command boundary changed")
        text_quad = struct.unpack("<5h", self.uc.mem_read(text_model + 12, 10))
        require(struct.unpack("<H", self.uc.mem_read(text_model, 2))[0] == 2
                and text_quad[:3] == (0x68, -(pixels // 2), 0)
                and text_quad[3] - text_quad[1] == pixels and 0 < text_quad[4] <= 40,
                "Native notice text-model quad differs from its measured placement")
        models = []
        for reference in NOTICE_FRAME_REFERENCES:
            self.uc.mem_write(STATE, struct.pack("<I", reference) + bytes(252))
            self.call(0x1129e8, STATE)
            models.append(struct.unpack("<I", self.uc.mem_read(STATE + 0x10, 4))[0])
            require(struct.unpack("<I", self.uc.mem_read(STATE, 4))[0] == reference + 5,
                    "Notice model-reference command boundary changed")
        require(len(set(models)) == 1, "Notice animation states disagree on their frame model")
        from notice_primitive_native import NoticePrimitiveMachine
        primitive = NoticePrimitiveMachine(self.elf.data).run(models[0])
        vertices = primitive["decoded_xy"]
        width, height = vertices[-1]
        require(vertices == [[0, 0], [width, 0], [0, height], [width, height]],
                "Native notice frame rectangle vertices differ")
        require(height == 40 and width % 2 == 0 and 390 <= width <= 640, "Notice frame size differs")
        require(width >= pixels + (NOTICE_FRAME_PADDING if raw != self.notice.raw else 0),
                "Notice text overlaps the frame padding")
        expected_uv = [[488.5, 140.5], [878.5, 140.5], [488.5, 180.5], [878.5, 180.5]]
        require([v["uv"] for v in primitive["packet_vertices"]] == expected_uv
                and primitive["clamp_uv_minmax"] == [488, 877, 140, 179]
                and primitive["clamp_modes"] == [2, 2],
                "Native notice texture sampling differs from SAVELOAD image 7")
        require([v["xy"] for v in primitive["packet_vertices"]] == vertices,
                "Native notice XY packet differs from its rectangle")
        for position, sign, y in NOTICE_FRAME_POSITIONS:
            self.uc.mem_write(STATE, struct.pack("<I", position) + bytes(252))
            self.call(0x112c48, STATE)
            actual = struct.unpack("<3f", self.uc.mem_read(STATE + 0x34, 12))
            require(actual == (sign * width / 2, y, 0), "Native notice frame position is not centered")
            require(struct.unpack("<I", self.uc.mem_read(STATE, 4))[0] == position + 7,
                    "Notice frame position command boundary changed")
        return {"native_draws": 1, "native_width": pixels, "text_y": draw["y"], "centering": "original width/2 logic",
                "frame_width": width, "frame_height": height, "frame_center_x": 0,
                "minimum_side_padding": (width - pixels) // 2, "frame_positions_checked": len(NOTICE_FRAME_POSITIONS),
                "native_rectangle_vertices": vertices, "native_primitive": primitive,
                "model": f"{models[0]:08X}", "model_references_checked": len(models),
                "native_text_quad": list(text_quad),
                "continuation": f"{command + 19:08X}", "colour": placement["rgba"],
                "gpu": "native UV/clamp/XY packet; VU identity/quantization modeled, no GS rasterization"}


def verify_notice(original, data, font_data):
    return NoticeMachine(original, data, font_data).run_notice()
