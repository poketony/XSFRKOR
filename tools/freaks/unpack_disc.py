"""Hash original inputs and read the ISO9660 file allocations."""
import hashlib
from pathlib import PurePosixPath




CHUNK = 4 << 20


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_hash(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(CHUNK):
            result.update(block)
    return result.hexdigest()


def safe_path(root, name):
    parts = PurePosixPath(name).parts
    require(parts and not name.startswith("/") and ":" not in name
            and "\\" not in name and all(p not in ("", ".", "..") for p in parts),
            f"Unsafe archive name: {name}")
    target = (root / name).resolve()
    require(target.is_relative_to(root.resolve()), f"Path escapes output: {name}")
    return target


def iso_records(path):
    """Walk ISO9660 independently of pycdlib and check both-endian fields."""
    size = path.stat().st_size
    result = {}
    visited = set()

    def both(data, offset, width):
        little = int.from_bytes(data[offset:offset + width], "little")
        big = int.from_bytes(data[offset + width:offset + 2 * width], "big")
        require(little == big, "ISO endianness mismatch")
        return little

    with path.open("rb") as stream:
        stream.seek(16 * 2048)
        pvd = stream.read(2048)
        require(pvd[:7] == b"\x01CD001\x01", "Wrong ISO primary volume")
        require(both(pvd, 128, 2) == 2048 and both(pvd, 80, 4) * 2048 == size,
                "Unexpected ISO sector or volume size")

        def walk(offset, count, parent):
            require((offset, count) not in visited and len(visited) < 1000,
                    "Cyclic or excessive ISO directory tree")
            visited.add((offset, count))
            require(offset + count <= size and count <= 1 << 20, "Bad directory extent")
            stream.seek(offset)
            data = stream.read(count)
            cursor = 0
            while cursor < count:
                length = data[cursor]
                if not length:
                    cursor = (cursor // 2048 + 1) * 2048
                    continue
                require(length >= 34 and cursor + length <= count
                        and cursor % 2048 + length <= 2048, "Invalid ISO directory record")
                record = data[cursor:cursor + length]
                cursor += length
                require(record[32] + 33 <= length, "Truncated ISO filename")
                raw_name = record[33:33 + record[32]]
                if raw_name in (b"\0", b"\1"):
                    continue
                require(not record[1] and not record[26] and not record[27]
                        and not record[25] & 128, "Unsupported extended ISO allocation")
                name = raw_name.decode("ascii").removesuffix(";1")
                require(name not in ("", ".", "..") and "/" not in name
                        and "\\" not in name, "Unsafe ISO name")
                start, length = both(record, 2, 4) * 2048, both(record, 10, 4)
                require(start + length <= size, "File outside ISO")
                full = parent + name
                if record[25] & 2:
                    walk(start, length, full + "/")
                else:
                    require(full not in result, "Duplicate ISO path")
                    result[full] = (start // 2048, length)

        walk(both(pvd, 158, 4) * 2048, both(pvd, 166, 4), "")
    allocations = sorted((s * 2048, s * 2048 + n) for s, n in result.values() if n)
    require(all(a[1] <= b[0] for a, b in zip(allocations, allocations[1:])),
            "Overlapping ISO files")
    return result
