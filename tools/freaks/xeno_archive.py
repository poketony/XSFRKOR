"""Freaks EXP TOC names, streaming archive reads, and bounded ARX decoding."""

from bisect import bisect_right
from collections import Counter
import struct

from unpack_disc import require


def decode_name(encoded, previous):
    require(encoded, "Empty encoded name")
    control = encoded[0]
    if not control:
        require(encoded[-1] == 0 and b"\0" not in encoded[1:-1], "Invalid full name")
        return encoded[1:-1]
    start = len(previous) - 1 - (control >> 4)
    count = control & 15
    require(start >= 0 and count == len(encoded) - 1, "Invalid differential name")
    return (previous[:start] + encoded[1:] + previous[start + count:]).split(b"\0", 1)[0]


def toc_entries(data, archive_size):
    require(data and len(data) == data[0] * 2048, "TOC sector count differs")
    cursor = 1
    stack = []
    previous = b""
    result = []
    names = set()
    while cursor < len(data) and data[cursor]:
        command = data[cursor]
        count = command & 63
        require(count >= 2 and cursor + count <= len(data), "Bad TOC record")
        encoded = data[cursor + 1:cursor + count]
        if command & 128:
            depth = len(stack) - encoded[0]
            require(0 <= depth <= len(stack), "Directory stack underflow")
            name = decode_name(encoded[1:], stack[depth] if depth < len(stack) else b"")
            del stack[depth:]
            stack.append(name)
            previous = b""
            cursor += count
            continue
        name = decode_name(encoded, previous)
        previous = name
        index = cursor + count
        end = index + (10 if command & 64 else 7)
        require(end <= len(data), "Truncated TOC fields")
        offset = int.from_bytes(data[index:index + 3], "little") * 2048
        size = struct.unpack_from("<I", data, index + 3)[0]
        decoded_size = int.from_bytes(data[index + 7:index + 10], "little") if command & 64 else None
        path = b"/".join(stack + [name]).decode("ascii")
        require(path.casefold() not in names, f"Duplicate archive path: {path}")
        require(offset >= len(data) and offset + size <= archive_size, f"Bad allocation: {path}")
        names.add(path.casefold())
        result.append({"path": path, "toc_index": index, "offset": offset,
                       "size": size, "decoded_size": decoded_size})
        cursor = end
    # The original has nonzero bytes after its zero command terminator.
    require(cursor < len(data) and data[cursor] == 0, "Missing TOC terminator")
    allocations = sorted((r["offset"], r["offset"] + r["size"]) for r in result if r["size"])
    require(all(a[1] <= b[0] for a, b in zip(allocations, allocations[1:])), "Archive allocations overlap")
    return result


class Chain:
    def __init__(self, paths):
        self.paths = paths
        self.sizes = [p.stat().st_size for p in paths]
        self.starts = []
        self.size = 0
        for size in self.sizes:
            self.starts.append(self.size)
            self.size += size
        self.streams = [p.open("rb") for p in paths]

    def read(self, offset, size):
        require(0 <= offset <= self.size and 0 <= size <= self.size - offset, "Read outside archive")
        output = bytearray()
        while size:
            index = bisect_right(self.starts, offset) - 1
            take = min(size, self.starts[index] + self.sizes[index] - offset)
            stream = self.streams[index]
            stream.seek(offset - self.starts[index])
            block = stream.read(take)
            require(len(block) == take, "Short archive read")
            output.extend(block)
            size -= take
            offset += take
        return bytes(output)

    def close(self):
        for stream in self.streams:
            stream.close()


def decompress_arx(data, expected_size):
    require(len(data) >= 136 and data[:4] == b"ARX\0", "Bad ARX header")
    size, stored, reserved = struct.unpack_from("<3I", data, 4)
    require(size == expected_size and size % 4 == 0 and size <= 128 << 20,
            "Invalid ARX decoded size")
    require(stored == len(data) and reserved == 0, "Unexpected ARX size/header convention")
    lookup = struct.unpack_from("<30I", data, 16)
    cursor = 136
    control = 0
    remaining = 0
    output = bytearray()

    def word():
        nonlocal cursor
        require(cursor + 4 <= len(data), "Truncated ARX word")
        value = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        return value

    def bit():
        nonlocal control, remaining
        if not remaining:
            control = word()
            remaining = 32
        remaining -= 1
        return (control >> remaining) & 1

    while len(output) < size:
        if not bit():
            value = word()
        else:
            if not bit():
                index = bit()
            elif not bit():
                index = 2 + (bit() << 1) + bit()
            elif not bit():
                index = 6 + (bit() << 2) + (bit() << 1) + bit()
            else:
                value = 0
                for _ in range(5):
                    value = (value << 1) | bit()
                index = 14 + value
            require(index < len(lookup), "ARX lookup index outside table")
            value = lookup[index]
        output.extend(struct.pack("<I", value))
    require(cursor == len(data), "Unexpected unused ARX words")
    return bytes(output)


def compress_arx(data):
    require(data and len(data) % 4 == 0 and len(data) <= 0xffffff, "ARX input must be nonempty aligned words")
    values = struct.unpack(f"<{len(data) // 4}I", data)
    lookup = [value for value, _ in Counter(values).most_common(30)]
    lookup.extend([0] * (30 - len(lookup)))
    indices = {}
    for index, value in enumerate(lookup):
        indices.setdefault(value, index)
    output = bytearray(136)
    struct.pack_into("<4sIII30I", output, 0, b"ARX\0", len(data), 0, 0, *lookup)
    remaining = 0
    control = 0
    control_at = 0

    def bit(value):
        nonlocal remaining, control, control_at
        if not remaining:
            control_at = len(output)
            output.extend(bytes(4))
            control, remaining = 0, 32
        remaining -= 1
        control |= value << remaining
        struct.pack_into("<I", output, control_at, control)

    for value in values:
        index = indices.get(value)
        if index is None:
            bit(0)
            output.extend(struct.pack("<I", value))
            continue
        if index < 2:
            prefix, code, width = (1, 0), index, 1
        elif index < 6:
            prefix, code, width = (1, 1, 0), index - 2, 2
        elif index < 14:
            prefix, code, width = (1, 1, 1, 0), index - 6, 3
        else:
            prefix, code, width = (1, 1, 1, 1), index - 14, 5
        for value_bit in prefix:
            bit(value_bit)
        for shift in range(width - 1, -1, -1):
            bit((code >> shift) & 1)
    struct.pack_into("<I", output, 8, len(output))
    return bytes(output)
