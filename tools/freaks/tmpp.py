"""Freaks TM++ indexed image conversion; retain storage dimensions and header."""

import struct

from PIL import Image

from adv_format import require


def clut_index(index):
    return (index & ~0x18) | (index & 8) << 1 | (index & 16) >> 1


class Tmpp:
    def __init__(self, data):
        require(len(data) >= 128 and data[:4] == b"TM++", "Not a TM++ image")
        self.data = data
        self.header = struct.unpack_from("<32I", data)
        require(self.header[1:3] == (0, 1), "Unexpected TM++ version")
        self.format = self.header[3]
        self.width, self.height = self.header[7:9]
        self.strip_width = self.header[9]
        require(0 < self.width <= 8192 and 0 < self.height <= 8192, "Unexpected TM++ dimensions")
        require(self.format in (2, 3), "Unsupported TM++ pixel format")
        require(0 < self.strip_width <= self.width, "Unexpected TM++ vertical strip width")
        palette_bytes = 1024 if self.format == 3 else 0
        require(len(data) == 128 + palette_bytes + self.width * self.height, "TM++ size differs")
        self.pixels = data[128 + palette_bytes:]

    def linear_indices(self):
        result = bytearray(len(self.pixels))
        offset = 0
        for x in range(0, self.width, self.strip_width):
            stride = min(self.strip_width, self.width - x)
            for y in range(self.height):
                at = y * self.width + x
                result[at:at + stride] = self.pixels[offset + y * stride:offset + (y + 1) * stride]
            offset += stride * self.height
        return bytes(result)

    def store_indices(self, pixels):
        require(len(pixels) == self.width * self.height, "Wrong TM++ pixel count")
        result = bytearray()
        for x in range(0, self.width, self.strip_width):
            stride = min(self.strip_width, self.width - x)
            for y in range(self.height):
                at = y * self.width + x
                result.extend(pixels[at:at + stride])
        return bytes(result)

    def palette(self):
        require(self.format == 3, "TM++ format 2 uses an external palette; not editable yet")
        result = []
        for index in range(256):
            at = 128 + clut_index(index) * 4
            r, g, b, alpha = self.data[at:at + 4]
            result.append((r, g, b, min(255, alpha * 2)))
        return result

    def image(self):
        palette = self.palette()
        rgba = bytes(value for index in self.linear_indices() for value in palette[index])
        return Image.frombytes("RGBA", (self.width, self.height), rgba)

    def replace(self, image, *, preserve_palette=False):
        require(image.size == (self.width, self.height),
                f"PNG canvas must remain {self.width}x{self.height}, including right padding")
        image = image.convert("RGBA")
        original = self.image()
        if image.tobytes() == original.tobytes():
            return self.data, {"method": "original-byte-copy", "max_error": 0, "mean_error": 0}
        palette = self.palette()
        color_to_index = {}
        for i, color in enumerate(palette):
            color_to_index.setdefault(color, i)
        pixels = list(image.getdata())
        if all(color in color_to_index for color in pixels):
            indices = bytes(color_to_index[color] for color in pixels)
            output = self.data[:1152] + self.store_indices(indices)
            method = "original-palette-exact"
        elif preserve_palette:
            def premultiplied(color):
                r, g, b, a = color
                return r * a, g * a, b * a, a * 255

            targets = [premultiplied(color) for color in palette]
            for color in dict.fromkeys(pixels):
                if color in color_to_index:
                    continue
                sample = premultiplied(color)
                color_to_index[color] = min(range(256), key=lambda i: sum(
                    (a - b) ** 2 for a, b in zip(sample, targets[i])))
            indices = bytes(color_to_index[color] for color in pixels)
            output = self.data[:1152] + self.store_indices(indices)
            method = "original-palette-nearest-rgba"
        else:
            reduced = image.quantize(colors=256, method=Image.Quantize.FASTOCTREE,
                                     dither=Image.Dither.NONE)
            colors = reduced.getpalette("RGBA")
            require(len(colors) == 1024, "Quantizer did not return 256 RGBA entries")
            stored_palette = bytearray(1024)
            for index in range(256):
                r, g, b, alpha = colors[index * 4:index * 4 + 4]
                at = clut_index(index) * 4
                stored_palette[at:at + 4] = bytes((r, g, b, min(128, (alpha + 1) // 2)))
            output = self.data[:128] + bytes(stored_palette) + self.store_indices(reduced.tobytes())
            method = "rgba-fast-octree-256"
        rebuilt = Tmpp(output)
        require(rebuilt.header == self.header, "TM++ header changed")
        errors = [abs(a - b) for a, b in zip(image.tobytes(), rebuilt.image().tobytes())]
        return output, {"method": method, "max_error": max(errors), "mean_error": sum(errors) / len(errors)}
