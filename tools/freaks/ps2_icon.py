"""Bounded PS2 browser-icon texture inspection; geometry/animation stay opaque."""

import struct

import numpy as np

from unpack_disc import require


class Icon:
    def __init__(self, data):
        require(len(data) >= 40, "Truncated PS2 icon")
        magic, shapes, flags, scale, vertices = struct.unpack_from("<5I", data)
        require(magic == 0x10000 and 0 < shapes <= 16 and vertices % 3 == 0 and flags & 4,
                "Unsupported PS2 icon profile")
        pos = 20 + vertices * (shapes * 8 + 16)
        require(pos + 20 <= len(data), "PS2 icon vertex extent")
        groups = struct.unpack_from("<I", data, pos + 16)[0]
        require(groups <= 1024, "PS2 icon animation count")
        pos += 20
        for _ in range(groups):
            require(pos + 8 <= len(data), "Truncated PS2 icon animation")
            keys = struct.unpack_from("<I", data, pos + 4)[0]
            pos += 8 + keys * 8
            require(pos <= len(data), "PS2 icon key extent")
        self.texture_offset = pos
        self.compressed = bool(flags & 8)
        if self.compressed:
            require(pos + 4 <= len(data), "Truncated PS2 icon RLE size")
            length = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            end, values = pos + length, []
            require(end == len(data), "PS2 icon RLE extent")
            while pos < end:
                require(pos + 2 <= end, "Truncated PS2 icon run")
                count = struct.unpack_from("<h", data, pos)[0]
                pos += 2
                require(count != 0, "Empty PS2 icon run")
                if count < 0:
                    require(pos - count * 2 <= end, "Truncated PS2 icon literals")
                    values.extend(struct.unpack_from(f"<{-count}H", data, pos))
                    pos -= count * 2
                else:
                    require(pos + 2 <= end, "Truncated PS2 icon repeated color")
                    value = struct.unpack_from("<H", data, pos)[0]
                    pos += 2
                    values.extend([value] * count)
                require(len(values) <= 16384, "PS2 icon RLE expands beyond 128x128")
            require(len(values) == 16384, "PS2 icon RLE pixel count")
            self.words = np.array(values, np.uint16).reshape(128, 128)
        else:
            require(pos + 32768 == len(data), "PS2 icon raw texture extent")
            self.words = np.frombuffer(data, "<u2", count=16384, offset=pos).reshape(128, 128).copy()
        self.data = data
        self.shapes, self.flags, self.vertices = shapes, flags, vertices

    def rgb(self):
        return np.stack([((self.words >> shift) & 31) * 255 // 31 for shift in (0, 5, 10)], axis=-1).astype(np.uint8)

    def replace_region(self, box, pixels):
        require(not self.compressed, "Editing compressed PS2 icons is not approved")
        x, y, w, h = box
        require(0 <= x < x + w <= 128 and 0 <= y < y + h <= 128
                and pixels.shape == (h, w, 3) and pixels.dtype == np.uint8, "PS2 icon edit dimensions")
        before = self.rgb()[y:y + h, x:x + w]
        changed = np.any(pixels != before, axis=2)
        words = self.words.copy()
        value = (pixels.astype(np.uint16) * 31 + 127) // 255
        packed = value[..., 0] | value[..., 1] << 5 | value[..., 2] << 10
        region = words[y:y + h, x:x + w]
        region[changed] = region[changed] & 0x8000 | packed[changed]
        data = self.data[:self.texture_offset] + words.astype("<u2").tobytes()
        require(len(data) == len(self.data), "PS2 icon size changed")
        return data
