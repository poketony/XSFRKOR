"""Prepare only the original game files required by the asset builder."""

from bisect import bisect_right
from collections import defaultdict
import hashlib
import json

from build_paths import DATA, REPO
from lzss import decompress as lzss
from ulz import decompress as ulz
from unpack_disc import file_hash, iso_records, require, safe_path
from xeno_archive import decompress_arx, toc_entries


def manifest(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def required_files():
    rows = manifest("required-originals.json")["files"]
    result = {row["path"]: row for row in rows}
    require(len(result) == len(rows) and all(name.startswith(("unpacked/", "decompressed/"))
                                           for name in result), "Bad original input inventory")
    return result


def pending_files(root, rows, *, verify=False):
    missing = []
    for name, row in rows.items():
        path = safe_path(root, name)
        if path.exists():
            require(path.is_file() and path.stat().st_size == row["size"] and file_hash(path) == row["sha256"],
                    f"Original input differs; not overwritten: {path}")
        else:
            missing.append(name)
            require(verify or not path.with_name(path.name + ".partial").exists(),
                    f"Previous partial input exists: {path}")
    if verify and missing:
        raise ValueError(f"Missing {len(missing)} original inputs (first: {missing[0]}). "
                         "Run python -B tools/rebuild_assets.py prepare --iso <original.iso> "
                         "or use --source-tree <prepared checkout>.")
    return missing


def save_original(root, row, raw):
    name = row["path"]
    require(len(raw) == row["size"] and hashlib.sha256(raw).hexdigest() == row["sha256"],
            f"Prepared original has the wrong bytes: {name}")
    path = safe_path(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("xb") as stream:
        stream.write(raw)
    require(file_hash(partial) == row["sha256"], f"Original write verification failed: {path}")
    # Do not replace a destination unexpectedly created during preparation.
    require(not path.exists(), f"Original appeared during preparation: {path}")
    partial.rename(path)


class DiscInputs:
    def __init__(self, stream, records):
        self.stream, self.records = stream, records
        self.parts = [f"EXP/XENOSAGA.{index:02}" for index in range(3)]
        self.starts = [0]
        for name in self.parts:
            self.starts.append(self.starts[-1] + records[name][1])

    def read(self, name, offset=0, size=None):
        lba, length = self.records[name]
        size = length - offset if size is None else size
        require(0 <= offset <= length and 0 <= size <= length - offset, f"Read outside ISO file: {name}")
        self.stream.seek(lba * 2048 + offset)
        raw = self.stream.read(size)
        require(len(raw) == size, f"Short ISO read: {name}")
        return raw

    def read_exp(self, offset, size):
        require(0 <= offset <= self.starts[-1] and 0 <= size <= self.starts[-1] - offset,
                "Read outside EXP archive")
        result = bytearray()
        while size:
            index = bisect_right(self.starts, offset) - 1
            take = min(size, self.starts[index + 1] - offset)
            result.extend(self.read(self.parts[index], offset - self.starts[index], take))
            size -= take
            offset += take
        return bytes(result)

    def decoded(self, row, exp):
        kind = row["codec"]
        if row["source"] == "EXP/XENOSAGA.00~02":
            entry = exp[row["path"]]
            require(all(entry[field] == row[field] for field in ("offset", "size", "decoded_size", "toc_index")),
                    f"EXP TOC entry differs: {row['path']}")
            stored = self.read_exp(entry["offset"], entry["size"])
        else:
            stored = self.read(row["source"], row["offset"], row["stored_size"])
        require(hashlib.sha256(stored).hexdigest() == row["stored_sha256"],
                f"Original compressed bytes differ: {row['output']['path']}")
        if kind == "Freaks-LZSS":
            return lzss(stored, limit=row["output"]["size"])
        if kind == "Ulz":
            return ulz(stored)[0]
        if kind == "ARX":
            return decompress_arx(stored, row["decoded_size"])
        require(kind == "raw", f"Unknown original codec: {kind}")
        return stored


def prepare_from_iso(iso, root, rows, missing):
    info = manifest("disc-inventory.json")
    require(iso.stat().st_size == info["iso_size"] and file_hash(iso) == info["iso_sha256"],
            "Original ISO differs. Use the supported unmodified Japanese disc (SLPS-25353).")
    records = {r["path"]: (r["lba"], r["size"]) for r in info["files"]}
    require(iso_records(iso) == records, "Original ISO directory differs from the inventory")
    contents = manifest("contents-report.json")
    require(contents["iso_sha256"] == info["iso_sha256"], "Decoded input inventory is for a different disc")
    members = {row["output"]["path"]: row for row in contents["members"]}
    with iso.open("rb") as stream:
        inputs = DiscInputs(stream, records)
        toc = inputs.read(inputs.parts[0])
        exp = {row["path"]: row for row in toc_entries(toc, inputs.starts[-1])}
        for name in missing:
            row = rows[name]
            if name.startswith("unpacked/"):
                raw = inputs.read(name.removeprefix("unpacked/"))
            else:
                member = members[name]
                require(member["output"] == row, f"Original inventories disagree: {name}")
                raw = inputs.decoded(member, exp)
            save_original(root, row, raw)


def prepare(iso=None, source_tree=None, *, root=REPO):
    rows = required_files()
    missing = pending_files(root, rows)
    if iso is not None:
        prepare_from_iso(iso.resolve(), root, rows, missing)
    else:
        require(source_tree is not None, "Supply an original ISO or a prepared source tree")
        source = source_tree.resolve()
        pending_files(source, rows, verify=True)
        for name in missing:
            save_original(root, rows[name], safe_path(source, name).read_bytes())
    print(f"[PASS] {len(rows)} original inputs checked; {len(missing)} prepared")


def check():
    pending_files(REPO, required_files(), verify=True)
