"""Original ADV background and DTB artwork readers for paired GRP rebuilding."""

import struct

from adv_format import require
import audit_artwork as audit
from database_text import read_entries
from elf_probe import Elf
from gothic import fixed_width
from menu_native import MenuVm
from unpack_contents import digest
from verify_codecs import Native
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import UC_MIPS_REG_PC, UC_MIPS_REG_S1, UC_MIPS_REG_V0


KINDS = {"ADV/DAT00/ADSBG.GRP": "paired", "DTB/DAT00/SNAP.GRP": "snapshot",
         "DTB/DAT00/THUMB.GRP": "thumbnails"}


def references(group, data, rows):
    sources, original_rows = audit.originals()
    owner = group.split("/")[0]
    sources[f"{owner}/{owner}.ELF"] = data
    require([(r["index"], r["offset"], r["stored_size"]) for r in rows] ==
            [(r["index"], r["offset"], r["stored_size"]) for r in original_rows[group]],
            "Artwork reference input layout differs")
    table = audit.reference_tables(sources, original_rows)[group]
    elf = Elf(data)
    result = []
    for index, addresses in table.items():
        fields = [elf.offset(address) for address in addresses]
        result.append({"index": index, "source_field": fields[0] if fields else None,
                       "size_field": fields[0] + 4 if fields else None, "aliases": fields[1:],
                       "source_width": 4, "base": 0, "command": None})
    return result


def words(vm, address, count):
    return struct.unpack(f"<{count}I", vm.uc.mem_read(address, count * 4))


def steps(vm, address, end):
    while address < end:
        address = vm.step(address)
    require(address == end, "Artwork command boundary differs")


def guard(vm, address, size):
    vm.uc.mem_write(address - 16, b"\xa5" * 16)
    vm.uc.mem_write(address + size, b"\xa5" * 16)


def guarded(vm, address, size):
    require(bytes(vm.uc.mem_read(address - 16, 16)) == b"\xa5" * 16
            and bytes(vm.uc.mem_read(address + size, 16)) == b"\xa5" * 16,
            "Artwork file read crossed its buffer boundary")


def decoded(vm, address, expected, row, packed):
    vm.expected_size = len(expected)
    before = len(vm.events)
    upload = vm.step(address)
    require(vm.step(upload) == upload + 13, "Artwork texture upload command differs")
    events = vm.events[before:]
    decode = next(event for event in events if event["kind"] == "decode")
    transfer = next(event for event in events if event["kind"] == "upload")
    stored = packed[row["offset"]:row["offset"] + row["stored_size"]]
    require(decode["size"] == len(stored) and decode["stored_sha256"] == digest(stored),
            "Artwork native decoder used the wrong relocated stream")
    require(bytes(vm.uc.mem_read(vm.work, len(expected))) == expected
            and transfer["sha256"] == digest(expected), "Artwork native decoded/uploaded bytes differ")
    return {**row, "decoded_size": len(expected), "decoded_sha256": digest(expected),
            "slot": transfer["slot"], "palette_bank": transfer["palette_bank"]}


