"""ADV cmd74 raster and native GS packet probe (no GPU or resource loader)."""

import hashlib
import struct

from adv_format import require, text_end
from font_native import FontMachine
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import (UC_MIPS_REG_0, UC_MIPS_REG_A0, UC_MIPS_REG_A1,
                               UC_MIPS_REG_A2, UC_MIPS_REG_A3, UC_MIPS_REG_T0,
                               UC_MIPS_REG_T1, UC_MIPS_REG_T2, UC_MIPS_REG_T3,
                               UC_MIPS_REG_S0, UC_MIPS_REG_S2, UC_MIPS_REG_S3,
                               UC_MIPS_REG_S4, UC_MIPS_REG_S7, UC_MIPS_REG_GP, UC_MIPS_REG_SP,
                               UC_MIPS_REG_RA, UC_MIPS_REG_PC, UC_MIPS_REG_V0,
                               UC_MIPS_REG_HI, UC_MIPS_REG_LO)


STATE = 0x1400000
SCRATCH = STATE + 0x1000
TEXT_MODEL = 0x1337e6
FRAME_MODELS = (0x13378c, 0x13379e, 0x1337b0, 0x1337c2)
OWNER_SPEC = 0x136c0f
TEXTURE_TABLE = 0x16a3b8


class MenuMachine(FontMachine):
    def __init__(self, data, font, *, vram_cursor=0):
        super().__init__(data, font)
        self.events = []
        self.clears = []
        self.active = None
        self.packet_start = None
        self.packet_end = None
        # MULT rd/MULT1 rd are R5900 GPR-writing forms, not generic MIPS.
        self.mults = (0x101ff8, 0x1012b4, 0x1012bc, 0x1012c0,
                      0x102448, 0x102474, 0x102594, 0x105378, 0x10537c, 0x105398,
                      0x116748, 0x116828, 0x116830)
        for address in self.mults:
            word = struct.unpack_from("<I", data, self.elf.offset(address))[0]
            require(word >> 26 in (0, 0x1c) and word & 63 == 24 and word >> 11 & 31,
                    "Unaudited R5900 multiply")
            # Execute a NOP after the semantic hook so delay-slot JALs still
            # take effect. Writing PC from a delay slot loses that branch.
            self.uc.mem_write(address, bytes(4))
        for address in (0x105300, 0x105310):
            self.uc.mem_write(address, bytes(4))
        for address in (*self.mults, 0x1010d8, 0x1021d0, 0x1072b8, 0x105230,
                        0x1052c0, 0x1052d8, 0x105478, 0x1052b8, 0x1072d8, 0x15d220,
                        0x102368, 0x1025c4, 0x105300, 0x105310):
            self.uc.hook_add(UC_HOOK_CODE, self.capture, begin=address, end=address)
        self.context = self.initialize_context(vram_cursor=vram_cursor)

    def fragment(self, start, stop, count=10000):
        reached = []

        def boundary(uc, pc, size, _):
            reached.append(pc)
            uc.emu_stop()

        hook = self.uc.hook_add(UC_HOOK_CODE, boundary, begin=stop, end=stop)
        try:
            self.uc.emu_start(start, self.machine.STOP, count=count)
        finally:
            self.uc.hook_del(hook)
        require(reached == [stop], f"Native menu fragment {start:08X} did not reach {stop:08X}")

    def initialize_context(self, *, vram_cursor=0):
        """Replay source allocation/spec/copy code; absolute cursor is a fixture.

        Do not call its default cursor zero an observed in-game VRAM address.
        The real owner spec and relative allocation/stride/PSM are verified.
        """
        require(0 <= vram_cursor <= 0x2000 and vram_cursor % 32 == 0, "Unaudited VRAM cursor fixture")
        self.uc.mem_write(self.elf.gp - 0x7f50, struct.pack("<H", vram_cursor))
        allocations = []
        for command, expected in ((0x13615b, "6910ffff6000030001"),
                                  (0x136164, "6920ffff60c003c001")):
            at = self.elf.offset(command)
            require(self.elf.data[at:at + 9] == bytes.fromhex(expected), "Original texture allocation command differs")
            self.uc.mem_write(STATE, struct.pack("<I", command))
            self.call(0x1136e8, STATE)
            require(struct.unpack("<I", self.uc.mem_read(STATE, 4))[0] == command + 9,
                    "Texture allocation command continuation differs")
            index = self.elf.data[at + 1]
            descriptor = list(struct.unpack("<3H", self.uc.mem_read(TEXTURE_TABLE + index * 6, 6)))
            allocations.append({"command": f"{command:08X}", "slot": index,
                                "request": list(struct.unpack_from("<HB2H", self.elf.data, at + 2)),
                                "descriptor": descriptor})

        parent = STATE + 0x3000
        spec_at = self.elf.offset(OWNER_SPEC)
        spec = struct.unpack_from("<2I10H", self.elf.data, spec_at)
        require(spec == (0x11e7a0, 0x137468, 0x1005, 0, 0, 0, 50, 32, 0, 0, 0, 0),
                "Pause-menu owning object definition differs")
        self.uc.mem_write(parent, bytes(0x120))
        self.call(0x112748, parent + 0x10)
        # cmd2E's real loader copies the unaligned inline spec to aligned stack.
        self.uc.mem_write(STATE, struct.pack("<I", OWNER_SPEC - 5))
        self.uc.reg_write(UC_MIPS_REG_S0, STATE)
        self.fragment(0x11ef60, 0x11efa4)
        require(bytes(self.uc.mem_read(self.machine.STACK, 28)) == self.elf.data[spec_at:spec_at + 28],
                "Original owning object inline-spec copy differs")
        for register, value in ((UC_MIPS_REG_S0, parent + 0x10), (UC_MIPS_REG_S2, self.machine.STACK),
                                (UC_MIPS_REG_S3, parent + 0x110)):
            self.uc.reg_write(register, value)
        self.fragment(0x11d9cc, 0x11da38)
        require(struct.unpack("<H", self.uc.mem_read(parent + 0x30, 2))[0] == 32,
                "Original owner constructor did not install texture base 32")
        self.uc.mem_write(self.elf.gp - 0x7f0c, struct.pack("<I", parent))
        self.uc.reg_write(UC_MIPS_REG_A3, STATE - 0x10)
        self.fragment(0x10c4d4, 0x10c504)
        require(bytes(self.uc.mem_read(STATE, 256)) == bytes(self.uc.mem_read(parent + 0x10, 256)),
                "Original child allocator did not preserve owner UI state")
        return {"owner_spec": f"{OWNER_SPEC:08X}", "owner_id": "1005", "texture_base": 32,
                "constructor": "0011D9CC..0011DA38", "child_copy": "0010C4D4..0010C504",
                "inline_spec_loader": "0011EF60..0011EFA4",
                "allocations": allocations, "vram_cursor_fixture": vram_cursor,
                "absolute_vram_cursor_observed": False}

    def capture(self, uc, address, size, _):
        if address in self.mults:
            word = struct.unpack_from("<I", self.elf.data, self.elf.offset(address))[0]
            rs, rt, rd = (word >> shift & 31 for shift in (21, 16, 11))
            signed = lambda v: (v & 0x7fffffff) - (v & 0x80000000)
            product = signed(uc.reg_read(UC_MIPS_REG_0 + rs)) * signed(uc.reg_read(UC_MIPS_REG_0 + rt))
            extend = lambda v: (v & 0x7fffffff) - (v & 0x80000000)
            uc.reg_write(UC_MIPS_REG_0 + rd, extend(product) & 0xffffffffffffffff)
            if word >> 26 == 0:
                uc.reg_write(UC_MIPS_REG_LO, extend(product) & 0xffffffffffffffff)
                uc.reg_write(UC_MIPS_REG_HI, extend(product >> 32) & 0xffffffffffffffff)
            return
        if address in (0x105300, 0x105310):
            expected = 0x700024a9 if address == 0x105300 else 0x7d840000
            word = struct.unpack_from("<I", self.elf.data, self.elf.offset(address))[0]
            require(word == expected, "Native packet zero-quad instruction differs")
            # POR a0,zero,zero; SQ a0,0(t4). Unicorn has 64-bit GPRs only.
            if address == 0x105300:
                uc.reg_write(UC_MIPS_REG_A0, 0)
            else:
                uc.mem_write(uc.reg_read(UC_MIPS_REG_0 + 12), bytes(16))
            return
        if address == 0x1010d8:
            self.active = {"pointer": uc.reg_read(UC_MIPS_REG_T0),
                           "x": uc.reg_read(UC_MIPS_REG_A2), "y": uc.reg_read(UC_MIPS_REG_A3)}
            self.events.append(self.active)
            return
        if address == 0x102368:
            self.clears.append({"x": uc.reg_read(UC_MIPS_REG_A2), "y": uc.reg_read(UC_MIPS_REG_A3),
                                "width": uc.reg_read(UC_MIPS_REG_T1), "height": uc.reg_read(UC_MIPS_REG_T2)})
            return
        if address == 0x1025c4:
            length = uc.reg_read(UC_MIPS_REG_0 + 15) - SCRATCH
            require(0x70 <= length <= 0x400, "Native clear packet left scratch allocation")
            packet = bytes(uc.mem_read(SCRATCH, length))
            registers = transfer_registers(packet)
            source = bytes(uc.mem_read(registers["source"], registers["width"] * registers["height"]))
            self.clears[-1].update(packet=packet, packet_bytes=length,
                                   registers=registers, source_blank=not any(source),
                                   source_sha256=hashlib.sha256(source).hexdigest())
            return
        if address == 0x1021d0:
            shape, bitmap = (uc.reg_read(r) for r in (UC_MIPS_REG_A0, UC_MIPS_REG_A1))
            width, height = struct.unpack("<2I", uc.mem_read(shape, 8))
            pixels = bytes(uc.mem_read(bitmap, width * height))
            self.active.update(width=width, height=height, pixels=pixels,
                               bitmap_sha256=hashlib.sha256(pixels).hexdigest())
            return
        if address == 0x105230:
            self.packet_start = uc.reg_read(UC_MIPS_REG_A0)
            self.active["accepted"] = False
            self.active["transfer"] = {"x": uc.reg_read(UC_MIPS_REG_T2),
                                       "y": uc.reg_read(UC_MIPS_REG_T3),
                                       "psm": uc.reg_read(UC_MIPS_REG_T1)}
            return
        if address == 0x1052c0:
            self.active["transfer"]["alignment"] = uc.reg_read(UC_MIPS_REG_V0)
            return
        if address == 0x1052d8:
            self.active["accepted"] = True
            return
        if address in (0x105478, 0x1052b8):
            self.packet_end = uc.reg_read(UC_MIPS_REG_V0)
            self.active["packet_bytes"] = self.packet_end - self.packet_start
            require(0 <= self.active["packet_bytes"] <= 0x400, "Native text packet left scratch allocation")
            packet = bytes(uc.mem_read(self.packet_start, self.active["packet_bytes"]))
            self.active["packet"] = packet
            if self.active["accepted"]:
                self.active["registers"] = transfer_registers(packet)
                source = self.active["registers"]["source"]
                pixels = bytes(uc.mem_read(source, self.active["width"] * self.active["height"]))
                self.active["dma_source_sha256"] = hashlib.sha256(pixels).hexdigest()
                require(pixels == self.active["pixels"], "Native DMA source differs from the text raster")
            return
        if address == 0x1072b8:
            uc.reg_write(UC_MIPS_REG_V0, SCRATCH)
        else:
            uc.reg_write(UC_MIPS_REG_V0, 0)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def command(self, command):
        self.uc.mem_write(STATE, struct.pack("<I", command))
        before = len(self.events)
        self.call(0x113ed0, STATE, count=2000000)
        require(self.uc.reg_read(UC_MIPS_REG_RA) == self.machine.STOP, "Menu RA differs")
        require(struct.unpack("<I", self.uc.mem_read(STATE, 4))[0] == command + 9,
                "Menu continuation differs")
        require(len(self.events) == before + 1, "Menu raster count differs")
        return self.events[-1]

    def menu(self):
        # Run the original callback/group commands. The unrelated group loader
        # at 13768b needs boot resource state, so resume at the real clear cmd73.
        for command, handler in ((0x13767e, 0x112ac8), (0x137683, 0x112b08),
                                 (0x137688, 0x112bc8)):
            self.uc.mem_write(STATE, struct.pack("<I", command))
            self.call(handler, STATE)
        self.uc.mem_write(STATE, struct.pack("<I", 0x137692))
        self.call(0x112660, STATE, count=4000000)
        require(self.uc.reg_read(UC_MIPS_REG_RA) == self.machine.STOP, "Menu dispatcher RA differs")
        require(len(self.events) == 4 and len(self.clears) == 1, "Menu caller did not rasterize four rows")
        return {"continuation": struct.unpack("<I", self.uc.mem_read(STATE, 4))[0],
                "model": struct.unpack("<I", self.uc.mem_read(STATE + 0x10, 4))[0],
                "position": list(struct.unpack("<3f", self.uc.mem_read(STATE + 0x34, 12))),
                "draw_callback": struct.unpack("<I", self.uc.mem_read(STATE + 8, 4))[0],
                "clear": {k: v for k, v in self.clears[0].items() if k != "packet"}}

    def primitive(self, address=TEXT_MODEL):
        vertices = self.rectangle(address)
        at = self.elf.offset(address)
        require(struct.unpack_from("<2H", self.elf.data, at) == (1, 0x68),
                "Menu text model is not one colored rectangle")
        packet = SCRATCH + 0x400
        self.uc.mem_write(packet, bytes(0x100))
        self.uc.mem_write(self.machine.STACK, bytes(0x80))
        self.uc.mem_write(self.machine.STACK + 0x10, struct.pack("<I", packet))
        self.uc.reg_write(UC_MIPS_REG_S4, SCRATCH)
        self.uc.reg_write(UC_MIPS_REG_S7, address + 14)
        self.uc.reg_write(UC_MIPS_REG_SP, self.machine.STACK)
        # Native scalar UV/clamp packing, after the unmodified model rectangle.
        self.fragment(0x106244, 0x1062c8, count=1000)
        value = struct.unpack("<Q", self.uc.mem_read(packet, 8))[0]
        u0, u1 = value >> 4 & 0x3ff, value >> 14 & 0x3ff
        v0, v1 = value >> 24 & 0x3ff, value >> 34 & 0x3ff
        descriptor = struct.unpack("<3H", self.uc.mem_read(TEXTURE_TABLE + 32 * 6, 6))
        self.uc.mem_write(SCRATCH + 8, struct.pack("<4I", descriptor[0], descriptor[1] >> 6, 0, 0))
        self.uc.reg_write(UC_MIPS_REG_S3, packet + 0x20)
        self.uc.reg_write(UC_MIPS_REG_S7, address + 12)
        self.fragment(0x106028, 0x106180, count=1000)
        tex0 = struct.unpack("<Q", self.uc.mem_read(packet + 0x40, 8))[0]
        return {"address": f"{address:08X}", "count": 1, "vertices": vertices,
                "uv_bounds": [u0, v0, u1 + 1, v1 + 1],
                "native_clamp_register": f"{value:016X}",
                "native_tex0_register": f"{tex0:016X}",
                "texture": {"dbp": tex0 & 0x3fff, "dbw": tex0 >> 14 & 63,
                            "psm": tex0 >> 20 & 63, "tw": tex0 >> 26 & 15, "th": tex0 >> 30 & 15},
                "rgba": self.elf.data[at + 18:at + 22].hex()}

    def rectangle(self, address):
        self.uc.mem_write(SCRATCH, bytes(0x200))
        self.uc.reg_write(UC_MIPS_REG_S4, SCRATCH)
        self.uc.reg_write(UC_MIPS_REG_S7, address + 4)
        self.uc.emu_start(0x105c84, 0x105d70, count=1000)
        require(self.uc.reg_read(UC_MIPS_REG_PC) == 0x105d70, "Rectangle decoder did not finish")
        return list(struct.unpack("<16f", self.uc.mem_read(SCRATCH + 0x140, 64)))


