"""Selected Japanese TM++ PNG editing and paired GRP/ELF rebuild, lab only."""

import argparse
import io
import json
from pathlib import Path
import re
import struct

from PIL import Image

from adv_format import require
import artwork_native
from audit_artwork import SELECTED as ARTWORK
from elf_probe import Elf
from font_tool import write_generated
from gothic import fixed_width
from lzss import compress, decompress
from menu_native import COMPONENTS, MenuVm, STATE
from tmpp import Tmpp
from unpack_contents import digest

from build_paths import DATA, REPO, WORK
from unpack_disc import file_hash


GROUPS = {
    "LNCH/DAT00/TITLE.GRP": {"selected": [*range(1, 8), *range(9, 18)], "kind": "direct", "limit": 0x100000},
    "LNCH/DAT00/OPTION.GRP": {"selected": [0, 1, 6], "kind": "direct", "limit": 0x100000},
    "ADV/DAT00/TITLE.GRP": {"selected": [*range(10), *range(11, 21), 22, 23], "kind": "direct", "limit": 0x100000},
    "ADV/DAT00/OPTION.GRP": {"selected": [0, 1, 7], "kind": "cache", "limit": 763181},
    "ADV/DAT00/BIGFONT.GRP": {"selected": [*range(9)], "kind": "table", "limit": 0x100000},
    "DTB/DAT00/DATABASE.GRP": {"selected": [10, 11, 12, 13, 22, 23, 24, 25, 46], "kind": "state", "limit": 0xa0000},
    "ADV/DAT00/ADSBG.GRP": {"selected": list(ARTWORK["ADV/DAT00/ADSBG.GRP"]), "kind": "paired", "limit": 0x200000},
    "DTB/DAT00/SNAP.GRP": {"selected": list(ARTWORK["DTB/DAT00/SNAP.GRP"]), "kind": "snapshot", "limit": 0x100000},
    "DTB/DAT00/THUMB.GRP": {"selected": list(ARTWORK["DTB/DAT00/THUMB.GRP"]), "kind": "thumbnails", "limit": 0x600000},
}
CACHE = 0x3b6430
BIGFONT_TABLE = 0x137cee
DB_POOL = 0x124212
DB_STRIPS = 0x129ddc


def image_name(group, index):
    return f"{group.split('/')[0]}_{Path(group).stem}_{index:04}.png"


def read_bound(group, sizes):
    kind = GROUPS[group]["kind"]
    if kind == "paired":
        require(len(sizes) % 2 == 0, "Background pair count differs")
        return max(sum(sizes[i:i + 2]) for i in range(0, len(sizes), 2))
    if kind == "snapshot":
        return max(sizes)
    return sum(sizes)


def catalogue():
    report = json.loads((DATA / "contents-report.json").read_text(encoding="utf-8"))
    inventory = json.loads((DATA / "disc-inventory.json").read_text(encoding="utf-8"))
    hashes = {row["path"]: row["sha256"] for row in inventory["files"]}
    sources, elfs, rows = {}, {}, {}
    for group, settings in GROUPS.items():
        path = REPO / "unpacked" / group
        require(file_hash(path) == hashes[group], f"Original GRP changed: {group}")
        sources[group] = path.read_bytes()
        rows[group] = [r for r in report["members"] if r["source"] == group]
        require([r["index"] for r in rows[group]] == list(range(len(rows[group]))), "GRP member order differs")
        require(all(not r["indexed_container"] for r in rows[group]), "Selected GRP directory format differs")
        maximum = read_bound(group, [r["output"]["size"] + (r["output"]["size"] + 7) // 8
                                    if r["index"] in settings["selected"] else r["stored_size"] for r in rows[group]])
        require(maximum <= settings["limit"], f"Selected PNG worst case exceeds verified buffer: {group}")
    for component, (name, _) in COMPONENTS.items():
        path = REPO / "unpacked" / name
        require(file_hash(path) == hashes[name], f"Original ELF changed: {name}")
        elfs[component] = path.read_bytes()
    return sources, elfs, rows


