"""Decode Freaks GRP and MINI WWROM members."""

import hashlib
import re
import struct

from lzss import decompress as lz_decode
from ulz import decompress as ulz_decode

from build_paths import DATA, REPO
from unpack_disc import require


def digest(data):
    return hashlib.sha256(data).hexdigest()


def grp_parts(data):
    # These streams have no inter-frame padding. Validate every candidate's
    # decoded header/length; indexed variants additionally supply exact bounds.
    starts = [m.start() for m in re.finditer(re.escape(b"\xfaTM++"), data)]
    require(starts, "No TM++ streams in GRP")
    indexed = starts[0] != 0
    if indexed:
        count = struct.unpack_from("<I", data)[0]
        require(count == len(starts) and starts[0] == 4 + count * 8,
                "Unexpected GRP directory")
        entries = [struct.unpack_from("<II", data, 4 + i * 8) for i in range(count)]
        require(entries == [(s, e - s) for s, e in zip(starts, starts[1:] + [len(data)])],
                "GRP directory disagrees with streams")
    for index, (start, end) in enumerate(zip(starts, starts[1:] + [len(data)])):
        decoded = lz_decode(data[start:end], limit=32 << 20)
        require(len(decoded) >= 128 and decoded[:4] == b"TM++", "Invalid TM++ image")
        header = struct.unpack_from("<12I", decoded)
        width, height = header[7:9]
        require(header[1:3] == (0, 1) and 0 < width <= 8192 and 0 < height <= 8192,
                "Unexpected TM++ header")
        palette = {2: 0, 3: 1024}.get(header[3])
        require(palette is not None and len(decoded) == 128 + palette + width * height,
                f"TM++ format/decoded size needs investigation: member {index}")
        yield {"index": index, "offset": start, "stored_size": end - start,
               "width": width, "height": height, "format": header[3],
               "palette_bytes": palette, "indexed_container": indexed}, decoded


def elf_offset(data, address):
    require(data[:6] == b"\x7fELF\x01\x01", "Expected ELF32 little-endian")
    phoff = struct.unpack_from("<I", data, 28)[0]
    stride, count = struct.unpack_from("<HH", data, 42)
    for i in range(count):
        kind, offset, va, _, size, _, _, _ = struct.unpack_from("<8I", data, phoff + i * stride)
        if kind == 1 and va <= address < va + size:
            return offset + address - va
    raise ValueError(f"Address not file-backed: {address:#x}")


def wwrom_parts(elf, data):
    # MINI's wwrom functions use this 20-byte table, not a signature scan.
    table = elf_offset(elf, 0x1bfb58)
    count = struct.unpack_from("<I", elf, elf_offset(elf, 0x1bfb50))[0]
    require(count == 22, "Unexpected WWROM table count")
    last_end = 0
    names = set()
    for index in range(count):
        offset, size, sector, decoded_size, name_va = struct.unpack_from("<5I", elf, table + index * 20)
        name_at = elf_offset(elf, name_va)
        name = elf[name_at:].split(b"\0", 1)[0].decode("ascii")
        require(name.startswith("../") and ".." not in name[3:].split("/"), "Unsafe WWROM debug path")
        name = name[3:]
        require(name.casefold() not in names and offset == sector * 2048
                and last_end <= offset and offset + size <= len(data), "Invalid WWROM allocation")
        names.add(name.casefold())
        raw = data[offset:offset + size]
        if decoded_size:
            decoded, streams = ulz_decode(raw)
            require(len(decoded) == decoded_size, f"WWROM decoded size mismatch: {name}")
            require(streams["references_end"] <= size
                    and size - streams["references_end"] <= 3, "Unexpected Ulz trailing data")
        else:
            decoded, streams = raw, None
        yield {"index": index, "name": name, "offset": offset, "stored_size": size,
               "stored_sha256": digest(raw), "codec": "Ulz" if decoded_size else "raw",
               "streams": streams}, decoded
        last_end = (offset + size + 2047) // 2048 * 2048
