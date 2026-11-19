"""Ulz v2 codec matching MINI.ELF VA 0x185000 (24-bit stream offsets)."""

from collections import deque
import struct

from unpack_disc import require


def compress(data, width=11):
    require(1 <= width <= 15 and len(data) < 1 << 24, "Unsupported Ulz input")
    window = 1 << width
    longest = (0xffff >> width) + 3
    history = {}
    flags, literals, references = bytearray(), bytearray(), bytearray()
    control = tokens = at = 0

    def key(position):
        return data[position] << 16 | data[position + 1] << 8 | data[position + 2]

    while at < len(data):
        length, distance = 0, 0
        if at + 3 <= len(data):
            maximum = min(longest, len(data) - at)
            for tried, previous in enumerate(reversed(history.get(key(at), ()))):
                if tried == 64:
                    break
                matched = 3
                while matched < maximum and data[previous + matched] == data[at + matched]:
                    matched += 1
                if matched > length:
                    length, distance = matched, at - previous
                    if length == maximum:
                        break
        if length >= 3:
            references.extend(struct.pack("<H", (length - 3) << width | (distance - 1)))
        else:
            length = 1
            literals.append(data[at])
            control |= 1 << (31 - tokens)
        tokens += 1
        if tokens == 32:
            flags.extend(struct.pack("<I", control))
            control = tokens = 0

        # Retain only the live sliding window, including positions inside matches.
        for position in range(at, at + length):
            if position >= window:
                expired = key(position - window)
                bucket = history[expired]
                bucket.popleft()
                if not bucket:
                    del history[expired]
            if position + 3 <= len(data):
                history.setdefault(key(position), deque()).append(position)
        at += length
    if tokens:
        flags.extend(struct.pack("<I", control))
    flags.extend(b"\0" * 4)
    raw_at = 16 + len(flags)
    ref_at = (raw_at + len(literals) + 3) & ~3
    require(raw_at < 1 << 24 and ref_at < 1 << 24, "Ulz stream offset exceeds 24 bits")
    header = (b"Ulz\x1a" + len(data).to_bytes(3, "little") + b"\x02"
              + raw_at.to_bytes(3, "little") + bytes((width,))
              + ref_at.to_bytes(3, "little") + b"\0")
    output = header + flags + literals + b"\0" * (ref_at - raw_at - len(literals)) + references
    return output + b"\0" * (-len(output) % 4)


def decompress(data):
    require(len(data) >= 16 and data[:4] == b"Ulz\x1a", "Bad Ulz header")
    size = int.from_bytes(data[4:7], "little")
    raw_start = raw = int.from_bytes(data[8:11], "little")
    refs_start = refs = int.from_bytes(data[12:15], "little")
    width = data[11]
    require(data[7] == 2 and 1 <= width <= 15 and size <= 16 << 20, "Unsupported Ulz header")
    require(16 <= raw_start <= refs_start <= len(data), "Ulz stream offsets outside source")
    flags = 16
    output = bytearray()
    mask = (1 << width) - 1
    while len(output) < size:
        require(flags + 4 <= raw_start, "Ulz flags overlap literal stream")
        control = struct.unpack_from("<I", data, flags)[0]
        flags += 4
        for bit in range(31, -1, -1):
            if len(output) == size:
                break
            if control & (1 << bit):
                require(raw < refs_start, "Ulz literals overlap reference stream")
                output.append(data[raw])
                raw += 1
            else:
                require(refs + 2 <= len(data), "Truncated Ulz reference")
                word = struct.unpack_from("<H", data, refs)[0]
                refs += 2
                distance = (word & mask) + 1
                length = (word >> width) + 3
                require(distance <= len(output) and len(output) + length <= size,
                        "Ulz reference outside output")
                start = len(output) - distance
                if distance >= length:
                    output.extend(output[start:start + length])
                else:
                    block = output[start:]
                    output.extend((block * ((length + distance - 1) // distance))[:length])
    return bytes(output), {"flags_end": flags, "literal_end": raw,
                           "references_end": refs, "displacement_bits": width}
