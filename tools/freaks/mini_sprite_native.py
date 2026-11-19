"""Original MINI sprite group loader, inner Ulz decoder and upload arguments."""

import struct

from mini_heap_native import HeapMachine
from mini_sprites import Sprites
from unpack_contents import digest, wwrom_parts
from unpack_disc import require
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import UC_MIPS_REG_0, UC_MIPS_REG_RA, UC_MIPS_REG_PC, UC_MIPS_REG_V0


class SpriteMachine(HeapMachine):
    def __init__(self, elf_data, rom):
        super().__init__(elf_data, rom)
        files = {row["name"].removeprefix("spr/"): raw for row, raw in wwrom_parts(elf_data, rom)
                 if row["name"].startswith("spr/")}
        self.sprites = Sprites(files)
        self.texture = None
        self.upload = None
        self.image_uploads, self.palette_uploads = [], []
        self.copy_calls = 0
        for address in (0x1a8644, 0x18cc90, 0x18ce78, 0x1b02e0, 0x18c978, 0x10ae44):
            self.uc.hook_add(UC_HOOK_CODE, self.graphics_call, begin=address, end=address)

    def graphics_call(self, uc, address, size, user):
        args = [uc.reg_read(UC_MIPS_REG_0 + i) & 0xffffffff for i in range(4, 12)]
        result = 0
        if address == 0x10ae44:
            base = struct.unpack("<I", uc.mem_read(0x224a98, 4))[0]
            pointer = uc.reg_read(UC_MIPS_REG_0 + 17) & 0xffffffff
            require(pointer >= base and (pointer - base) % 16 == 0 and (pointer - base) // 16 < 302,
                    "Native MINI sprite source pointer is not a table row")
            self.texture = self.sprites.textures[(pointer - base) // 16]
            return
        if address == 0x1a8644:
            require(args[2] <= 1 << 20, "Unexpected MINI sprite memcpy size")
            uc.mem_write(args[0], bytes(uc.mem_read(args[1], args[2])))
            result = args[0]
            self.copy_calls += 1
        elif address == 0x18cc90:
            require(self.texture is not None and self.upload is None, "Unexpected MINI GS upload setup")
            descriptor, destination, stride, pixel_format, x, y, width, height = args
            require(descriptor in (0x224ad0, 0x224b30) and x == y == 0, "MINI GS upload origin/descriptor differs")
            self.upload = (descriptor, destination, stride, pixel_format, width, height)
            result = descriptor
        elif address == 0x18ce78:
            require(self.upload is not None and args[0] == self.upload[0], "MINI GS upload data has no setup")
            descriptor, destination, stride, pixel_format, width, height = self.upload
            item = self.texture
            if descriptor == 0x224ad0:
                transfer_height = item.height - int(item.colors == 256 and item.width >= 1024 and item.height >= 512)
                require(pixel_format == (0x13 if item.colors == 256 else 0x14)
                        and (width, height) == (item.width, transfer_height), "MINI texture upload dimensions/PSM differ")
                require(stride == max(1, item.width // 64), "MINI texture upload stride differs")
                raw = bytes(uc.mem_read(args[1], len(item.pixels)))
                require(raw == item.pixels, "Native MINI texture upload pixels differ")
                self.image_uploads.append({"texture": item.index, "width": width, "height": height,
                                           "psm": pixel_format, "destination": destination, "sha256": digest(raw)})
                self.palette_cursor = 0
            else:
                variant = self.palette_cursor
                require(pixel_format == 0 and stride == 1 and (width, height) == ((16, 16) if item.colors == 256 else (8, 2)),
                        "MINI palette upload dimensions/PSM differ")
                require(variant < item.palettes, "Native MINI uploaded too many palette variants")
                at = item.palette_start * 64 + variant * item.colors * 4
                expected = self.sprites.files["clutimg.bin"][at:at + item.colors * 4]
                require(bytes(uc.mem_read(args[1], len(expected))) == expected, "Native MINI palette upload bytes differ")
                self.palette_uploads.append({"texture": item.index, "variant": variant, "destination": destination})
                self.palette_cursor += 1
            self.upload = None
            result = 1
        uc.reg_write(UC_MIPS_REG_V0, result)
        uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def verify_graphics(self, groups=None):
        heap = super().verify()
        heap["resource_allocations"] = [dict(row) for row in heap["resource_allocations"]]
        self.call(0x10a7c0, count=200_000_000)
        requested = self.sprites.groups if groups is None else groups
        group_reports = []
        for kind, group, ids in requested:
            before_images, before_palettes = len(self.image_uploads), len(self.palette_uploads)
            self.call(0x10a8c8)
            require(self.call(0x10ad08, group, kind, count=200_000_000) == 0, "Native MINI sprite group loader failed")
            actual = self.image_uploads[before_images:]
            palettes = self.palette_uploads[before_palettes:]
            require([row["texture"] for row in actual] == list(ids), "Native MINI group texture order differs")
            require([(row["texture"], row["variant"]) for row in palettes]
                    == [(i, v) for i in ids for v in range(self.sprites.textures[i].palettes)],
                    "Native MINI group palette order differs")
            group_reports.append({"kind": kind, "group": group, "textures": list(ids), "palettes": len(palettes)})
        require(self.upload is None, "Unfinished MINI GS upload")
        for row, expected in wwrom_parts(self.elf.data, self.rom):
            descriptor = struct.unpack("<I", self.uc.mem_read(0x224578 + row["index"] * 36, 4))[0]
            rom_pointer = struct.unpack("<I", self.uc.mem_read(0x224570, 4))[0]
            pointer = struct.unpack("<I", self.uc.mem_read(descriptor, 4))[0] if descriptor else rom_pointer + row["offset"]
            require(bytes(self.uc.mem_read(pointer, len(expected))) == expected, "Native MINI resource changed during sprite loading")
        rom_pointer = struct.unpack("<I", self.uc.mem_read(0x224570, 4))[0]
        require(bytes(self.uc.mem_read(rom_pointer, len(self.rom))) == self.rom
                and bytes(self.uc.mem_read(self.arena_start + self.arena_size, 16)) == b"\xa5" * 16,
                "MINI sprite loading changed archive or crossed arena end")
        require(not self.pending and all(row["descriptor"] for row in self.allocations), "MINI sprite allocation failed")
        return {"groups": group_reports, "image_uploads": len(self.image_uploads),
                "palette_uploads": len(self.palette_uploads), "texture_ids": sorted({r["texture"] for r in self.image_uploads}),
                "real_resource_heap": heap, "memcpy_calls_modelled": self.copy_calls,
                "scope": "Original WWROM/resource allocator, group table lookup, temporary allocations, inner Ulz and upload argument preparation. Aligned libc memcpy and GS transfer/sync calls modelled; GPU rasterization and scene reachability not run."}
