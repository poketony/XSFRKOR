"""Read-only reference-site audit for the 55 approved EXP embedded strings."""

import json
import struct

import audit_exp_strings as audit
from elf_probe import Elf, signed16
import exp_executable as executable
from font_tool import write_generated

from build_paths import WORK


LOW_OPS = (9, 13, 0x19, 0x20, 0x21, 0x23, 0x24, 0x25, 0x28, 0x29, 0x2b)


def collect(sources, rows):
    main = Elf(sources["EXP.ELF"])
    owners = {target: row.address for row in rows for target in range(row.address, row.address + row.span)}
    result = []
    for name, data in sources.items():
        sections = main.sections if name == "EXP.ELF" else [s for _, s in audit.sections(data)]
        for section in sections:
            if not section[2] & 2 or section[1] == 8:
                continue
            words = struct.unpack_from(f"<{section[5] // 4}I", data, section[4])
            for index, word in enumerate(words):
                address = section[3] + index * 4
                if word in owners:
                    result.append({"source": name, "site": address, "kind": "word", "target": word,
                                   "owner": owners[word], "word": word})
                if not section[2] & 4:
                    continue
                target = main.gp + signed16(word)
                if word >> 21 & 31 == 28 and target in owners:
                    result.append({"source": name, "site": address, "kind": "gp", "target": target,
                                   "owner": owners[target], "word": word})
                if word >> 26 != 15:
                    continue
                register = word >> 16 & 31
                for low_index in range(index + 1, min(index + 17, len(words))):
                    low = words[low_index]
                    if low >> 21 & 31 != register or low >> 26 not in LOW_OPS:
                        continue
                    target = ((word & 0xffff) << 16) + (low & 0xffff if low >> 26 == 13 else signed16(low))
                    if target in owners:
                        result.append({"source": name, "site": section[3] + low_index * 4, "upper": address,
                                       "kind": "lui", "target": target, "owner": owners[target],
                                       "word": low, "upper_word": word})
    return result


def main():
    import exp_literal_relocation as literal
    sources = executable.originals()
    rows = executable.entries(sources)
    refs = collect(sources, rows)
    literal.validate_candidates(sources, {row.address: row for row in rows}, refs)
    observed = {(ref["owner"], ref["site"]) for ref in refs if ref["source"] == "EXP.ELF" and ref["kind"] == "lui"}
    report = {"scope": "Candidate sites only, not a patch whitelist. Includes clobbered-register and overlay-address false matches.",
              "sources": {name: executable.sb.checksum(data) for name, data in sources.items()},
              "strings": [{"address": row.address, "bytes": len(row.raw), "span": row.span, "text": row.text,
                           "references": [r for r in refs if r["owner"] == row.address]} for row in rows],
              "reference_candidates": len(refs), "interior_candidates": sum(r["target"] != r["owner"] for r in refs),
              "reviewed_literal_strings": len(literal.RELOCATABLE),
              "reviewed_low_sites": sum(map(len, literal.LOW_SITES.values())),
              "reviewed_table_words": sum(map(len, literal.WORD_SITES.values())),
              "reviewed_sites_missed_by_short_window": [site for address, sites in literal.LOW_SITES.items()
                                                        for site in sites if (address, site) not in observed],
              "discarded_clobbered_lui_site": 0x27eb20,
              "game_files_written": 0}
    write_generated(WORK / "evidence/exp-relocation-candidates.json", (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    for row in report["strings"]:
        sites = sorted({(r["source"], r["site"], r["kind"], r["target"]) for r in row["references"]})
        print(f"{row['address']:08X} " + "; ".join(f"{name}:{site:08X} {kind} -> {target:08X}" for name, site, kind, target in sites))
    print(f"[AUDIT] {len(rows)} owned strings, {len(refs)} candidates; 46 literal strings/53 sites reviewed; no game outputs")


if __name__ == "__main__":
    main()