def ink_bounds(event):
    width = event["width"]
    occupied = [(i % width, i // width) for i, value in enumerate(event["pixels"]) if value]
    require(occupied, "Menu raster is blank")
    xs, ys = zip(*occupied)
    return [min(xs), min(ys), max(xs) + 1, max(ys) + 1]


def transfer_registers(packet):
    require(len(packet) >= 0x80 and len(packet) % 16 == 0, "Native upload packet is truncated")
    values = {}
    for offset, address in ((0x20, 0x50), (0x30, 0x52), (0x40, 0x51), (0x50, 0x53)):
        value, actual = struct.unpack_from("<2Q", packet, offset)
        require(actual == address, "Native text upload GS register order differs")
        values[address] = value
    buffer, region, position = values[0x50], values[0x52], values[0x51]
    return {"dbp": buffer >> 32 & 0x3fff, "dbw": buffer >> 48 & 63,
            "psm": buffer >> 56 & 63, "x": position >> 32 & 0x7ff,
            "y": position >> 48 & 0x7ff, "width": region & 0xfff,
            "height": region >> 32 & 0xfff, "direction": values[0x53],
            "source": struct.unpack_from("<I", packet, len(packet) - 12)[0],
            "source_qwords": struct.unpack_from("<I", packet, len(packet) - 16)[0] & 0xffff}


def verify(data, font):
    """Rebuild acceptance: native text pixels, packet descriptors and menu UVs.

    The scalar packet encoder runs, but GPU/DMA, resource loading, palette
    upload and VU screen transforms are not executed. It is not a PCSX2 proof.
    """
    from adv_menu_center import _span
    from ui_labels import COMMANDS
    from unicorn.mips_const import UC_MIPS_REG_V0

    machine = MenuMachine(data, font)
    caller = machine.menu()
    primitive = machine.primitive()
    require(caller["model"] == TEXT_MODEL and caller["position"] == [24, 10, 0]
            and caller["continuation"] == 0x1376c6 and caller["draw_callback"] == 0x105580,
            "Native menu model, placement or caller continuation differs")
    require(primitive["uv_bounds"] == [784, 318, 884, 398]
            and primitive["vertices"] == [0, 0, 0, 1, 100, 0, 0, 1, 0, 80, 0, 1, 100, 80, 0, 1],
            "Native menu text primitive differs")
    require(primitive["texture"]["tw"] == primitive["texture"]["th"] == 10,
            "Native menu TEX0 logical size differs")
    clear = machine.clears[0]["registers"]
    require((clear["x"], clear["y"], clear["width"], clear["height"], clear["psm"])
            == (784, 318, 104, 80, 0x13), "Native menu clear packet differs")
    require(machine.clears[0]["source_blank"],
            "Native menu clear source was not zeroed")

    def measure(raw):
        machine.uc.mem_write(machine.machine.SOURCE, raw + b"\0")
        machine.call(0x1017c0, machine.machine.SOURCE)
        return machine.uc.reg_read(UC_MIPS_REG_V0)

    rows = []
    for index, ((command, _), event) in enumerate(zip(COMMANDS, machine.events)):
        require(event["accepted"] and event["packet_bytes"] == 0x90,
                f"Native menu upload rejected at row {index + 1}, x={event['x']}")
        transfer = event["registers"]
        require(all(transfer[key] == event[key] for key in ("x", "y", "width", "height"))
                and transfer["psm"] == 0x13 and transfer["dbw"] == primitive["texture"]["dbw"]
                and transfer["dbp"] == primitive["texture"]["dbp"]
                and transfer["source_qwords"] * 16 == event["width"] * event["height"],
                "Native menu transfer packet does not match its raster or sampling texture")
        require(event["dma_source_sha256"] == event["bitmap_sha256"],
                "Native menu DMA source differs from captured raster")
        block = bytes(machine.uc.mem_read(event["pointer"], 128))
        raw = block[:text_end(block, 0)]
        leading, width = _span(raw, measure)
        center = 2 * (event["x"] + leading) + width
        require(abs(center - 1668) <= 2 and event["x"] % 2 == 0,
                "Native menu visible span is not centered at the nearest valid upload coordinate")
        bounds = ink_bounds(event)
        visible = [bounds[0] + event["x"], bounds[1] + event["y"],
                   bounds[2] + event["x"], bounds[3] + event["y"]]
        require(784 <= visible[0] < visible[2] <= 884
                and 318 + index * 20 <= visible[1] < visible[3] <= 338 + index * 20
                and event["y"] == 318 + index * 20 and event["height"] == 20,
                "Native menu glyph pixels spill out of their sampled row")
        rows.append({k: v for k, v in event.items() if k not in ("pixels", "packet")}
                    | {"command": f"{command:08X}", "ink_bounds_uv": visible,
                       "visible_span_center_x2": center,
                       "raster_ink_pixels": sum(bool(value) for value in event["pixels"])})
    return {"native_rows": rows, "native_caller": caller, "primitive": primitive,
            "context": machine.context, "source_buffers": "captured before reuse",
            "excluded": ["full boot/resource loader", "absolute VRAM cursor observation", "DMA/GPU",
                         "palette upload", "VU screen transforms", "PCSX2 display"]}
