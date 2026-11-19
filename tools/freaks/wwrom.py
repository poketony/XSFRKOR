"""Paired MINI WWROM/ELF repack with original native resource-heap preflight."""

import json
import struct

from elf_probe import Elf
from ulz import compress, decompress
import mini_validation_cache
from unpack_contents import digest, wwrom_parts

from build_paths import DATA, REPO
from unpack_disc import require


TABLE = 0x1bfb58
RESIZABLE = {"dic/dicdata.dix", "dic/dicdata.wix", "dic/dicdata.wdt", "spr/textureimg.bin", "stage/1p.stg"}


def originals():
    inventory = json.loads((DATA / "disc-inventory.json").read_text(encoding="utf-8"))
    hashes = {row["path"]: row["sha256"] for row in inventory["files"]}
    result = []
    for name in ("MINI/MINI.ELF", "MINI/WWROM.DAT"):
        data = (REPO / "unpacked" / name).read_bytes()
        require(digest(data) == hashes[name], f"Original MINI source changed: {name}")
        result.append(data)
    return tuple(result)


def rebuild(elf_data, rom, edits):
    require(isinstance(edits, dict), "WWROM edits must be a name-to-bytes mapping")
    rows = list(wwrom_parts(elf_data, rom))
    known = {row["name"] for row, _ in rows}
    require(set(edits) <= known, "Unknown WWROM edit name")
    changed = {}
    for row, decoded in rows:
        name = row["name"]
        if name not in edits:
            continue
        value = edits[name]
        require(isinstance(value, bytes), f"WWROM edit must contain bytes: {name}")
        require(len(value) == len(decoded) or name in RESIZABLE and 0 < len(value) < 1 << 24,
                f"WWROM edit must preserve decoded size: {name}")
        if value != decoded:
            changed[name] = value
    if not changed:
        return elf_data, rom, {"changed": 0, "members": 22, "elf_size_unchanged": True,
                               "rom_bytes": len(rom), "positions": []}

    elf = Elf(elf_data)
    output_elf = bytearray(elf_data)
    output = bytearray()
    positions = []
    for row, decoded in rows:
        name = row["name"]
        stored = rom[row["offset"]:row["offset"] + row["stored_size"]]
        if name in changed:
            if row["codec"] == "Ulz":
                stored = compress(changed[name], row["streams"]["displacement_bits"])
                require(decompress(stored)[0] == changed[name], f"Ulz self-check failed: {name}")
            else:
                stored = changed[name]
        offset = len(output)
        output.extend(stored)
        output.extend(b"\0" * (-len(output) % 2048))
        require(len(output) < 32 << 20, "Repacked WWROM alone exceeds PS2 RAM")
        field = elf.offset(TABLE) + row["index"] * 20
        struct.pack_into("<3I", output_elf, field, offset, len(stored), offset // 2048)
        new_size = len(changed.get(name, decoded))
        if row["codec"] == "Ulz":
            struct.pack_into("<I", output_elf, field + 12, new_size)
        positions.append({"index": row["index"], "name": name, "changed": name in changed,
                          "old_offset": row["offset"], "offset": offset,
                          "stored_size": len(stored), "decoded_size": new_size})
    output_elf, output = bytes(output_elf), bytes(output)
    verified = list(wwrom_parts(output_elf, output))
    require(len(verified) == len(rows), "WWROM member count changed")
    for (before, decoded), (after, actual) in zip(rows, verified):
        require(before["name"] == after["name"]
                and actual == changed.get(before["name"], decoded), "WWROM rebuilt member differs")
        if before["name"] not in changed:
            require(before["stored_sha256"] == after["stored_sha256"], "Unedited WWROM stream changed")
    from mini_heap_native import HeapMachine
    heap = mini_validation_cache.checked("resource-heap", (output_elf, output),
                                         lambda: HeapMachine(output_elf, output).verify())
    return output_elf, output, {"changed": len(changed), "members": len(rows), "native_heap": heap,
                               "elf_size_unchanged": len(output_elf) == len(elf_data),
                               "rom_bytes": len(output), "positions": positions}