def virtual(elf, offset):
    for section in elf.sections:
        if section[2] & 2 and section[1] != 8 and section[4] <= offset < section[4] + section[5]:
            return section[3] + offset - section[4]
    raise ValueError("Menu data outside loaded ELF sections")


def find_reads(data, filename):
    elf = Elf(data)
    result = []
    for match in re.finditer(re.escape(filename.lower().encode("ascii") + b"\0"), data):
        address = virtual(elf, match.start())
        pattern = b"\x83...." + re.escape(struct.pack("<I", address)) + b"\0" * 8
        result.extend(virtual(elf, m.start()) for m in re.finditer(pattern, data, re.S))
    require(result, f"No original whole-file menu load for {filename}")
    return sorted(set(result))


def references(group, data, rows):
    elf = Elf(data)
    kind = GROUPS[group]["kind"]
    if group in artwork_native.KINDS:
        return artwork_native.references(group, data, rows)
    result = []
    for row in rows:
        offset, size = row["offset"], row["stored_size"]
        if kind == "table":
            field = elf.offset(BIGFONT_TABLE + row["index"] * 16)
            require(data[field:field + 8] == struct.pack("<II", offset, size), "BIGFONT table changed")
            result.append({"index": row["index"], "source_field": field, "size_field": field + 4,
                           "source_width": 4, "base": 0, "command": None})
            continue
        if kind == "state":
            if row["index"] >= 77:
                field = elf.offset(DB_STRIPS + (row["index"] - 77) * 8)
                require(data[field:field + 8] == struct.pack("<II", offset, size), "DATABASE strip table changed")
                result.append({"index": row["index"], "source_field": field, "size_field": field + 4,
                               "source_width": 4, "base": 0, "command": None, "tail": True})
                continue
            prefix = b"\x0b\xa0" + struct.pack("<I", DB_POOL) + b"\x16\x12\xa0\x12\xa0"
            pattern = prefix + struct.pack("<I", offset) + b"\x89\0\0\0\x87\xa0\0\0\x81\x12" + struct.pack("<I", size)
            matches = [m.start() for m in re.finditer(re.escape(pattern), data)]
            require(len(matches) == 1, f"DATABASE source command is not unique: {row['index']}")
            start = matches[0]
            result.append({"index": row["index"], "source_field": start + 11, "size_field": start + 25,
                           "source_width": 4, "base": 0, "command": virtual(elf, start), "decode": virtual(elf, start + 15)})
            continue
        base = CACHE if kind == "cache" else 0
        source = struct.pack("<I", base + offset) if kind == "cache" else offset.to_bytes(3, "little") + b"\x86"
        pattern = b"\x89\0\0\0\x87" + source + b"\x12" + struct.pack("<I", size)
        matches = [m.start() for m in re.finditer(re.escape(pattern), data)]
        require(len(matches) == 1, f"Direct GRP source command is not unique: {group}/{row['index']}")
        start = matches[0]
        result.append({"index": row["index"], "source_field": start + 5, "size_field": start + 10,
                       "source_width": 4 if kind == "cache" else 3, "base": base, "command": virtual(elf, start)})
    return result


def reference_sites(ref):
    if ref["source_field"] is None:
        return []
    return [(ref["source_field"], ref["size_field"]),
            *((field, field + 4) for field in ref.get("aliases", []))]


