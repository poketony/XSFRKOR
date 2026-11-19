"""Read-only audit of Japanese artwork outside the existing menu pipeline."""

import json
from pathlib import Path
import struct

from adv_format import require
from database_text import read_entries
from elf_probe import Elf
from tmpp import Tmpp
from unpack_contents import digest

from build_paths import DATA, REPO, WORK
from unpack_disc import file_hash
from verify_codecs import Native
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2,
                               UC_MIPS_REG_A3, UC_MIPS_REG_GP, UC_MIPS_REG_PC,
                               UC_MIPS_REG_RA, UC_MIPS_REG_S1, UC_MIPS_REG_SP,
                               UC_MIPS_REG_V0)


SELECTED = {"ADV/DAT00/ADSBG.GRP": tuple(range(100, 120)),
            "DTB/DAT00/SNAP.GRP": (25, 27, 29),
            "DTB/DAT00/THUMB.GRP": (82,)}
NO_ADDITIONAL_JAPANESE = ("ADV/DAT00/ADCHA.GRP", "ADV/DAT00/ADMYC.GRP",
                          "ADV/DAT00/CGDISP.GRP", "ADV/DAT00/CHRSEL.GRP",
                          "ADV/DAT00/COMMON.GRP", "ADV/DAT00/ENDING.GRP",
                          "ADV/DAT00/MAIN.GRP", "ADV/DAT00/MVDUEU.GRP",
                          "ADV/DAT00/MVDUFO.GRP", "ADV/DAT00/MVEUDU.GRP",
                          "ADV/DAT00/MVFODU.GRP", "ADV/DAT00/SAVELOAD.GRP",
                          "LNCH/DAT00/LOGO.GRP", "LNCH/DAT00/SAVELOAD.GRP")


def originals():
    inventory = json.loads((DATA / "disc-inventory.json").read_text(encoding="utf-8"))
    hashes = {row["path"]: row["sha256"] for row in inventory["files"]}
    report = json.loads((DATA / "contents-report.json").read_text(encoding="utf-8"))
    sources = {}
    for name in (*SELECTED, "ADV/ADV.ELF", "DTB/DTB.ELF"):
        path = REPO / "unpacked" / name
        require(file_hash(path) == hashes[name], f"Original artwork input changed: {name}")
        sources[name] = path.read_bytes()
    rows = {name: [row for row in report["members"] if row["source"] == name] for name in SELECTED}
    require({name: len(items) for name, items in rows.items()} ==
            {"ADV/DAT00/ADSBG.GRP": 182, "DTB/DAT00/SNAP.GRP": 40, "DTB/DAT00/THUMB.GRP": 118},
            "Artwork member counts differ")
    for name, items in rows.items():
        require([row["index"] for row in items] == list(range(len(items))), "Artwork order differs")
        for row in items:
            stored = sources[name][row["offset"]:row["offset"] + row["stored_size"]]
            require(digest(stored) == row["stored_sha256"], "Artwork compressed source differs")
    return sources, rows


def reference_tables(sources, rows):
    result = {name: {row["index"]: [] for row in items} for name, items in rows.items()}

    def record(name, address, expected_index=None):
        data = sources[name.split("/")[0] + "/" + name.split("/")[0] + ".ELF"]
        elf = Elf(data)
        pair = struct.unpack_from("<2I", data, elf.offset(address))
        matches = [row["index"] for row in rows[name] if (row["offset"], row["stored_size"]) == pair]
        require(len(matches) == 1 and (expected_index is None or matches[0] == expected_index),
                f"Artwork table differs: {name} at {address:08X}")
        result[name][matches[0]].append(address)

    name = "ADV/DAT00/ADSBG.GRP"
    for index in range(182):
        record(name, 0x1381b0 + index * 8, index)
    for index in range(91):
        record(name, 0x179420 + index * 8, index * 2)

    data = sources["DTB/DTB.ELF"]
    elf = Elf(data)
    entries = read_entries(data)
    snap = sorted({entry.words[7] for entry in entries if entry.words[7]})
    require(snap == list(range(0x16bfb0, 0x16c230, 16)), "SNAP record ownership differs")
    for address in snap:
        record("DTB/DAT00/SNAP.GRP", address)
    thumb = sorted({entry.words[6] for entry in entries if entry.words[6]})
    require(thumb == list(range(0x1755a0, 0x175938, 8)), "THUMB record ownership differs")
    require(elf.gp - 0x7e80 == 0x175470, "Default THUMB GP address differs")
    for address in (0x175470, *thumb):
        record("DTB/DAT00/THUMB.GRP", address)
    require({index for index, fields in result["DTB/DAT00/THUMB.GRP"].items() if not fields} == {16, 73},
            "Unreferenced THUMB member set differs")
    return result


def call(machine, address, *args):
    for register, value in ((UC_MIPS_REG_GP, machine.elf.gp), (UC_MIPS_REG_SP, machine.STACK),
                            (UC_MIPS_REG_RA, machine.STOP)):
        machine.uc.reg_write(register, value)
    for register, value in zip((UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3), args):
        machine.uc.reg_write(register, value)
    machine.uc.emu_start(address, machine.STOP, count=200000)
    require(machine.uc.reg_read(UC_MIPS_REG_PC) == machine.STOP
            and machine.uc.reg_read(UC_MIPS_REG_SP) == machine.STACK, "Artwork getter did not return cleanly")


