"""Cross-check extracted payloads using the original game's MIPS functions."""

import argparse
import json
import struct

from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS64, UC_MODE_LITTLE_ENDIAN
from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2,
                               UC_MIPS_REG_GP, UC_MIPS_REG_SP, UC_MIPS_REG_RA,
                               UC_MIPS_REG_PC, UC_MIPS_REG_V0,
                               UC_CPU_MIPS64_MIPS64R2_GENERIC)

from elf_probe import Elf

from build_paths import DATA, REPO, WORK
from unpack_disc import require, file_hash
from unpack_contents import digest


class Native:
    SOURCE = 0x1000000
    OUTPUT = 0x1800000
    STOP = 0x20ff000
    STACK = 0x20fe000

    def __init__(self, data):
        self.elf = Elf(data)
        self.uc = Uc(UC_ARCH_MIPS, UC_MODE_MIPS64 | UC_MODE_LITTLE_ENDIAN)
        self.uc.ctl_set_cpu_model(UC_CPU_MIPS64_MIPS64R2_GENERIC)
        self.uc.mem_map(0x100000, 0x2000000)
        phoff = struct.unpack_from("<I", data, 28)[0]
        stride, count = struct.unpack_from("<HH", data, 42)
        for i in range(count):
            kind, offset, va, _, size, _, _, _ = struct.unpack_from("<8I", data, phoff + i * stride)
            if kind == 1:
                self.uc.mem_write(va, data[offset:offset + size])

    def decode(self, entry, raw, expected, codec):
        require(len(raw) < 4 << 20 and len(expected) < 8 << 20, "Native probe allocation exceeded")
        self.uc.mem_write(self.SOURCE, raw)
        self.uc.mem_write(self.OUTPUT - 16, b"\xa5" * (len(expected) + 32))
        self.uc.reg_write(UC_MIPS_REG_GP, self.elf.gp)
        self.uc.reg_write(UC_MIPS_REG_SP, self.STACK)
        self.uc.reg_write(UC_MIPS_REG_RA, self.STOP)
        if codec == "Ulz":
            args = (self.SOURCE, self.OUTPUT, 0)
        else:
            args = (self.OUTPUT, self.SOURCE, len(raw))
        for register, value in zip((UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2), args):
            self.uc.reg_write(register, value)
        if codec == "ARX":
            self.uc.mem_write(self.elf.gp - 0x74b8, struct.pack("<I", 0x1400000))
        self.uc.emu_start(entry, self.STOP, timeout=60_000_000, count=200_000_000)
        require(self.uc.reg_read(UC_MIPS_REG_PC) == self.STOP, "Native function did not return")
        require(self.uc.reg_read(UC_MIPS_REG_SP) == self.STACK, "Native stack pointer changed")
        actual = bytes(self.uc.mem_read(self.OUTPUT, len(expected)))
        require(actual == expected, "Native decoder output differs")
        require(bytes(self.uc.mem_read(self.SOURCE, len(raw))) == raw, "Native source buffer changed")
        require(bytes(self.uc.mem_read(self.OUTPUT - 16, 16)) == b"\xa5" * 16
                and bytes(self.uc.mem_read(self.OUTPUT + len(expected), 16)) == b"\xa5" * 16,
                "Native output crossed canary")
        return self.uc.reg_read(UC_MIPS_REG_V0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    report = json.loads((DATA / "contents-report.json").read_text(encoding="utf-8"))
    original = REPO / "unpacked"
    main_data = (original / "SLPS_253.53").read_bytes()
    for path in ("ADV/ADV.ELF", "DTB/DTB.ELF"):
        other = (original / path).read_bytes()
        require(other[0x3610:0x3924] == main_data[0x3610:0x3924], "GRP decoder code differs between components")
    machines = {"Freaks-LZSS": Native(main_data),
                "Ulz": Native((original / "MINI/MINI.ELF").read_bytes()),
                "ARX": Native((original / "EXP/EXP.ELF").read_bytes())}
    entries = {"Freaks-LZSS": 0x102610, "Ulz": 0x185000, "ARX": 0x194af8}
    counts = {name: 0 for name in machines}
    records = []
    source_name = None
    source = None
    for item in report["members"]:
        codec = item["codec"]
        if codec not in machines or args.smoke and counts[codec]:
            continue
        if item["source"] != source_name:
            source_name = item["source"]
            if codec == "ARX":
                from xeno_archive import Chain
                chain = Chain([original / f"EXP/XENOSAGA.{i:02}" for i in range(3)])
                try:
                    raw = chain.read(item["offset"], item["size"])
                finally:
                    chain.close()
            else:
                source = (original / item["source"]).read_bytes()
        if codec != "ARX":
            raw = source[item["offset"]:item["offset"] + item["stored_size"]]
        require(digest(raw) == item["stored_sha256"], "Native probe source hash differs")
        target = REPO / item["output"]["path"]
        require(file_hash(target) == item["output"]["sha256"], "Native probe output hash differs")
        decoded = target.read_bytes()
        result = machines[codec].decode(entries[codec], raw, decoded, codec)
        expected_return = Native.OUTPUT + len(decoded) if codec == "Freaks-LZSS" else len(decoded)
        require(result == expected_return, f"Native return differs for {codec}: {result:#x}")
        counts[codec] += 1
        records.append({"path": item["output"]["path"], "codec": codec, "sha256": digest(decoded)})
        if len(records) % 100 == 0:
            print(f"[NATIVE] {len(records)} payloads matched", flush=True)
    require(all(counts.values()), "A compression family was not tested")
    result = {"mode": "smoke" if args.smoke else "complete", "counts": counts,
              "native_output_bytes_equal": True, "source_buffers_unchanged": True,
              "stack_and_output_canaries": "pass", "payloads": records}
    destination = WORK / "evidence" / ("native-smoke.json" if args.smoke else "native-codecs.json")
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] {counts}", flush=True)


if __name__ == "__main__":
    main()