def native_group(group, elf_data, packed, layout, raw_images, refs, *, decoded_elf=None,
                 database_measure=fixed_width):
    if group in artwork_native.KINDS:
        return artwork_native.verify(group, elf_data, packed, layout, raw_images, measure=database_measure)
    component = group.split("/")[0]
    original_elf = elf_data if decoded_elf is None else decoded_elf
    kind = GROUPS[group]["kind"]
    filename = Path(group).name.lower()
    files = {filename: packed}
    if kind == "cache":
        witness = REPO / "unpacked/ADV/DAT00/ENDING.GRP"
        inventory = json.loads((DATA / "disc-inventory.json").read_text(encoding="utf-8"))
        expected = next(row for row in inventory["files"] if row["path"] == "ADV/DAT00/ENDING.GRP")
        require(file_hash(witness) == expected["sha256"] and witness.stat().st_size == GROUPS[group]["limit"],
                "Original ADV cache-capacity witness differs")
        files["ending.grp"] = witness
    vm = MenuVm(elf_data, component, files)
    read_roots = []
    if kind == "cache":
        for address in find_reads(original_elf, "ending.grp"):
            require(vm.step(address) == address + 17, "Original ending cache load differs")
            event = vm.events[-1]
            require(event["destination"] == CACHE and event["size"] == GROUPS[group]["limit"],
                    "Original large cache allocation could not be demonstrated")
    if kind == "state":
        pc = 0x12762f
        for _ in range(3):
            pc = vm.step(pc)
        require(pc == 0x12764d, "DATABASE arena allocation layout differs")
        start = struct.unpack("<I", vm.uc.mem_read(DB_POOL, 4))[0]
        next_pool = struct.unpack("<I", vm.uc.mem_read(DB_POOL + 4, 4))[0]
        require(next_pool - start == 0xa0000 and len(packed) <= next_pool - start, "DATABASE pool overflow")
    if kind != "table":
        read_roots = find_reads(original_elf, filename)
        for address in read_roots:
            require(vm.step(address) == address + 17, "Original menu file-load length differs")
            read = vm.events[-1]
            require(read["kind"] == "read" and read["offset"] == 0 and read["requested_size"] == 0
                    and read["size"] == len(packed), "Menu does not read the rebuilt whole file")
            if kind == "cache":
                require(read["destination"] == CACHE, "ADV option cache destination differs")
        first = refs[0]["command"]
        require(vm.uc.mem_read(first - 2, 1)[0] == 0x4f and vm.step(first - 2) == first,
                "Original menu texture-slot setup differs")
    records = []
    previous_end = None
    for row, ref in zip(layout, refs):
        index = row["index"]
        expected = raw_images[index]
        vm.expected_size = len(expected)
        before = len(vm.events)
        if kind == "table":
            vm.state_word(0xa0, index)
            pc = 0x137c98
            for _ in range(8):
                pc = vm.step(pc)
            require(pc == 0x137cca, "BIGFONT table reader length differs")
            require(struct.unpack("<II", vm.uc.mem_read(STATE + 0xa4, 8)) == (row["offset"], row["stored_size"]),
                    "BIGFONT native table fields differ")
            require(vm.step(0x137c28) == 0x137c35, "BIGFONT partial file-read command differs")
            require(vm.step(0x137ccb) == 0x137cd6, "BIGFONT variable-size decompression differs")
        elif ref.get("tail"):
            vm.state_word(0xa4, (index - 77) << 8)
            pc = 0x129d6b
            for _ in range(6):
                pc = vm.step(pc)
            require(pc == 0x129d91, "DATABASE strip table reader length differs")
            require(vm.step(pc) == 0x129d9c, "DATABASE strip decompression differs")
        else:
            pc = ref["command"]
            if kind in ("direct", "cache") and previous_end is not None:
                cursor = previous_end
                while cursor < pc:
                    require(vm.uc.mem_read(cursor, 1)[0] == 0x4f and cursor + 2 <= pc,
                            "Unaudited command between direct graphics loads")
                    require(vm.step(cursor) == cursor + 2, "Menu texture-slot change length differs")
                    cursor += 2
                require(cursor == pc, "Direct graphics command order differs")
            if kind == "state":
                if previous_end != pc:
                    require(vm.uc.mem_read(pc - 2, 1)[0] == 0x4f and vm.step(pc - 2) == pc,
                            "DATABASE texture block setup differs")
                require(vm.step(pc) == pc + 6 and vm.step(pc + 6) == pc + 15, "DATABASE source expression differs")
                pc += 15
            upload = vm.step(pc)
            require(upload == pc + 14, "Menu decompression command length differs")
            require(vm.step(upload) == upload + 13, "Menu texture upload command length differs")
            previous_end = upload + 13
        events = vm.events[before:]
        decode = next(e for e in events if e["kind"] == "decode")
        stored = packed[row["offset"]:row["offset"] + row["stored_size"]]
        require(decode["size"] == len(stored) and decode["stored_sha256"] == digest(stored),
                "Native decompressor did not read the relocated GRP member")
        actual = bytes(vm.uc.mem_read(vm.work, len(expected)))
        require(actual == expected, "Native rebuilt TM++ output differs")
        if kind != "table" and not ref.get("tail"):
            upload = next(e for e in events if e["kind"] == "upload")
            require(upload["sha256"] == digest(expected), "Native texture upload differs")
        else:
            upload = None
        records.append({"index": index, "offset": row["offset"], "stored_size": row["stored_size"],
                        "decoded_size": len(expected), "decoded_sha256": digest(expected),
                        "slot": upload["slot"] if upload else None,
                        "palette_bank": upload["palette_bank"] if upload else None})
        if kind == "state" and index == 77:
            pc = 0x129d21
            for _ in range(4):
                pc = vm.step(pc)
            require(pc == 0x129d39 and vm.step(pc) == 0x129d44,
                    "DATABASE alternate first-strip path differs")
            require(bytes(vm.uc.mem_read(vm.work, len(expected))) == expected,
                    "DATABASE alternate first-strip output differs")
    return {"group": group, "file_reads": read_roots, "members": records, "native": "pass"}