def verify(group, data, packed, layout, raw_images, *, measure=fixed_width):
    kind = KINDS[group]
    owner = group.split("/")[0]
    filename = group.rsplit("/", 1)[1].lower()
    vm = MenuVm(data, owner, {filename: packed}, state=0x2000000,
                read_limit=0x600000 if kind == "thumbnails" else 0x180000)
    records = {}
    read_roots = []
    if owner == "DTB":
        # The original initializer reserves 0x10000 paragraphs for file I/O.
        require(struct.unpack_from("<I", data, vm.elf.offset(0x109fa8))[0] == 0x3c020001,
                "DTB initial file-buffer reservation differs")
        steps(vm, 0x12762f, 0x12764d)
        pools = words(vm, 0x124212, 3)
        require(pools[1] - pools[0] == 0xa0000 and pools[2] - pools[1] == 0x200000
                and vm.work - pools[2] == 0x600000, "DTB artwork arena layout differs")
        entries = read_entries(data, measure=measure)
        if kind == "thumbnails":
            capacity = 0x600000
            require(len(packed) <= capacity, "THUMB whole-file buffer overflow")
            guard(vm, pools[2], capacity)
            audit.call(vm.native, 0x1236e0, vm.state)
            require(vm.step(0x129cc5) == 0x129cd6, "THUMB full-read command differs")
            event = vm.events[-1]
            require(event["destination"] == pools[2] and event["offset"] == 0
                    and event["size"] == len(packed) and event["requested_size"] == 0,
                    "THUMB rebuilt file was not loaded into its original arena")
            guarded(vm, pools[2], capacity)
            read_roots.append(0x129cc5)
            for row in layout:
                index = row["index"]
                candidates = []
                for entry in entries:
                    pointer = entry.words[6] or 0x175470
                    offset = struct.unpack_from("<I", data, vm.elf.offset(pointer))[0]
                    if offset == row["offset"]:
                        candidates.append(entry.index)
                if not candidates:
                    require(index in (16, 73), "Unexpected unreferenced THUMB member")
                    continue
                audit.call(vm.native, 0x124120, candidates[0], vm.state + 0xa4, vm.state + 0xa8)
                require(words(vm, vm.state + 0xa4, 2) == (row["offset"], row["stored_size"]),
                        "THUMB getter does not agree with rebuilt layout")
                steps(vm, 0x129c73, 0x129c7f)
                records[index] = decoded(vm, 0x129c7f, raw_images[index], row, packed)
            for row in layout:
                if row["index"] not in records:
                    # These two original images have no DB entry; check their codec only.
                    machine = Native(data)
                    raw = raw_images[row["index"]]
                    stored = packed[row["offset"]:row["offset"] + row["stored_size"]]
                    require(machine.decode(0x102610, stored, raw, "Freaks-LZSS") == machine.OUTPUT + len(raw),
                            "Unreferenced THUMB codec differs")
                    records[row["index"]] = {**row, "decoded_size": len(raw), "decoded_sha256": digest(raw),
                                             "slot": None, "palette_bank": None, "unreferenced": True}
        else:
            capacity = pools[0] - vm.native.SOURCE
            require(capacity == 0x100000, "SNAP file-buffer capacity differs")
            read_roots.append(0x127cf4)
            for row in layout:
                require(row["stored_size"] <= capacity, "SNAP compressed member exceeds file buffer")
                candidates = [entry.index for entry in entries if entry.words[7]
                              and struct.unpack_from("<I", data, vm.elf.offset(entry.words[7]))[0] == row["offset"]]
                require(candidates, "SNAP has no DB entry")
                vm.uc.mem_write(vm.elf.gp - 0x777c, struct.pack("<I", 1))
                vm.uc.mem_write(0x3aa228, struct.pack("<I", candidates[0]))
                audit.call(vm.native, 0x123d30, vm.state)
                require(words(vm, vm.state + 0xa8, 2) == (row["offset"], row["stored_size"]),
                        "SNAP getter does not agree with rebuilt layout")
                guard(vm, vm.native.SOURCE, capacity)
                require(vm.step(0x127cf4) == 0x127d01, "SNAP partial-read command differs")
                event = vm.events[-1]
                require((event["offset"], event["size"], event["destination"]) ==
                        (row["offset"], row["stored_size"], vm.native.SOURCE), "SNAP partial-read arguments differ")
                guarded(vm, vm.native.SOURCE, capacity)
                records[row["index"]] = decoded(vm, 0x129739, raw_images[row["index"]], row, packed)
    else:
        steps(vm, 0x1361b5, 0x1361c9)
        start, next_pool = words(vm, 0x1332ce, 2)
        capacity = next_pool - start
        require(capacity == 0x200000, "ADSBG original pair-buffer allocation differs")
        require(vm.step(0x137dfb) == 0x137e01, "ADSBG source-buffer getter differs")
        read_roots = [0x137e29, 0x137ebc, 0x137f1e, 0x137fb2, 0x13805b]
        for pair in range(91):
            normal, dark = layout[pair * 2:pair * 2 + 2]
            vm.state_word(0xa8, pair * 16)
            steps(vm, 0x13812a, 0x138168)
            expected = (normal["offset"], normal["stored_size"], dark["offset"], dark["stored_size"])
            require(words(vm, vm.state + 0xac, 4) == expected, "ADSBG native pair table differs")
            span = dark["offset"] + dark["stored_size"] - normal["offset"]
            require(0 < span <= capacity and words(vm, vm.state + 0xbc, 1)[0] == span,
                    "ADSBG native pair read exceeds its arena")
            for address in read_roots:
                guard(vm, start, capacity)
                require(vm.step(address) == address + 13, "ADSBG partial-read command differs")
                event = vm.events[-1]
                require((event["offset"], event["size"], event["destination"]) ==
                        (normal["offset"], span, start), "ADSBG native pair read arguments differ")
                guarded(vm, start, capacity)
            steps(vm, 0x138169, 0x138174)
            records[normal["index"]] = decoded(vm, 0x138197, raw_images[normal["index"]], normal, packed)
            steps(vm, 0x138183, 0x138192)
            records[dark["index"]] = decoded(vm, 0x138197, raw_images[dark["index"]], dark, packed)

        def stop_gallery(uc, address, size, _):
            uc.reg_write(UC_MIPS_REG_PC, vm.native.STOP)

        vm.uc.hook_add(UC_HOOK_CODE, stop_gallery, begin=0x13b848, end=0x13b848)
        for index in range(91):
            row = layout[index * 2]
            require(row["stored_size"] <= 0x100000, "ADSBG gallery file buffer overflow")
            guard(vm, vm.native.SOURCE, 0x100000)
            vm.uc.reg_write(UC_MIPS_REG_S1, index)
            vm.uc.reg_write(UC_MIPS_REG_V0, vm.native.SOURCE)
            audit.call(vm.native, 0x13b81c)
            event = vm.events[-1]
            require((event["offset"], event["size"], event["destination"]) ==
                    (row["offset"], row["stored_size"], vm.native.SOURCE), "ADSBG gallery alias differs")
            require(event["sha256"] == digest(packed[row["offset"]:row["offset"] + row["stored_size"]]),
                    "ADSBG gallery read bytes differ")
            guarded(vm, vm.native.SOURCE, 0x100000)
    require(len(records) == len(layout), "Artwork verification omitted a member")
    return {"group": group, "file_reads": read_roots, "members": [records[row["index"]] for row in layout],
            "source_capacity": capacity, "native": "pass",
            "file_read_count": sum(event["kind"] == "read" for event in vm.events),
            "max_file_read": max(event["size"] for event in vm.events if event["kind"] == "read"),
            "scope": "Original allocators, table getters, file-read arguments, decompressor and upload arguments; I/O and GS are modeled"}