def native_getters(sources):
    data = sources["DTB/DTB.ELF"]
    dtb = Native(data)
    entries = read_entries(data)
    destination = dtb.OUTPUT
    for entry in entries:
        for field, address in ((6, 0x124120), (7, 0x124168)):
            dtb.uc.mem_write(destination - 16, b"\xa5" * 48)
            call(dtb, address, entry.index, destination, destination + 4, destination + 8)
            pointer = entry.words[field]
            if field == 6:
                pointer = pointer or 0x175470
                expected = data[dtb.elf.offset(pointer):dtb.elf.offset(pointer) + 8] + b"\xa5" * 8
            elif pointer:
                expected = data[dtb.elf.offset(pointer):dtb.elf.offset(pointer) + 12] + b"\xa5" * 4
                require(dtb.uc.reg_read(UC_MIPS_REG_V0) == 1, "Native SNAP presence flag differs")
            else:
                expected = b"\xa5" * 16
                require(dtb.uc.reg_read(UC_MIPS_REG_V0) == 0, "Native absent SNAP flag differs")
            require(bytes(dtb.uc.mem_read(destination, 16)) == expected, "Native artwork getter fields differ")
            require(bytes(dtb.uc.mem_read(destination - 16, 16)) == b"\xa5" * 16
                    and bytes(dtb.uc.mem_read(destination + 16, 16)) == b"\xa5" * 16,
                    "Native artwork getter crossed output boundary")

    adv = Native(sources["ADV/ADV.ELF"])
    reads = []

    def capture(uc, address, size, _):
        name_at = uc.reg_read(UC_MIPS_REG_A0)
        name = bytes(uc.mem_read(name_at, 64)).split(b"\0", 1)[0].decode("ascii")
        require(name.casefold().endswith("adsbg.grp"), "Gallery reader filename differs")
        reads.append(tuple(uc.reg_read(r) for r in (UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3)))
        uc.reg_write(UC_MIPS_REG_PC, adv.STOP)

    adv.uc.hook_add(UC_HOOK_CODE, capture, begin=0x116e40, end=0x116e40)
    for index in range(91):
        adv.uc.reg_write(UC_MIPS_REG_S1, index)
        adv.uc.reg_write(UC_MIPS_REG_V0, adv.SOURCE)
        call(adv, 0x13b81c)
        expected = struct.unpack_from("<2I", sources["ADV/ADV.ELF"], adv.elf.offset(0x179420 + index * 8))
        require(reads[-1] == (*expected, adv.SOURCE), "Native gallery partial-read arguments differ")
    return {"dtb_getter_calls": len(entries) * 2, "adv_gallery_partial_read_arguments": len(reads),
            "scope": "Original DTB getters and ADV gallery table-to-read argument path; file I/O is intercepted"}


def audit():
    sources, rows = originals()
    tables = reference_tables(sources, rows)
    machines = {owner: Native(sources[owner + "/" + owner + ".ELF"]) for owner in ("ADV", "DTB")}
    targets = []
    for name, indices in SELECTED.items():
        for index in indices:
            row = rows[name][index]
            path = REPO / row["output"]["path"]
            require(file_hash(path) == row["output"]["sha256"], "Decoded artwork input changed")
            raw = path.read_bytes()
            texture = Tmpp(raw)
            stored = sources[name][row["offset"]:row["offset"] + row["stored_size"]]
            machine = machines[name.split("/")[0]]
            result = machine.decode(0x102610, stored, raw, "Freaks-LZSS")
            require(result == machine.OUTPUT + len(raw), "Native artwork decode return differs")
            targets.append({"group": name, "index": index, "decoded_path": row["output"]["path"],
                            "width": texture.width, "height": texture.height, "sha256": digest(raw),
                            "source_offset": row["offset"], "stored_size": row["stored_size"],
                            "reference_addresses": tables[name][index], "pipeline": "graphics_pipeline.py",
                            "editable_png": f"graphics/{name.split('/')[0]}_{Path(name).stem}_{index:04}.png"})
    return {"status": "read-only source audit; artwork is connected to the shared graphics pipeline",
            "additional_japanese_images": len(targets), "targets": targets,
            "visually_reviewed_no_additional_japanese": list(NO_ADDITIONAL_JAPANESE),
            "native": native_getters(sources),
            "unreferenced_thumb_members": [16, 73],
            "rebuild_evidence": ["graphics-pipeline-tests.json", "pipeline-tests.json"],
            "not_verified": ["PCSX2 display and full in-game state transitions"],
            "game_files_written": False, "temporary_images_written": False}


def main():
    result = audit()
    path = WORK / "evidence/artwork-audit.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] {len(result['targets'])} additional Japanese images; "
          f"{result['native']['dtb_getter_calls']} DTB getters; "
          f"{result['native']['adv_gallery_partial_read_arguments']} ADV gallery read arguments")
    print("[NO GAME OUTPUT] Rebuild checks: graphics-pipeline-tests.json and pipeline-tests.json")


if __name__ == "__main__":
    main()