def plan(edits, *, verify_native=True, elf_overrides=None, progress=None, database_measure=fixed_width):
    sources, elfs, groups = catalogue()
    allowed = {(g, i) for g, settings in GROUPS.items() for i in settings["selected"]}
    require(set(edits) <= allowed, "PNG edit is not in the Japanese graphics catalogue")
    outputs = {}
    overrides = elf_overrides or {}
    require(set(overrides) <= set(elfs), "Unknown graphics ELF override")
    require(all(len(data) == len(elfs[owner]) for owner, data in overrides.items()), "ELF override size differs")
    elf_outputs = {owner: bytearray(overrides.get(owner, data)) for owner, data in elfs.items()}
    written = {owner: set() for owner in elfs}
    reports = []
    verification = []
    for number, (group, rows) in enumerate(groups.items(), 1):
        if progress is not None:
            progress(f"[GRAPHICS {number}/{len(groups)}] Repack {group} ({len(rows)} textures)")
        owner = group.split("/")[0]
        refs = references(group, elfs[owner], rows)
        packed = bytearray()
        layout, raw_images, changes = [], {}, []
        for row in rows:
            index = row["index"]
            original = sources[group][row["offset"]:row["offset"] + row["stored_size"]]
            raw = ((REPO / row["output"]["path"]).read_bytes() if group in artwork_native.KINDS
                   else decompress(original))
            require(digest(raw) == row["output"]["sha256"], "Original TM++ decoded hash differs")
            stored = original
            if (group, index) in edits:
                replacement, quality = Tmpp(raw).replace(edits[group, index], preserve_palette=True)
                require(replacement[:1152] == raw[:1152], "Shared palette/header changed")
                if replacement != raw:
                    stored = compress(replacement)
                    require(decompress(stored) == replacement, "PNG compression roundtrip differs")
                    raw = replacement
                    changes.append({"index": index, "quality": quality, "old_size": row["stored_size"], "new_size": len(stored)})
            raw_images[index] = raw
            layout.append({"index": index, "offset": len(packed), "stored_size": len(stored)})
            packed.extend(stored)
        require(read_bound(group, [row["stored_size"] for row in layout]) <= GROUPS[group]["limit"],
                f"GRP source buffer exceeded: {group}")
        patched = elf_outputs[owner]
        if changes:
            for row, ref in zip(layout, refs):
                offset = row["offset"] + ref["base"]
                width = ref["source_width"]
                require(offset < 1 << (width * 8), "Relocated menu pointer overflow")
                for source_field, size_field in reference_sites(ref):
                    fields = set(range(source_field, source_field + width)) | set(range(size_field, size_field + 4))
                    require(not fields & written[owner], "Graphics references overlap; no output written")
                    require(all(patched[i] == elfs[owner][i] for i in fields), "Text/graphics ELF fields overlap; no output written")
                    written[owner].update(fields)
                    patched[source_field:source_field + width] = offset.to_bytes(width, "little")
                    struct.pack_into("<I", patched, size_field, row["stored_size"])
            outputs[group] = bytes(packed)
        reports.append({"group": group, "changed": changes, "old_size": len(sources[group]),
                        "new_size": len(packed), "native_check": None})
        verification.append((group, bytes(packed), layout, raw_images, refs))
    if verify_native:
        for number, (record, (group, packed, layout, raw_images, refs)) in enumerate(zip(reports, verification), 1):
            if progress is not None:
                progress(f"[GRAPHICS CHECK {number}/{len(groups)}] {group} ({len(layout)} textures)")
            owner = group.split("/")[0]
            record["native_check"] = native_group(group, bytes(elf_outputs[owner]), packed, layout, raw_images, refs,
                                                   decoded_elf=elfs[owner], database_measure=database_measure)
    for owner, patched in elf_outputs.items():
        require(len(patched) == len(elfs[owner]), "Graphics rebuild changed ELF size")
        if bytes(patched) != elfs[owner]:
            outputs[COMPONENTS[owner][0]] = bytes(patched)
    return outputs, reports


