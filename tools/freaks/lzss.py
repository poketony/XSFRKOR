"""Freaks GRP codec, confirmed against SLPS_253.53 VA 0x102610."""


def decompress(data, limit=128 << 20):
    output = bytearray()
    cursor = 0
    while cursor < len(data):
        flags = data[cursor]
        cursor += 1
        if cursor == len(data):
            raise ValueError("Flag byte has no following token")
        for bit in range(7, -1, -1):
            if cursor == len(data):
                break
            if flags & (1 << bit):
                output.append(data[cursor])
                cursor += 1
            else:
                if cursor + 2 > len(data):
                    raise ValueError("Truncated LZ reference")
                word = data[cursor] | (data[cursor + 1] << 8)
                cursor += 2
                distance = 1024 - (word & 1023)
                length = (word >> 10) + 2
                if distance > len(output):
                    raise ValueError("LZ reference precedes output")
                if len(output) + length > limit:
                    raise ValueError("LZ output exceeds safety bound")
                start = len(output) - distance
                if distance >= length:
                    output.extend(output[start:start + length])
                else:
                    block = output[start:]
                    output.extend((block * ((length + distance - 1) // distance))[:length])
            if len(output) > limit:
                raise ValueError("LZ output exceeds safety bound")
    return bytes(output)


def compress(data):
    output = bytearray()
    cursor = 0
    while cursor < len(data):
        flag_at = len(output)
        output.append(0)
        flags = 0
        for bit in range(7, -1, -1):
            if cursor == len(data):
                break
            # Native references allow overlap, a 1024-byte window and 2..65 bytes.
            best_at = -1
            best_size = 1
            end = min(len(data), cursor + 65)
            search = cursor
            prefix = data[cursor:cursor + 2]
            while len(prefix) == 2:
                at = data.rfind(prefix, max(0, cursor - 1024), min(search + 1, len(data)))
                if at < 0:
                    break
                size = 2
                while cursor + size < end and data[at + size] == data[cursor + size]:
                    size += 1
                if size > best_size:
                    best_at, best_size = at, size
                    if cursor + size == end:
                        break
                search = at - 1
            if best_size >= 2:
                word = ((best_size - 2) << 10) | (1024 + best_at - cursor)
                output.extend(word.to_bytes(2, "little"))
                cursor += best_size
            else:
                flags |= 1 << bit
                output.append(data[cursor])
                cursor += 1
        output[flag_at] = flags
    return bytes(output)
