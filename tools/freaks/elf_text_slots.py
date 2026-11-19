"""Allocate translated C strings only inside audited ELF string slots."""

from adv_format import require, text_end


def allocate(data, elf, slots, replacements, label, *, find_end=text_end):
    require(set(replacements) <= set(slots), "Unknown ELF string allocation")
    output = bytearray(data)
    free, pending, positions = [], [], {}
    for address, end in sorted(slots.items()):
        offset, limit = elf.offset(address), elf.offset(end - 1) + 1
        terminator = find_end(data, offset)
        require(offset <= terminator < limit and not any(data[terminator + 1:limit]),
                "ELF string slot contains non-text data")
        old = data[offset:terminator]
        raw = replacements.get(address, old)
        if address in replacements and len(raw) + 1 > end - address:
            pending.append((address, raw))
            used = 0
        else:
            positions[address] = address
            used = len(raw) + 1
            if address in replacements:
                output[offset:limit] = raw + bytes(end - address - len(raw))
        low = (address + used + 3) & ~3
        if low < end:
            free.append((low, end))
    merged = []
    for low, high in free:
        if merged and merged[-1][1] == low:
            merged[-1] = (merged[-1][0], high)
        else:
            merged.append((low, high))
    for address, raw in sorted(pending, key=lambda item: -len(item[1])):
        allocation = next((i for i, (a, b) in enumerate(merged) if len(raw) + 1 <= b - a), None)
        require(allocation is not None,
                f"No owned {label} slot fits {len(raw) + 1} bytes at {address:08X}; shorten translations (no arbitrary ELF cave is used)")
        low, high = merged.pop(allocation)
        positions[address] = low
        output[elf.offset(low):elf.offset(low) + len(raw) + 1] = raw + b"\0"
        next_free = (low + len(raw) + 4) & ~3
        if next_free < high:
            merged.insert(allocation, (next_free, high))
    return output, positions
