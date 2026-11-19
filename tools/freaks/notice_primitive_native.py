"""LNCH frame primitive decoder and GS UV emitter, with explicit VU limits."""

import struct

from adv_format import require
from verify_codecs import Native
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import (UC_MIPS_REG_0, UC_MIPS_REG_A0, UC_MIPS_REG_A1,
                               UC_MIPS_REG_A2, UC_MIPS_REG_GP, UC_MIPS_REG_SP,
                               UC_MIPS_REG_RA, UC_MIPS_REG_PC, UC_MIPS_REG_V0)


DRAW = 0x1401000
PACKET = DRAW + 0x1000


class NoticePrimitiveMachine:
    """Run 105AA8 through the actual rectangular/explicit-UV packet branches.

    VU object-to-screen transforms are seeded as identity (local XY), and the
    VU float-to-fixed conversion is modeled. Native coordinate/UV decoding,
    branch selection, UV/clamp packet writes and primitive continuation run.
    DMA and GS rasterization are not executed.
    """

    def __init__(self, data):
        self.native = Native(data)
        self.uc, self.elf = self.native.uc, self.native.elf
        self.vertices = []
        self.transform_calls = 0
        self.packet_calls = 0
        # Unicorn cannot decode R5900 LQ/SQ in a branch delay slot. In this
        # emulator only, NOP those instructions and perform the audited scalar
        # store/load in their code hooks without changing delayed branches.
        for address in (0x105e34, 0x1061c8, 0x1061d0, 0x1066dc, 0x1066f8):
            self.uc.mem_write(address, bytes(4))
        for address in (0x107190, 0x1071b8, 0x1071f8, 0x1072b8, 0x1072d8,
                        0x103300, 0x1054e8, 0x12cbe0, 0x12cc18, 0x105e28,
                        0x105e34, 0x1061c8, 0x1061d0, 0x1066dc, 0x1066f8):
            self.uc.hook_add(UC_HOOK_CODE, self.capture, begin=address, end=address)

    def capture(self, uc, address, size, _):
        read = uc.reg_read
        if address == 0x105e28:
            # R5900 POR a2, zero, zero.
            require(self.elf.data[self.elf.offset(address):self.elf.offset(address) + 4]
                    == bytes.fromhex("a9340070"), "Primitive MMI zero differs")
            uc.reg_write(UC_MIPS_REG_A2, 0)
            uc.reg_write(UC_MIPS_REG_PC, address + 4)
            return
        if address in (0x105e34, 0x1061c8, 0x1061d0, 0x1066dc, 0x1066f8):
            word = struct.unpack_from("<I", self.elf.data, self.elf.offset(address))[0]
            op, rs, rt = word >> 26, word >> 21 & 31, word >> 16 & 31
            require(op in (0x1e, 0x1f), "Primitive audited LQ/SQ differs")
            offset = (word & 0x7fff) - (word & 0x8000)
            at = read(UC_MIPS_REG_0 + rs) + offset
            if op == 0x1f:
                uc.mem_write(at, struct.pack("<2Q", read(UC_MIPS_REG_0 + rt), 0))
            else:
                low, high = struct.unpack("<2Q", uc.mem_read(at, 16))
                require(high == 0, "Primitive scalar LQ has nonzero high half")
                uc.reg_write(UC_MIPS_REG_0 + rt, low)
            return
        if address == 0x1072b8:
            self.packet_calls += 1
            uc.reg_write(UC_MIPS_REG_V0, PACKET)
        elif address == 0x103300:
            base = read(UC_MIPS_REG_A0)
            count = struct.unpack("<I", uc.mem_read(base + 0x134, 4))[0]
            raw = bytes(uc.mem_read(base + 0x140, count * 16))
            self.vertices = [list(struct.unpack_from("<4f", raw, i * 16)[:2]) for i in range(count)]
            uc.mem_write(base + 0x1c0, raw)
            self.transform_calls += 1
        elif address == 0x12cc18:
            values = struct.unpack("<4f", uc.mem_read(read(UC_MIPS_REG_A1), 16))
            uc.mem_write(read(UC_MIPS_REG_A0), struct.pack("<4i", *(int(x * 16) for x in values)))
        elif address == 0x12cbe0:
            uc.mem_write(read(UC_MIPS_REG_A0), bytes(uc.mem_read(read(UC_MIPS_REG_A1), 16)))
        else:
            uc.reg_write(UC_MIPS_REG_V0, 0)
        uc.reg_write(UC_MIPS_REG_PC, read(UC_MIPS_REG_RA))

    def run(self, model):
        uc, n = self.uc, self.native
        uc.mem_write(DRAW, bytes(0x2000))
        uc.mem_write(DRAW, struct.pack("<I", DRAW))
        uc.mem_write(DRAW + 0x40, struct.pack("<I", 0x103300))
        for register, value in ((UC_MIPS_REG_GP, self.elf.gp),
                                (UC_MIPS_REG_SP, n.STACK), (UC_MIPS_REG_RA, n.STOP),
                                (UC_MIPS_REG_A0, DRAW), (UC_MIPS_REG_A1, model)):
            uc.reg_write(register, value)
        uc.emu_start(0x105aa8, n.STOP, count=50000)
        require(uc.reg_read(UC_MIPS_REG_PC) == n.STOP, "Notice primitive did not return")
        require(self.transform_calls == self.packet_calls == 1, "Notice primitive count differs")
        flag = struct.unpack("<H", uc.mem_read(model + 2, 2))[0]
        count = 4
        require(flag in (0x60, 0x65), "Unaudited notice primitive flag")
        # The native packet has one colour qword followed by UV/XYZ pairs.
        vertices = []
        for i in range(count):
            uv = struct.unpack("<2I", uc.mem_read(PACKET + 0x60 + i * 0x20, 8))
            xy = struct.unpack("<2i", uc.mem_read(PACKET + 0x70 + i * 0x20, 8))
            vertices.append({"uv": [x / 16 for x in uv], "xy": [x / 16 for x in xy]})
        clamp = struct.unpack("<Q", uc.mem_read(PACKET + 0x30, 8))[0]
        bounds = [(clamp >> shift) & 1023 for shift in (4, 14, 24, 34)]
        return {"flag": f"{flag:04X}", "decoded_xy": self.vertices,
                "packet_vertices": vertices, "clamp_uv_minmax": bounds,
                "clamp_modes": [clamp & 3, (clamp >> 2) & 3],
                "packet_header_hex": bytes(uc.mem_read(PACKET, 0x60)).hex(),
                "continuation": f"{uc.reg_read(UC_MIPS_REG_V0):08X}",
                "uv_native": True, "xy_conversion": "identity VU transform; VFTI4 modeled",
                "excluded": "DMA, GS rasterization, VU culling and animated object/camera transforms"}


def verify_sampling(proof, texture, origin=(488, 140)):
    points = [p["uv"] for p in proof["packet_vertices"]]
    low = [min(p[i] for p in points) - origin[i] for i in (0, 1)]
    high = [max(p[i] for p in points) - origin[i] for i in (0, 1)]
    x0, x1, y0, y1 = proof["clamp_uv_minmax"]
    clamp = [x0 - origin[0], x1 - origin[0], y0 - origin[1], y1 - origin[1]]
    require(proof["clamp_modes"] == [2, 2]
            and 0 <= clamp[0] <= clamp[1] < texture.width and 0 <= clamp[2] <= clamp[3] < texture.height,
            f"Notice UV samples outside SAVELOAD image 7: {low}..{high}, {texture.width}x{texture.height}")
    return {"image": 7, "texture_size": [texture.width, texture.height],
            "uv_local_min": low, "uv_local_max": high, "clamp_local_minmax": clamp}
