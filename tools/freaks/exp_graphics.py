"""Selected EXP PNGs: bounded menu masks, map labels, JPEGs and ARX title."""

from functools import lru_cache
import io
import json
from pathlib import Path
import struct

import numpy as np
from PIL import Image, JpegImagePlugin

import exp_graphics_audit as gs
from font_tool import write_generated

from build_paths import DATA
from unpack_disc import require
from xeno_archive import compress_arx, decompress_arx


SELECTION = DATA / "exp/graphics-selection.json"
SELECTION_HASH = "6d4c16c4331205f7784ff1c7bdf894ea6e79e649aeb69db4113a204cc7eafe4d"
JPEG_LIMIT = 0x20000


@lru_cache(maxsize=1)
def selection():
    data = SELECTION.read_bytes()
    require(gs.sha(data) == SELECTION_HASH, "EXP graphics selection changed; revalidate its regions first")
    return json.loads(data)


def image_name(key):
    return key + ".png"


def catalogue():
    sources, regions = {}, {}
    for name, row in selection()["menu"].items():
        data = (gs.SOURCE / name).read_bytes()
        require(gs.sha(data) == row["sha256"], f"Original EXP menu differs: {name}")
        require(data[:4] == b"XTX\0" and len(data) == 262224
                and struct.unpack_from("<3I", data, 4) == (len(data), 1, 16), "EXP menu XTX header differs")
        require(struct.unpack_from("<4H3I", data, 16) == (256, 4, 256, 0, 0, 16386, 48),
                "EXP menu upload differs")
        sources[name] = data
        inverse = None
        if name.endswith("ctrl01.xtx"):
            y, x = np.indices((256, 256))
            pos = gs.ct32(x, y, 256)[..., None] * 4 + np.arange(4)
            inverse = np.full(262144, -1, np.int64)
            inverse[pos] = np.arange(80, len(data)).reshape(256, 256, 4)
        for rect in row["rectangles"]:
            x, y, w, h = rect["xywh"]
            yy, xx = np.indices((h, w)); xx, yy = xx + x, yy + y
            if inverse is not None:
                nib = gs.index4(xx, yy, 512)
                offsets, shifts, mask = inverse[nib // 2], (nib & 1) * 4, 15
            else:
                offsets, shifts, mask = 80 + gs.upload8(xx, yy, 512), np.zeros_like(xx), 255
            require(offsets.min() >= 80 and offsets.max() < len(data), "EXP menu pixels outside upload")
            indices = (np.frombuffer(data, np.uint8)[offsets] >> shifts) & mask
            require(indices.max() <= 15, "EXP menu region is not a 16-level text mask")
            region = {"source": name, "kind": "mask", "offsets": offsets, "shifts": shifts, "mask": mask,
                      "indices": indices, "pixels": indices.astype(np.uint8) * 17, "palette": None}
            regions.setdefault(rect["key"], []).append(region)
    for row in selection()["labels"]:
        name = row["path"]
        data = (gs.SOURCE / name).read_bytes()
        require(gs.sha(data) == row["sha256"], f"Original EXP label differs: {name}")
        sources[name] = data
        detail = next(d for p, d in gs.effect_images(data) if d["offset"] == row["offset"])
        require(detail == {k: v for k, v in row.items() if k not in ("id", "path", "sha256")}, "EXP label metadata differs")
        w, h, bpp = detail["width"], detail["height"], detail["bpp"]
        pos = detail["offset"] + 64
        ii = np.arange(w * h).reshape(h, w)
        offsets = pos + (ii // 2 if bpp == 4 else ii)
        shifts = (ii & 1) * 4 if bpp == 4 else np.zeros_like(ii)
        mask = 15 if bpp == 4 else 255
        indices = (np.frombuffer(data, np.uint8)[offsets] >> shifts) & mask
        pal = detail["palette_offset"]
        palette = gs.clut(data[pal:pal + 4 * (1 << bpp)], bpp)
        require(not palette[:, :3].any(), "EXP map label is not an alpha-only texture")
        key = Path(name).stem
        require(key not in regions, "EXP graphics key collision")
        regions[key] = [{"source": name, "kind": "mask", "offsets": offsets, "shifts": shifts, "mask": mask,
                         "indices": indices, "pixels": palette[indices, 3], "palette": palette[:, 3]}]
    require(len(regions) == 56 and sum(map(len, regions.values())) == 59, "EXP menu/label selection count differs")
    for row in selection()["raster"]:
        name = row["path"]
        data = (gs.SOURCE / name).read_bytes()
        require(gs.sha(data) == row["sha256"], f"Original EXP raster differs: {name}")
        if name.endswith(".jpg"):
            with Image.open(io.BytesIO(data)) as image:
                require(image.format == "JPEG" and image.size == (512, 448), "EXP source JPEG differs")
                pixels = np.array(image.convert("RGB"))
                sampling = JpegImagePlugin.get_sampling(image)
                require(sampling in (0, 1, 2), "EXP source JPEG sampling differs")
            kind = "jpeg"
        else:
            require(name == "yajima/titlefan.bin" and len(data) == 512 * 448 * 4, "EXP title dimensions differ")
            pixels = np.frombuffer(data, np.uint8).reshape(448, 512, 4).copy()
            require(pixels[..., 3].max() <= 128, "EXP title alpha exceeds PS2 normal range")
            pixels[..., 3] = pixels[..., 3].astype(np.uint16) * 255 // 128
            kind = "arx"
        sources[name] = data
        key = Path(name).stem
        require(key not in regions, "EXP raster key collides")
        regions[key] = [{"source": name, "kind": kind, "pixels": pixels,
                         **({"jpeg_sampling": sampling} if kind == "jpeg" else {})}]
    require(len(regions) == 60, "EXP raster selection count differs")
    used = {}
    for key, rows in regions.items():
        for row in rows:
            if row["kind"] != "mask":
                continue
            positions = row["offsets"] * 8 + row["shifts"]
            require(len(np.unique(positions)) == positions.size, f"EXP aliased pixels: {key}")
            addresses = set(positions.reshape(-1).tolist())
            prior = used.setdefault(row["source"], set())
            require(not addresses & prior, f"EXP text regions overlap: {key}")
            prior.update(addresses)
            require(np.array_equal(row["pixels"], rows[0]["pixels"]), "EXP shared text copies differ")
    return sources, regions


def mask_image(image, size):
    require(image.size == size, f"EXP PNG must remain {size[0]}x{size[1]}")
    if image.mode == "L":
        return np.array(image)
    rgba = np.array(image.convert("RGBA"))
    require(np.all(rgba[..., 3] == 255), "EXP masks must be opaque grayscale PNGs, not transparent RGBA")
    require(np.array_equal(rgba[..., 0], rgba[..., 1]) and np.array_equal(rgba[..., 0], rgba[..., 2]),
            "EXP text masks must be grayscale; color would alter the wrong palette indices")
    return rgba[..., 0]


def edit_pixels(image, region):
    pixels = region["pixels"]
    size = (pixels.shape[1], pixels.shape[0])
    require(image.size == size, f"EXP PNG must remain {size[0]}x{size[1]}")
    if region["kind"] == "mask":
        return mask_image(image, size)
    rgba = np.array(image.convert("RGBA"))
    if region["kind"] == "arx":
        require("A" in image.getbands() or "transparency" in image.info,
                "EXP title PNG must retain its alpha channel")
        return rgba
    require(np.all(rgba[..., 3] == 255), "EXP JPEG editor must be opaque RGB/RGBA")
    return rgba[..., :3]


def encode_jpeg(pixels, sampling=0):
    for quality in (95, 90, 85, 80):
        buffer = io.BytesIO()
        Image.fromarray(pixels).save(buffer, format="JPEG", quality=quality,
                                     subsampling=sampling, optimize=True, progressive=False)
        packed = buffer.getvalue()
        if (len(packed) + 2047) // 2048 * 2048 <= JPEG_LIMIT:
            with Image.open(io.BytesIO(packed)) as image:
                require(image.size == (512, 448) and not image.info.get("progressive")
                        and image.mode == "RGB" and JpegImagePlugin.get_sampling(image) == sampling,
                        "EXP encoded JPEG profile differs")
                decoded = np.array(image)
            return packed, {"jpeg_quality": quality, "jpeg_sampling": sampling, "jpeg_limit": JPEG_LIMIT,
                            "mean_channel_error": float(np.abs(decoded.astype(np.int16) - pixels).mean())}
    raise ValueError("EXP JPEG exceeds its 128KiB source window even at quality 80; reduce image complexity")


def extract(directory):
    directory = Path(directory) / "EXP"
    _, regions = catalogue()
    for key, rows in regions.items():
        pixels = rows[0]["pixels"]
        target = directory / image_name(key)
        if target.exists():
            with Image.open(target) as image:
                require(np.array_equal(edit_pixels(image, rows[0]), pixels),
                        f"Existing EXP base PNG preserved; use {target.stem}_KOR.png: {target}")
        else:
            out = io.BytesIO(); Image.fromarray(pixels).save(out, format="PNG")
            directory.mkdir(parents=True, exist_ok=True)
            write_generated(target, out.getvalue())
    print(f"[EXP GRAPHICS] {len(regions)} selected PNGs; existing files preserved")


def load_edits(directory):
    directory = Path(directory) / "EXP"
    if not directory.exists():
        return {}
    _, regions = catalogue()
    allowed = {(key + "_KOR.png").casefold(): key for key in regions}
    edits = {}
    for path in directory.iterdir():
        if not path.is_file() or not path.name.casefold().endswith("_kor.png"):
            continue
        require(path.name.casefold() in allowed, f"Unknown EXP edited PNG: {path.name}")
        key = allowed[path.name.casefold()]
        require(key not in edits, f"Duplicate EXP edit: {key}")
        with Image.open(path) as image:
            require(image.format == "PNG", "Edited EXP file must be PNG")
            edits[key] = image.copy()
    return edits


def plan(edits):
    sources, regions = catalogue()
    require(set(edits) <= set(regions), "EXP edited image is not in the verified selection")
    patched = {name: bytearray(data) for name, data in sources.items()}
    changes = []
    for key, image in edits.items():
        region = regions[key][0]
        original = region["pixels"]
        pixels = edit_pixels(image, region)
        dirty = pixels != original
        if not dirty.any():
            continue
        if region["kind"] != "mask":
            if region["kind"] == "jpeg":
                packed, quality = encode_jpeg(pixels, region["jpeg_sampling"])
            else:
                raw = np.frombuffer(sources[region["source"]], np.uint8).reshape(448, 512, 4).copy()
                rgba = pixels.copy()
                rgba[..., 3] = (rgba[..., 3].astype(np.uint16) * 128 + 127) // 255
                changed = np.any(dirty, axis=2)
                raw[changed] = rgba[changed]
                packed = compress_arx(raw.tobytes())
                require((len(packed) + 2047) // 2048 * 2048 <= len(raw.tobytes()),
                        "EXP ARX packed input would precede its decoded allocation")
                require(decompress_arx(packed, raw.size) == raw.tobytes(), "EXP title ARX re-extraction differs")
                quality = {"decoded_size": raw.size, "alpha_range": [0, 128], "decoded_sha256": gs.sha(raw.tobytes())}
            patched[region["source"]] = bytearray(packed)
            changes.append({"image": key, "kind": region["kind"], "old_size": len(sources[region["source"]]),
                            "new_size": len(packed), **quality})
            continue
        palette = regions[key][0]["palette"]
        if palette is None:
            indices = ((pixels.astype(np.uint16) + 8) // 17).astype(np.uint8)
            quantized = indices * 17
        else:
            indices = np.argmin(np.abs(pixels[..., None].astype(np.int16) - palette.astype(np.int16)), axis=-1).astype(np.uint8)
            quantized = palette[indices]
        written = 0
        for row in regions[key]:
            values = row["indices"].copy()
            values[dirty] = indices[dirty]
            output = np.frombuffer(patched[row["source"]], np.uint8)
            off, shift = row["offsets"][dirty], row["shifts"][dirty]
            bits = row["mask"] << shift
            # Both nibbles may be edited in one byte; update each plane separately.
            for s in np.unique(shift):
                take = shift == s
                output[off[take]] = ((output[off[take]] & (255 ^ bits[take])) | (values[dirty][take].astype(np.uint16) << s)).astype(np.uint8)
            reread = (output[row["offsets"]] >> row["shifts"]) & row["mask"]
            require(np.array_equal(reread, values), "EXP rebuilt image re-extraction differs")
            written += int(np.count_nonzero(values != row["indices"]))
        changes.append({"image": key, "copies": len(regions[key]), "edited_pixels": int(dirty.sum()),
                        "changed_indices": written,
                        "max_quantization_error": int(np.abs(quantized[dirty].astype(np.int16) - pixels[dirty]).max())})
    outputs = {"EXP/FILES/" + name: bytes(data) for name, data in patched.items() if bytes(data) != sources[name]}
    mask_sources = {row["source"] for rows in regions.values() for row in rows if row["kind"] == "mask"}
    require(all(len(data) == len(sources[name.removeprefix("EXP/FILES/")]) for name, data in outputs.items()
                if name.removeprefix("EXP/FILES/") in mask_sources),
            "EXP graphics file length changed")
    return outputs, {"changed": changes, "selected_images": len(regions),
                     "mask_file_sizes_palettes_and_uvs_preserved": True,
                     "scope": "Masks re-extracted; JPEG fixed dimensions/source window; title ARX roundtrip; no GPU"}
