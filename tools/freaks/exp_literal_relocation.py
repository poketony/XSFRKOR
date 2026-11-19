"""Reallocate reviewed NUL-driven EXP literals inside their owned string spans."""

import struct

from unpack_disc import require


# Only low ADDIU immediates change: shared LUI values and register lifetimes stay intact.
LOW_SITES = {
    0x2b20c0: (0x1d6a88, 0x1d6ba4), 0x2b2160: (0x1d6a44,),
    0x2b2180: (0x1d6a78,), 0x2b21a0: (0x1d6b10,), 0x2b21c0: (0x1d6b20,),
    0x2b21f8: (0x1d6c7c,), 0x2b2228: (0x1d6cb4,),
    0x682bd0: (0x1e7634,), 0x682c48: (0x1e7f0c,), 0x682cc0: (0x1e7ea0,),
    0x682d70: (0x1e8404,), 0x682dc0: (0x1e8398,),
    0x684840: (0x229f78, 0x22be04, 0x22bed0), 0x684998: (0x22bdec, 0x22c490),
    0x6849f0: (0x22be3c, 0x22bf34), 0x684ab8: (0x22ce80, 0x22e7ec),
    0x684c90: (0x22e484,),
    0x696dd0: (0x22b768,), 0x696de0: (0x22b794,),
    0x696de8: (0x22b7c0,), 0x696df0: (0x22b7ec,),
    0x686d28: (0x27d58c,), 0x697230: (0x27c87c,), 0x687928: (0x1a4bbc,),
    0xa82a90: (0xa8017c,), 0xa82af8: (0xa80198,), 0xa82b48: (0xa80260,),
    0xa82b90: (0xa80270,), 0xa82bd8: (0xa8028c,), 0xa82c30: (0xa80350,),
    0xa82c90: (0xa802d4,), 0xa82df8: (0xa81898,), 0xa82ee8: (0xa819cc,),
    0xa82f08: (0xa81b4c,), 0xa83088: (0xa81d28,), 0xa830f8: (0xa81de0,),
    0xa83140: (0xa81e30,), 0xa831b0: (0xa81f28,), 0xa83228: (0xa81fa8,),
    0xa83278: (0xa82000,), 0xa83430: (0xa82268,),
    0xa83460: (0xa82204,), 0xa834a8: (0xa82274,),
}
WORD_SITES = {0x682e50: (0x2b79b8,), 0xa83308: (0xa82a80,),
              0xa833f8: (0xa82a7c,), 0xa83430: (0xa82a78,)}
RELOCATABLE = LOW_SITES.keys() | WORD_SITES.keys()
# A conservative tool policy, not a claim that every renderer has a 256-byte buffer.
MAX_BYTES = 255


def bank(address):
    return (address + 0x8000) >> 16


def validate_sites(data, elf):
    require(len(RELOCATABLE) == 46, "EXP literal whitelist differs")
    for target, sites in LOW_SITES.items():
        for site in sites:
            word = struct.unpack_from("<I", data, elf.offset(site))[0]
            require(word >> 26 == 9 and word & 65535 == target & 65535,
                    f"EXP literal ADDIU reference differs: {site:08X}")
    for target, sites in WORD_SITES.items():
        for site in sites:
            require(struct.unpack_from("<I", data, elf.offset(site))[0] == target,
                    f"EXP literal table reference differs: {site:08X}")


def validate_candidates(sources, rows, candidates=None):
    from audit_exp_relocation import collect
    from elf_probe import Elf
    elf = Elf(sources["EXP.ELF"])
    validate_sites(sources["EXP.ELF"], elf)
    for ref in collect(sources, rows.values()) if candidates is None else candidates:
        target, site = ref["owner"], ref["site"]
        if target not in RELOCATABLE:
            continue
        require(ref["target"] == target, "EXP literal has an interior reference candidate")
        # This candidate uses an obsolete LUI. The actual intervening LUI builds a color command.
        if ref["source"] == "EXP.ELF" and target == 0x696dd0 and site == 0x27eb20:
            require(struct.unpack_from("<2I", sources["EXP.ELF"], elf.offset(0x27eb1c))
                    == (0x3c050068, 0x24a56dd0), "EXP discarded color-command candidate differs")
            continue
        sites = WORD_SITES.get(target, ()) if ref["kind"] == "word" else LOW_SITES.get(target, ())
        require(ref["kind"] in ("word", "lui") and site in sites
                and (ref["source"] == "EXP.ELF" or ref["source"] == "OV02.OVL" and target >= 0xa80000),
                f"Unreviewed EXP literal reference: {ref['source']}:{site:08X}")