def extract(directory=None):
    directory = WORK / "graphics" if directory is None else Path(directory)
    sources, _, groups = catalogue()
    count = 0
    for group, settings in GROUPS.items():
        for index in settings["selected"]:
            row = groups[group][index]
            raw = decompress(sources[group][row["offset"]:row["offset"] + row["stored_size"]])
            image = Tmpp(raw).image()
            target = directory / image_name(group, index)
            if target.exists():
                with Image.open(target) as existing:
                    require(existing.size == image.size and existing.convert("RGBA").tobytes() == image.tobytes(),
                            f"Existing PNG preserved; not overwritten: {target}")
            else:
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                write_generated(target, buffer.getvalue())
            count += 1
    print(f"[PASS] {count} Japanese graphics PNGs; existing edits preserved")
    import mini_graphics
    mini_graphics.extract(directory)
    import exp_graphics
    exp_graphics.extract(directory)
    import save_assets
    save_assets.extract(directory)


def load_edits(directory=None):
    directory = WORK / "graphics" if directory is None else Path(directory)
    edits = {}
    expected_edits = {Path(image_name(group, index)).stem.casefold() + "_kor.png"
                      for group, settings in GROUPS.items() for index in settings["selected"]}
    import mini_graphics
    expected_edits.update(Path(mini_graphics.image_name(i)).stem.casefold() + "_kor.png"
                          for i in mini_graphics.catalogue()[1])
    import save_assets
    expected_edits.add(Path(save_assets.IMAGE).stem.casefold() + "_kor.png")
    for path in directory.glob("*"):
        if path.is_file() and path.name.casefold().endswith("_kor.png"):
            require(path.name.casefold() in expected_edits, f"Unknown edited PNG; not silently ignored: {path.name}")
    for group, settings in GROUPS.items():
        for index in settings["selected"]:
            base = directory / image_name(group, index)
            path = base.with_name(base.stem + "_KOR.png")
            if path.exists():
                with Image.open(path) as image:
                    require(image.format == "PNG", "Edited image must be PNG")
                    edits[group, index] = image.convert("RGBA")
    return edits


def previews(outputs, report):
    result = {}
    for group in report:
        for change in group["changed"]:
            index = change["index"]
            row = group["native_check"]["members"][index]
            data = outputs[group["group"]][row["offset"]:row["offset"] + row["stored_size"]]
            image = Tmpp(decompress(data)).image()
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            result[WORK / "evidence/graphics-rebuilt" / image_name(group["group"], index)] = buffer.getvalue()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("extract", "verify", "rebuild"))
    parser.add_argument("--font", type=Path)
    args = parser.parse_args()
    if args.mode == "extract":
        extract()
        return
    from rebuild_pipeline import execute
    execute(args.mode, args.font)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"[ERROR] {error}")
