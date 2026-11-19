"""Read-only MINI STG/STF/PBF structure and original stage-loader audit."""

import argparse
from dataclasses import dataclass, replace
import json
import struct

from font_tool import write_generated
from unpack_contents import digest, wwrom_parts

from build_paths import WORK
from unpack_disc import require
import wwrom


@dataclass(frozen=True)
class Board:
    data: bytes
    width: int
    height: int
    cells: tuple
    pieces: tuple
    objects: tuple

    @classmethod
    def parse(cls, data):
        require(len(data) >= 80 and data[:4] == b"PBF\0", "MINI PBF header is truncated or invalid")
        require(struct.unpack_from("<2I", data, 4) == (0x100, len(data)), "MINI PBF version/size differs")
        width, height = data[12:14]
        require(0 < width <= 13 and 0 < height <= 9, "MINI board exceeds runtime cell map")
        cell_count, cell_stride, group_count, group_stride, object_count, object_stride = struct.unpack_from("<6H", data, 32)
        require(cell_count <= 117 and group_count <= 8 and object_count <= 16
                and (cell_stride, group_stride, object_stride) == (8, 8, 8), "MINI PBF counts/strides exceed runtime layout")
        cells_at, groups_at, objects_at, reserved = struct.unpack_from("<4I", data, 64)
        require(cells_at == 16 and groups_at == cells_at + cell_count * 8 and not reserved,
                "MINI PBF cell section differs")
        require(groups_at + 64 + 32 <= len(data) and objects_at + 64 + object_count * 8 == len(data),
                "MINI PBF section end differs")
        cells = tuple(data[64 + cells_at + i * 8:72 + cells_at + i * 8] for i in range(cell_count))
        require(all(not any(row[6:]) and row[3] in (1, 2, 4) and row[5] < object_count
                    and row[1] + row[3] <= width and row[2] + row[3] <= height for row in cells),
                "MINI PBF cell coverage/object index differs")
        directory = struct.unpack_from("<8I", data, 64 + groups_at)
        cursor, pieces = 32, []
        for relative in directory[:group_count]:
            require(relative == cursor and 64 + groups_at + relative + 4 <= len(data), "MINI PBF piece-group offset differs")
            count = struct.unpack_from("<I", data, 64 + groups_at + relative)[0]
            require(count <= 117 and 64 + groups_at + relative + 4 + count * 8 <= 64 + objects_at,
                    "MINI PBF piece group exceeds section")
            rows = tuple(data[68 + groups_at + relative + i * 8:76 + groups_at + relative + i * 8] for i in range(count))
            require(all(row[0] < 72 and row[1] < width and row[2] < height and not any(row[3:]) for row in rows),
                    "MINI PBF preplaced piece differs")
            pieces.append(rows)
            cursor += 4 + count * 8
        require(all(x == 0xffffffff for x in directory[group_count:]) and groups_at + cursor == objects_at,
                "MINI PBF unused groups or piece-section end differ")
        objects = tuple(data[64 + objects_at + i * 8:72 + objects_at + i * 8] for i in range(object_count))
        require(all(not any(row[5:]) for row in objects), "MINI PBF object padding differs")
        return cls(data, width, height, cells, tuple(pieces), objects)


@dataclass(frozen=True)
class Stage:
    index: int
    offset: int
    data: bytes
    board: Board
    inventory_offset: int
    inventory: bytes
    goals: tuple

    def text(self, offset, size):
        raw = self.data[offset:offset + size]
        require(b"\0" in raw, "MINI stage text has no terminator")
        return raw.split(b"\0", 1)[0].decode("euc_jp")

    def report(self):
        return {"index": self.index, "offset": self.offset, "bytes": len(self.data),
                "description": self.text(0x1c, 46), "name": self.text(0x4a, 18),
                "hints": [self.text(0xa0, 64), self.text(0xe0, 64)],
                "variant_values": list(struct.unpack_from("<4H", self.data, 0x5c)),
                "goals": [{"kind": g[0], "argument": g[1], "parameter": g[2], "payload": list(g[4:])}
                          for g in self.goals],
                "board": {"width": self.board.width, "height": self.board.height,
                          "cell_records": len(self.board.cells), "object_records": len(self.board.objects),
                          "preplaced_groups": [[list(row[:3]) for row in group] for group in self.board.pieces]},
                "inventory_header": list(self.data[self.inventory_offset:self.inventory_offset + 2]),
                "inventory": list(self.inventory)}


