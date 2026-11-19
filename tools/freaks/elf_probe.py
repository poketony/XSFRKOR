"""Small read-only ELF/R5900 inspection helpers; addresses are virtual."""

import argparse
from pathlib import Path
import struct

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS64, CS_MODE_LITTLE_ENDIAN


REGS = "zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra".split()


def signed16(word):
    return (word & 0xffff) - (0x10000 if word & 0x8000 else 0)


class Elf:
    def __init__(self, data):
        self.data = data
        h = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
        if h[0][:6] != b"\x7fELF\x01\x01" or h[2] != 8:
            raise ValueError("Expected little-endian ELF32 MIPS")
        self.sections = [struct.unpack_from("<10I", data, h[6] + i * h[11])
                         for i in range(h[12])]
        names = self.sections[h[13]]
        names = data[names[4]:names[4] + names[5]]
        self.named = {names[s[0]:].split(b"\0", 1)[0].decode(): s for s in self.sections}
        self.gp = struct.unpack_from("<I", data, self.named[".reginfo"][4] + 20)[0]
        self.dis = Cs(CS_ARCH_MIPS, CS_MODE_MIPS64 | CS_MODE_LITTLE_ENDIAN)

    def offset(self, address):
        for s in self.sections:
            if s[2] & 2 and s[1] != 8 and s[3] <= address < s[3] + s[5]:
                return s[4] + address - s[3]
        raise ValueError(f"Not file-backed: {address:#x}")

    def instruction(self, address):
        offset = self.offset(address)
        raw = self.data[offset:offset + 4]
        w = int.from_bytes(raw, "little")
        if w >> 26 in (0x1e, 0x1f):
            return f"{'lq' if w >> 26 == 0x1e else 'sq'} ${REGS[w >> 16 & 31]}, {signed16(w):#x}(${REGS[w >> 21 & 31]})"
        if w >> 26 == 0x1c:
            return f".word {w:#010x} ; R5900 MMI"
        decoded = next(self.dis.disasm(raw, address), None)
        return f"{decoded.mnemonic} {decoded.op_str}" if decoded else f".word {w:#010x}"

    def listing(self, address, size):
        return "\n".join(f"{a:08X} [{self.offset(a):06X}] {self.instruction(a)}"
                         for a in range(address, address + size, 4))

    def refs(self, target):
        results = []
        for s in self.sections:
            if not s[2] & 2 or s[1] == 8:
                continue
            words = struct.unpack_from(f"<{s[5] // 4}I", self.data, s[4])
            for i, w in enumerate(words):
                a = s[3] + i * 4
                if w == target:
                    results.append((a, "data pointer"))
                if not s[2] & 4:
                    continue
                op, rs, rt = w >> 26, w >> 21 & 31, w >> 16 & 31
                if op in (2, 3) and (w & 0x3ffffff) * 4 == target:
                    results.append((a, "jump/call"))
                if rs == 28 and self.gp + signed16(w) == target:
                    results.append((a, "GP candidate"))
                if op != 15:
                    continue
                for j in range(i + 1, min(i + 17, len(words))):
                    low = words[j]
                    if low >> 21 & 31 != rt:
                        continue
                    lo_op = low >> 26
                    if lo_op not in (9, 13, 0x19, 0x20, 0x21, 0x23, 0x24, 0x25, 0x28, 0x29, 0x2b, 0x31, 0x39, 0x37, 0x3f):
                        continue
                    value = ((w & 0xffff) << 16) + (low & 0xffff if lo_op == 13 else signed16(low))
                    if value == target:
                        results.append((a, f"LUI candidate -> {s[3] + j * 4:08X}"))
        return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("elf", type=Path)
    p.add_argument("mode", choices=("disasm", "refs", "lz-candidates"))
    p.add_argument("address", type=lambda v: int(v, 0), nargs="?", default=0)
    p.add_argument("size", type=lambda v: int(v, 0), nargs="?", default=0x100)
    a = p.parse_args()
    elf = Elf(a.elf.read_bytes())
    if a.mode == "disasm":
        print(elf.listing(a.address, a.size))
    elif a.mode == "refs":
        for address, kind in elf.refs(a.address):
            print(f"{address:08X} {kind}")
    else:
        s = elf.named[".text"]
        for offset in range(s[4], s[4] + s[5], 4):
            w = struct.unpack_from("<I", elf.data, offset)[0]
            if w >> 26 == 12 and w & 0xffff == 0x3ff:
                address = s[3] + offset - s[4]
                print(elf.listing(address - 0x50, 0xb0), "\n")


if __name__ == "__main__":
    main()