def allocate(rows, prepared, locations, numeric_loans):
    pending = [(a, value[0]) for a, value in prepared.items()
               if a in RELOCATABLE and len(value[0]) >= rows[a].span]
    if not pending:
        return dict(locations), [], {}, set()
    require(all(len(raw) <= MAX_BYTES for _, raw in pending),
            "EXP literal exceeds the conservative 255-byte relocation policy")
    runs = []
    for address in sorted(RELOCATABLE):
        if runs and runs[-1][-1] + rows[runs[-1][-1]].span == address and bank(runs[-1][0]) == bank(address):
            runs[-1].append(address)
        else:
            runs.append([address])
    free = []
    for address in sorted(RELOCATABLE):
        row = rows[address]
        raw = prepared.get(address, (row.raw, None))[0]
        used = len(raw) + 1 if len(raw) < row.span else 0
        start, end = address + used, address + row.span
        if start < end:
            free.append((start, end))
    for loan in numeric_loans:
        low, high = loan["target"], loan["target"] + loan["bytes"]
        split = []
        for start, end in free:
            if end <= low or high <= start:
                split.append((start, end))
            else:
                if start < low:
                    split.append((start, low))
                if high < end:
                    split.append((high, end))
        free = split
    merged = []
    for start, end in free:
        require(bank(start) == bank(end - 1), "EXP literal free span crosses its LUI bank")
        if merged and merged[-1][1] == start and bank(merged[-1][0]) == bank(start):
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    positions, moves, extra, clear, placed = dict(locations), [], {}, set(), set()

    def install(bodies, low, high, owners, kind):
        nonlocal merged
        merged = [(a, b) for a, b in merged if b <= low or high <= a]
        cursor = low
        for a, body in bodies.items():
            positions[a] = cursor
            extra[a] = body
            clear.add(a)
            if a != cursor or len(body) >= rows[a].span:
                moves.append({"source": a, "target": cursor, "bytes": len(body) + 1,
                              "owners": list(owners), "kind": kind})
            cursor += len(body) + 1
        placed.update(bodies)
        if cursor < high:
            merged.append((cursor, high))
            merged.sort()

    pending_addresses = {a for a, _ in pending}
    for run in runs:
        low, high = run[0], run[-1] + rows[run[-1]].span
        if len(set(run) & pending_addresses) < 2:
            continue
        if any(loan["target"] < high and low < loan["target"] + loan["bytes"] for loan in numeric_loans):
            continue
        bodies = {a: prepared.get(a, (rows[a].raw, None))[0] for a in run}
        if sum(len(body) + 1 for body in bodies.values()) <= high - low:
            install(bodies, low, high, run, "compact")

    for address, raw in sorted(pending, key=lambda item: (-len(item[1]), item[0])):
        if address in placed:
            continue
        choices = [(index, low, high) for index, (low, high) in enumerate(merged)
                   if bank(low) == bank(address) and high - low >= len(raw) + 1]
        if not choices:
            candidates = []
            for run in runs:
                low, high = run[0], run[-1] + rows[run[-1]].span
                if bank(low) != bank(address) or set(run) & placed:
                    continue
                reservations = [*numeric_loans, *moves]
                if any(loan["target"] < high and low < loan["target"] + loan["bytes"] for loan in reservations):
                    continue
                bodies = {a: prepared.get(a, (rows[a].raw, None))[0] for a in run}
                used = sum(len(body) + 1 for body in bodies.values())
                if used + (0 if address in run else len(raw) + 1) <= high - low:
                    candidates.append((run, bodies, low, high))
            if candidates:
                run, bodies, low, high = min(candidates, key=lambda item: (address not in item[0], abs(item[2] - address)))
                install(bodies, low, high, run, "compact")
                if address in run:
                    continue
                choices = [(index, a, b) for index, (a, b) in enumerate(merged)
                           if bank(a) == bank(address) and b - a >= len(raw) + 1]
            else:
                # Exchange two owned blocks when the incoming text cannot share the
                # larger block, but that block's short texts fit the vacated source.
                source_run = next(run for run in runs if address in run)
                source_low, source_high = source_run[0], source_run[-1] + rows[source_run[-1]].span
                source_bodies = {a: prepared.get(a, (rows[a].raw, None))[0] for a in source_run}
                source_used = sum(len(body) + 1 for body in source_bodies.values())
                swaps = []
                for run in runs:
                    low, high = run[0], run[-1] + rows[run[-1]].span
                    if run == source_run or bank(low) != bank(address) or (set(run) | set(source_run)) & placed:
                        continue
                    intervals = ((source_low, source_high), (low, high))
                    if any(loan["target"] < b and a < loan["target"] + loan["bytes"]
                           for loan in (*numeric_loans, *moves) for a, b in intervals):
                        continue
                    bodies = {a: prepared.get(a, (rows[a].raw, None))[0] for a in run}
                    if source_used <= high - low and sum(len(body) + 1 for body in bodies.values()) <= source_high - source_low:
                        swaps.append((run, bodies, low, high))
                if swaps:
                    run, bodies, low, high = min(swaps, key=lambda item: abs(item[2] - address))
                    install(source_bodies, low, high, run, "exchange")
                    install(bodies, source_low, source_high, source_run, "exchange")
                    continue
        require(choices, f"No owned EXP literal span fits {len(raw) + 1} bytes at {address:08X}; "
                "shorten nearby translations; no arbitrary executable cave is used")
        index, low, high = min(choices, key=lambda item: (abs(item[1] - address), item[1]))
        merged.pop(index)
        positions[address] = low
        stop = low + len(raw) + 1
        if stop < high:
            merged.insert(index, (stop, high))
        owners = [a for a in sorted(RELOCATABLE) if a < stop and low < a + rows[a].span]
        moves.append({"source": address, "target": low, "bytes": len(raw) + 1, "owners": owners, "kind": "borrow"})
        placed.add(address)
    return positions, moves, extra, clear


def patch_references(output, elf, locations):
    sites = []
    for address, target in sorted(locations.items()):
        if address == target or address not in RELOCATABLE:
            continue
        require(bank(address) == bank(target), "EXP literal relocation would alter a shared LUI value")
        for site in LOW_SITES.get(address, ()):
            offset = elf.offset(site)
            word = struct.unpack_from("<I", output, offset)[0]
            require(word >> 26 == 9 and word & 65535 == address & 65535, "EXP literal patch overlap")
            struct.pack_into("<I", output, offset, word & 0xffff0000 | target & 65535)
            sites.append(site)
        for site in WORD_SITES.get(address, ()):
            offset = elf.offset(site)
            require(struct.unpack_from("<I", output, offset)[0] == address, "EXP literal table patch overlap")
            struct.pack_into("<I", output, offset, target)
            sites.append(site)
    return sites