def remove_preplaced(stage, locations):
    board = stage.board
    existing = {(r[1], r[2]) for group in board.pieces for r in group}
    require(locations and locations <= existing, "Only existing preplaced pieces can be removed")
    groups_at, objects_at = struct.unpack_from("<2I", board.data, 68)
    prefix = bytearray(board.data[:64 + groups_at])
    groups, directory = bytearray(), []
    for group in board.pieces:
        keep = [row for row in group if (row[1], row[2]) not in locations]
        directory.append(32 + len(groups))
        groups.extend(struct.pack("<I", len(keep)) + b"".join(keep))
    directory.extend([0xffffffff] * (8 - len(directory)))
    struct.pack_into("<I", prefix, 72, groups_at + 32 + len(groups))
    pbf = prefix + struct.pack("<8I", *directory) + groups + board.data[64 + objects_at:]
    struct.pack_into("<I", pbf, 8, len(pbf))
    checked = Board.parse(bytes(pbf))
    require((checked.cells, checked.objects) == (board.cells, board.objects)
            and sum(map(len, checked.pieces)) == sum(map(len, board.pieces)) - len(locations),
            "Removed pieces changed the board layout")
    inventory_at = 304 + len(pbf)
    raw = bytearray(stage.data[:304]) + pbf + stage.data[stage.inventory_offset:]
    struct.pack_into("<I", raw, 8, len(raw))
    struct.pack_into("<2I", raw, 20, len(pbf), inventory_at)
    return replace(stage, board=checked, data=bytes(raw), inventory_offset=inventory_at)


class Stages:
    def __init__(self, data):
        self.data = data
        require(len(data) >= 512 and data[:4] == b"STG\0", "MINI STG header is invalid")
        version, header_size, count, directory_size = struct.unpack_from("<4I", data, 4)
        require(version == 0x100 and header_size == 512 and directory_size == count * 4
                and 0 < count < 1024 and not any(data[20:512]), "MINI STG header fields differ")
        base = header_size + directory_size
        require(base <= len(data), "MINI STG directory is truncated")
        offsets = struct.unpack_from(f"<{count}I", data, header_size)
        require(offsets[0] == 0 and all(a < b and b % 512 == 0 for a, b in zip(offsets, offsets[1:])),
                "MINI STG stage directory order/alignment differs")
        self.stages = []
        for index, relative in enumerate(offsets):
            at = base + relative
            end = base + offsets[index + 1] if index + 1 < count else len(data)
            require(at + 304 <= end <= len(data) and data[at:at + 4] == b"STF\0", "MINI STF header is invalid")
            version, size = struct.unpack_from("<2I", data, at + 4)
            pbf_at, pbf_size, inventory_at = struct.unpack_from("<3I", data, at + 16)
            require(version == 0x100 and 304 <= size <= end - at and pbf_at == 304
                    and inventory_at == pbf_at + pbf_size and inventory_at + 4 <= size,
                    "MINI STF sections differ")
            require(not any(data[at + size:end]), "MINI STF nonzero unowned padding")
            raw = data[at:at + size]
            board = Board.parse(raw[pbf_at:pbf_at + pbf_size])
            inventory_count = struct.unpack_from("<H", raw, inventory_at + 2)[0]
            require(inventory_at + 4 + inventory_count == size, "MINI STF inventory size differs")
            inventory = raw[inventory_at + 4:]
            require(all(token < 72 for token in inventory), "MINI STF inventory token exceeds original alphabet")
            # Odd inventories are read rounded up to two bytes by the original loader.
            require(not inventory_count % 2 or at + size < end and data[at + size] == 0,
                    "MINI STF odd inventory lacks its zero read padding")
            goals = tuple(raw[0x68 + i * 16:0x78 + i * 16] for i in range(3))
            require(all(g[0] <= 10 and g[3] == 0 for g in goals), "MINI STF goal structure differs")
            require(all(g[0] != 9 or 2 <= g[2] <= 9 and all(t < 72 for t in g[4:4 + g[2]]) for g in goals),
                    "MINI STF explicit goal word exceeds original search bounds")
            stage = Stage(index, at, raw, board, inventory_at, inventory, goals)
            stage.report()
            self.stages.append(stage)

    def report(self):
        return {"sha256": digest(self.data), "bytes": len(self.data), "stages": [s.report() for s in self.stages],
                "scope": "Original STG/STF/PBF structure. Parameter meanings not proven by runtime use remain numeric; no Korean puzzle rebuild yet"}


def originals():
    elf, rom = wwrom.originals()
    return Stages(next(raw for row, raw in wwrom_parts(elf, rom) if row["name"] == "stage/1p.stg"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    stages = originals()
    report = stages.report()
    if args.native:
        from mini_stage_native import StageMachine
        report["native"] = StageMachine(*wwrom.originals()).verify_stages()
    name = "mini-stages-native.json" if args.native else "mini-stages-structure.json"
    write_generated(WORK / "evidence" / name, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"[PASS] {len(stages.stages)} MINI stages; native={args.native}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"[ERROR] {error}")
