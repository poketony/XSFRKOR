import hashlib
import io
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "freaks"))

import prepare_originals as originals


def row(name, raw):
    return {"path": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def both(value, width):
    return value.to_bytes(width, "little") + value.to_bytes(width, "big")


def directory_entry(name, lba, size, *, directory=False):
    record = bytearray(33 + len(name) + int(len(name) % 2 == 0))
    record[0] = len(record)
    record[2:10] = both(lba, 4)
    record[10:18] = both(size, 4)
    record[25] = 2 if directory else 0
    record[28:32] = both(1, 2)
    record[32] = len(name)
    record[33:33 + len(name)] = name
    return record


class OriginalPreparationTests(unittest.TestCase):
    def test_checked_source_tree_preserves_existing_and_refuses_changed_bytes(self):
        raw = b"original"
        name = "unpacked/file"
        rows = {name: row(name, raw)}
        with tempfile.TemporaryDirectory() as storage, patch.object(originals, "required_files", return_value=rows):
            root = Path(storage) / "target"
            source = Path(storage) / "source"
            originals.save_original(source, rows[name], raw)
            originals.prepare(source_tree=source, root=root)
            self.assertEqual(originals.pending_files(root, rows, verify=True), [])
            with patch.object(originals, "save_original", side_effect=AssertionError("unexpected write")):
                originals.prepare(source_tree=source, root=root)
            (root / name).write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "not overwritten"):
                originals.prepare(source_tree=source, root=root)
            self.assertEqual((root / name).read_bytes(), b"modified")

    def test_verify_missing_is_read_only(self):
        with tempfile.TemporaryDirectory() as storage:
            root = Path(storage)
            with self.assertRaisesRegex(ValueError, "Missing 1 original inputs"):
                originals.pending_files(root, {"unpacked/file": row("unpacked/file", b"a")}, verify=True)
            self.assertEqual(list(root.iterdir()), [])

    def test_exp_reader_crosses_noncontiguous_disc_extents(self):
        records = {f"EXP/XENOSAGA.{i:02}": (2 * i, 3) for i in range(3)}
        raw = bytearray(8195)
        raw[:3], raw[4096:4099], raw[8192:] = b"ABC", b"DEF", b"GHI"
        source = originals.DiscInputs(io.BytesIO(raw), records)
        self.assertEqual(source.read_exp(2, 6), b"CDEFGH")
        self.assertEqual(source.read_exp(9, 0), b"")
        with self.assertRaisesRegex(ValueError, "outside EXP archive"):
            source.read_exp(8, 2)

    def test_wrong_payload_is_rejected_before_creating_directories(self):
        with tempfile.TemporaryDirectory() as storage:
            root = Path(storage)
            with self.assertRaisesRegex(ValueError, "wrong bytes"):
                originals.save_original(root, row("unpacked/file", b"a"), b"b")
            self.assertEqual(list(root.iterdir()), [])

    def test_prepares_only_requested_files_from_iso_and_exp_toc(self):
        outer, inner = b"game executable", b"archive text"
        toc = bytearray(2048)
        name = b"\0demo.txt\0"
        toc[0] = 1
        toc[1] = len(name) + 1
        toc[2:2 + len(name)] = name
        at = 2 + len(name)
        toc[at:at + 3] = (1).to_bytes(3, "little")
        struct.pack_into("<I", toc, at + 3, len(inner))
        iso = bytearray(28 * 2048)
        pvd = memoryview(iso)[16 * 2048:17 * 2048]
        pvd[:7] = b"\x01CD001\x01"
        pvd[80:88] = both(28, 4)
        pvd[128:132] = both(2048, 2)
        root_record = directory_entry(b"\0", 20, 2048, directory=True)
        pvd[156:156 + len(root_record)] = root_record
        root = directory_entry(b"EXP", 21, 2048, directory=True) + directory_entry(b"GAME.ELF;1", 22, len(outer))
        iso[20 * 2048:20 * 2048 + len(root)] = root
        directory = b"".join(directory_entry(f"XENOSAGA.{n:02};1".encode(), 23 + n, len(data))
                             for n, data in enumerate((toc, inner, b"unused")))
        iso[21 * 2048:21 * 2048 + len(directory)] = directory
        files = [("GAME.ELF", outer), ("EXP/XENOSAGA.00", toc),
                 ("EXP/XENOSAGA.01", inner), ("EXP/XENOSAGA.02", b"unused")]
        for index, (_, data) in enumerate(files, 22):
            iso[index * 2048:index * 2048 + len(data)] = data
        manifest = {"disc-inventory.json": {"iso_size": len(iso), "iso_sha256": hashlib.sha256(iso).hexdigest(),
                                            "files": [dict(row(n, d), lba=i) for i, (n, d) in enumerate(files, 22)]}}
        outer_row, inner_row = row("unpacked/GAME.ELF", outer), row("decompressed/EXP/XENOSAGA/demo.txt", inner)
        member = dict(path="demo.txt", toc_index=at, offset=2048, size=len(inner), decoded_size=None,
                      source="EXP/XENOSAGA.00~02", stored_sha256=inner_row["sha256"], codec="raw", output=inner_row)
        manifest["contents-report.json"] = {"iso_sha256": manifest["disc-inventory.json"]["iso_sha256"],
                                            "members": [member]}
        manifest["required-originals.json"] = {"files": [outer_row, inner_row]}
        with tempfile.TemporaryDirectory() as storage, patch.object(originals, "manifest", side_effect=manifest.__getitem__):
            source = Path(storage) / "source.iso"
            source.write_bytes(iso)
            workspace = Path(storage) / "checkout"
            originals.prepare(iso=source, root=workspace)
            self.assertEqual((workspace / outer_row["path"]).read_bytes(), outer)
            self.assertEqual((workspace / inner_row["path"]).read_bytes(), inner)
            self.assertFalse((workspace / "unpacked/EXP").exists())


if __name__ == "__main__":
    unittest.main()
