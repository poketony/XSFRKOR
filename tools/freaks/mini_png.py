"""RGBA editor PNGs with optional, nonbinding game-palette reference data."""

import base64
import hashlib
from io import BytesIO
import json

from PIL.PngImagePlugin import PngInfo

from unpack_disc import require


KEY = "mini_palette_reference"
FORMAT = "FREAKS-MINI-PALETTE-REFERENCE-v1"


def rgba_png(image):
    indices = image.info.get("mini_palette_indices", image.info.get("mini_palette_reference_indices"))
    rgba = image.convert("RGBA")
    fingerprint = hashlib.sha256(rgba.tobytes()).hexdigest()
    authoritative = "mini_palette_indices" in image.info
    previous_fingerprint = image.info.get("mini_palette_reference_rgba_sha256")
    if indices is None and image.mode == "P" and image.info.get("mini_original_indices") == "v1":
        indices = image.tobytes()
        authoritative = True
    if indices is None and KEY in image.info:
        indices = palette_reference(image, 256)
        previous_fingerprint = reference_fingerprint(image)
    info = PngInfo()
    if indices is not None:
        require(isinstance(indices, bytes) and len(indices) == image.width * image.height,
                "MINI PNG palette reference has invalid dimensions")
        reference = {"format": FORMAT, "size": list(image.size),
                     "indices": base64.b64encode(indices).decode("ascii")}
        if authoritative or previous_fingerprint == fingerprint:
            reference["rgba_sha256"] = fingerprint
        info.add_text(KEY, json.dumps(reference, separators=(",", ":")), zip=True)
    buffer = BytesIO()
    rgba.save(buffer, format="PNG", pnginfo=info)
    return buffer.getvalue()


def palette_reference(image, colors):
    if KEY not in image.info:
        return None
    try:
        reference = json.loads(image.info[KEY])
        require(reference["format"] == FORMAT and reference["size"] == list(image.size),
                "MINI PNG palette reference identity/dimensions differ")
        indices = base64.b64decode(reference["indices"], validate=True)
        require(len(indices) == image.width * image.height and max(indices) < colors,
                "MINI PNG palette reference has invalid dimensions/range")
        return indices
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid MINI PNG palette reference: {error}") from error


def reference_fingerprint(image):
    if KEY not in image.info:
        return None
    value = json.loads(image.info[KEY]).get("rgba_sha256")
    require(value is None or isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value), "Invalid MINI RGBA fingerprint")
    return value
