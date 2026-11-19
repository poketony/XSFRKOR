"""Read-only EXP graphics inventory and bounded contact-sheet review."""

import argparse
from collections import defaultdict
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import struct

import numpy as np
from PIL import Image, ImageDraw


from build_paths import DATA, REPO, WORK


SOURCE = REPO / "decompressed/EXP/XENOSAGA"
OUT = WORK / "evidence/exp-graphics"
BLOCK32 = np.array([0, 1, 4, 5, 16, 17, 20, 21, 2, 3, 6, 7, 18, 19, 22, 23,
                    8, 9, 12, 13, 24, 25, 28, 29, 10, 11, 14, 15, 26, 27, 30, 31])
BLOCK4 = np.array([0, 2, 8, 10, 1, 3, 9, 11, 4, 6, 12, 14, 5, 7, 13, 15,
                   16, 18, 24, 26, 17, 19, 25, 27, 20, 22, 28, 30, 21, 23, 29, 31])
WORD32 = np.array([0, 1, 4, 5, 8, 9, 12, 13, 2, 3, 6, 7, 10, 11, 14, 15])
NAMES = re.compile(r"moji|kanban|kannban|sign|font|text|logo|poster|koukoku|menu|title|"
                   r"kanji|kanzi|book|shop|letter|noren|japan|jpn|banner|shinbun|"
                   r"sinbun|^hon|_hon|hyouji|hyousi|hyoshi|tehai|label|rogo|paper", re.I)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pixel_sha(pixels):
    return sha(struct.pack("<3I", *pixels.shape) + pixels.tobytes())


def ct32(x, y, width=512):
    block = BLOCK32[((x & 63) >> 3) + ((y & 31) >> 3) * 8]
    word = WORD32[(x & 7) + (y & 1) * 8]
    return ((y >> 5) * (width >> 6) + (x >> 6)) * 2048 + block * 64 + ((y & 7) >> 1) * 16 + word


def upload8(x, y, width):
    return ((y & ~15) * width + (x & ~15) * 2
            + ((((y & ~3) >> 1) + (y & 1)) & 7) * width * 2
            + ((x + (((y + 2) >> 2 & 1) * 4)) & 7) * 4
            + ((y >> 1) & 1) + ((x >> 2) & 2))


def index8(x, y):
    src = upload8(x, y, 1024)
    return ct32((src // 4) % 512, src // 2048) * 4 + src % 4


def index4(x, y, width=1024):
    block = BLOCK4[((x & 127) >> 5) + ((y & 127) >> 4) * 4]
    col = (y & 15) >> 2
    word = ((x & 1) + ((x & 6) << 1) + ((y & 1) << 1)) ^ ((((y >> 1) ^ col) & 1) << 3)
    nibble = ((x >> 3) & 3) * 2 + ((y >> 1) & 1)
    return (((y >> 7) * (width // 128) + (x >> 7)) * 2048 + block * 64 + col * 16 + word) * 8 + nibble


def alpha(pixels, normalized=False):
    result = pixels.copy()
    values = result[..., 3].astype(np.uint16)
    if normalized:
        if values.max(initial=0) <= 128:
            result[..., 3] = values * 255 // 128
    else:
        result[..., 3] = np.minimum(values * 2, 255)
    return result


def clut(raw, bits, convert_alpha=True):
    p = np.frombuffer(raw, np.uint8).reshape(1 << bits, 4).copy()
    if bits == 8:
        i = np.arange(256)
        p = p[(i & ~24) | ((i & 8) << 1) | ((i & 16) >> 1)]
    return alpha(p) if convert_alpha else p


def effect_blocks(data, base=0):
    require(len(data) >= 64 and struct.unpack_from("<I", data)[0] == len(data), "Effect length")
    pos, rows = 64, []
    while pos < len(data):
        require(pos + 64 <= len(data), "Truncated effect header")
        size = struct.unpack_from("<I", data, pos)[0]
        if size == 0 and not any(data[pos:]):
            break
        require(size >= 64 and pos + size <= len(data), "Effect block extent")
        fields = struct.unpack_from("<6H", data, pos + 8)
        name = data[pos + 20:pos + 64].split(b"\0", 1)[0].decode("ascii")
        rows.append({"offset": base + pos, "size": size, "fields": list(fields), "name": name})
        pos += size
    yield rows
    for row in rows:
        if row["fields"][0] == 10:
            start = row["offset"] - base + 64
            yield from effect_blocks(data[start:start + row["size"] - 64], base + start)


def effect_images(data):
    for rows in effect_blocks(data):
        palettes = defaultdict(list)
        for r in rows:
            if r["fields"][0] == 3:
                palettes[r["name"]].append(r)
        for r in rows:
            kind, rid, w, h, bpp, flags = r["fields"]
            if kind not in (4, 7):
                continue
            detail = {"offset": r["offset"], "name": r["name"], "width": w, "height": h,
                      "bpp": bpp, "flags": flags}
            candidates = palettes[r["name"][:-4] + ".clt"]
            if (not r["name"].endswith(".bmp") or bpp not in (4, 8) or flags not in (0, 1)
                    or not 0 < w <= 4096 or not 0 < h <= 4096 or w % 2):
                yield None, {**detail, "error": "Unsupported effect image profile"}
                continue
            if len(candidates) != 1:
                yield None, {**detail, "error": "Missing/ambiguous same-container palette"}
                continue
            pal = candidates[0]
            length = w * h * bpp // 8
            require(r["size"] - 64 == (length + 63) // 64 * 64, "Effect pixels extent")
            require(pal["size"] - 64 == 4 * (1 << bpp) and pal["fields"][4] == bpp, "Effect CLUT extent")
            raw = np.frombuffer(data, np.uint8, count=length, offset=r["offset"] + 64)
            if bpp == 4:
                raw = np.column_stack((raw & 15, raw >> 4)).reshape(-1)
            palette = clut(data[pal["offset"] + 64:pal["offset"] + pal["size"]], bpp)
            pixels = palette[raw.reshape(h, w)]
            yield pixels, {**detail, "palette_offset": pal["offset"] + 64}


def txy_images(data):
    require(data[:4] == b"txy\0" and struct.unpack_from("<I", data, 8)[0] == len(data), "TXY header")
    count = struct.unpack_from("<I", data, 4)[0]
    require(0 < count <= 32, "TXY bank count")
    offsets = struct.unpack_from(f"<{count}I", data, 16)
    for bank, off in enumerate(offsets):
        hdr = struct.unpack_from("<8I", data, off)
        require(hdr[:5] == (64, 16, 512, 128, 0) and hdr[7] == 0 and hdr[5] <= 8, "TXY bank header")
        memory = np.zeros(512 * 1024, np.uint8)
        written = np.zeros(len(memory), bool)
        for j in range(hdr[5]):
            addr, base, w, h, pad, qwc, blocks, flags = struct.unpack_from("<8I", data, off + 32 + j * 32)
            require(pad == 0 and 0 < w <= 512 and h in (8, 16, 24, 32)
                    and qwc == w * h // 4 + 2 and addr + 32 + w * h * 4 <= len(data), "TXY upload")
            yy, xx = np.indices((h, w))
            positions = (ct32(xx, yy) * 4 + base * 256)[..., None] + np.arange(4)
            require(positions.max() < len(memory), "TXY upload GS extent")
            memory[positions] = np.frombuffer(data, np.uint8, count=w * h * 4, offset=addr + 32).reshape(h, w, 4)
            written[positions] = True
        seen = set()
        for i in range(hdr[6]):
            pos = off + 288 + i * 96
            v = struct.unpack_from("<16I", data, pos)
            if v[12] != bank or v[:7] + v[8:10] in seen:
                continue
            seen.add(v[:7] + v[8:10])
            tbp, psm, w, h, x, y, cbp, flag, px, py = v[:10]
            name = data[pos + 64:pos + 96].split(b"\0", 1)[0].decode("cp932", errors="replace")
            d = {"name": name, "bank": bank, "material": i, "record": pos,
                 "psm": psm, "region": [x, y, w, h], "clut": [px, py]}
            if not (0 < w <= 1024 and 0 < h <= 1024 and x + w <= 1024 and y + h <= 1024):
                yield None, {**d, "error": "Placeholder/invalid texture dimensions"}
                continue
            yy, xx = np.indices((h, w)); xx, yy = xx + x, yy + y
            if psm == 19:
                indices = index8(xx, yy); origin = int(index8(np.array(x), np.array(y)))
                lookup = memory[indices]
                cy, cx = np.indices((16, 16))
            elif psm == 20:
                nib = index4(xx, yy); indices = nib // 2
                origin = int(index4(np.array(x), np.array(y))) // 2
                lookup = (memory[indices] >> ((nib & 1) * 4)) & 15
                cy, cx = np.indices((2, 8))
            elif psm == 0:
                indices = ct32(xx, yy) * 4; origin = int(ct32(x, y)) * 4
                lookup = None
            else:
                yield None, {**d, "error": "Unsupported PSM"}
                continue
            if origin != tbp * 256 or indices.max() >= len(memory) or not written[indices].all():
                yield None, {**d, "error": "Texture address/unloaded byte mismatch"}
                continue
            if psm == 0:
                palpos = indices[..., None] + np.arange(4)
                if palpos.max() >= len(memory) or not written[palpos].all():
                    yield None, {**d, "error": "RGBA reads unloaded bytes"}
                    continue
                pixels = alpha(memory[palpos], normalized=True)
            else:
                palpos = ct32(cx + px, cy + py)[..., None] * 4 + np.arange(4)
                if int(ct32(px, py)) * 4 != cbp * 256 or palpos.max() >= len(memory) or not written[palpos].all():
                    yield None, {**d, "error": "CLUT address/unloaded byte mismatch"}
                    continue
                pixels = alpha(clut(memory[palpos].tobytes(), 8 if psm == 19 else 4,
                                    convert_alpha=False)[lookup], normalized=True)
            yield pixels, d


def xtx_views(data):
    require(data[:4] == b"XTX\0" and struct.unpack_from("<I", data, 4)[0] == len(data), "XTX header")
    count, off = struct.unpack_from("<2I", data, 8)
    require(0 < count <= 32, "XTX upload count")
    headers = [struct.unpack_from("<4H3I", data, off + i * 20) for i in range(count)]
    width = (headers[0][1] or 8) * 64
    canvas = np.zeros((512, width, 4), np.uint8)
    memory = np.zeros(4 * 1024 * 1024, np.uint8)
    bottom = 0
    for w, bw, h, pad, base, qwc, addr in headers:
        require(w > 0 and h > 0 and pad == 0 and qwc == 2 + w * h // 4
                and addr + 32 + w * h * 4 <= len(data), "XTX upload extent")
        x, y = (base // 4096 % ((bw or 8) // 2)) * 64, (base // 4096 // ((bw or 8) // 2)) * 32
        require(width == (bw or 8) * 64 and x + w <= width and y + h <= 512, "XTX audit atlas dimensions")
        bottom = max(bottom, y + h)
        pixels = np.frombuffer(data, np.uint8, count=w * h * 4, offset=addr + 32).reshape(h, w, 4)
        canvas[y:y + h, x:x + w] = pixels
        yy, xx = np.indices((h, w))
        memory[ct32(xx + x, yy + y, width)[..., None] * 4 + np.arange(4)] = pixels
    yy, xx = np.indices((((bottom + 31) // 32) * 64, width * 2))
    indices8 = canvas.reshape(-1)[upload8(xx, yy, width * 2)]
    yield np.repeat(np.minimum(indices8.astype(np.uint16) * 17, 255).astype(np.uint8)[..., None], 3, axis=2), {"name": "PSMT8-index-preview"}
    yy, xx = np.indices((1024, 512))
    nib = index4(xx, yy, 512)
    indices4 = ((memory[nib // 2] >> ((nib & 1) * 4)) & 15).astype(np.uint8) * 17
    yield np.repeat(indices4[..., None], 3, axis=2), {"name": "PSMT4-index-preview"}
    if count == 1 and 80 <= w < 128 and h >= 16:
        yy, xx = np.indices((h * 2, 128))
        indexed = canvas.reshape(-1)[upload8(xx, yy, 1024)]
        palette = alpha(clut(canvas[:16, 64:80].tobytes(), 8, convert_alpha=False), normalized=True)
        yield palette[indexed], {"name": "palette64-candidate"}


def decode(kind, data):
    return {"effects": effect_images, "txy": txy_images, "xtx": xtx_views}[kind](data)


def scan(kind):
    suffixes = {"effects": (".esd", ".esp"), "txy": (".txy",), "xtx": (".xtx",)}[kind]
    files, unique, unresolved = [], {}, []
    for p in sorted(SOURCE.rglob("*")):
        if p.suffix not in suffixes:
            continue
        data = p.read_bytes(); rel = p.relative_to(SOURCE).as_posix()
        row = {"path": rel, "bytes": len(data), "sha256": sha(data), "images": []}
        for pixels, detail in decode(kind, data):
            location = {"path": rel, **detail}
            if pixels is None:
                unresolved.append(location)
                continue
            digest = pixel_sha(pixels)
            if digest not in unique:
                unique[digest] = {"id": len(unique), "sha256": digest, "shape": list(pixels.shape), "locations": []}
            u = unique[digest]; u["locations"].append(location); row["images"].append(u["id"])
        files.append(row)
    report = {"kind": kind, "files": files, "images": list(unique.values()), "unresolved": unresolved}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (kind + ".json.gz")).write_bytes(gzip.compress(json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0))
    print(f"[SCAN] {kind}: {len(files)} files; {len(unique)} unique images; {len(unresolved)} unresolved")
    return report


def read_index(kind):
    return json.loads(gzip.decompress((OUT / (kind + ".json.gz")).read_bytes()))


def get_pixels(kind, row):
    loc = row["locations"][0]
    data = (SOURCE / loc["path"]).read_bytes()
    for pixels, detail in decode(kind, data):
        if detail == {k: v for k, v in loc.items() if k != "path"}:
            require(pixels is not None and pixel_sha(pixels) == row["sha256"], "Review source/pixels changed")
            return pixels
    raise ValueError("Review image no longer found")


def pxy_layout(data):
    require(len(data) >= 0x74 and data[:4] == b"pxy\0"
            and struct.unpack_from("<I", data, 12)[0] == len(data), "PXY header")
    count, table, tail = struct.unpack_from("<3I", data, 0x38)
    ntail, stream, draws, groups, materials, bones, parts, matrices, partmat, names, inverse = struct.unpack_from("<11I", data, 0x48)
    require(materials + count * 96 <= names, "PXY material extent")
    require(names + parts * 112 == matrices and matrices + bones * 64 == inverse
            and inverse + bones * 64 == partmat and partmat + parts * 64 == stream,
            "PXY name/matrix extents")
    require(stream <= table <= tail and table + draws * 32 == tail
            and tail + 16 + ntail * 96 <= len(data), "PXY stream/table extents")
    end = stream
    for i in range(draws):
        start, qwords = struct.unpack_from("<2I", data, table + i * 32)
        require(start == end and start + qwords * 16 <= table, "PXY draw stream")
        end = start + qwords * 16
    require(end == table, "PXY stream end")
    return {"materials": count, "material_stride": 96,
            "material_padding": names - materials - count * 96,
            "bones": bones, "parts": parts, "draw_streams": draws,
            "draw_bytes": table - stream, "tail_records": ntail,
            "uninterpreted_tail_bytes": len(data) - tail - 16 - ntail * 96}


def bundle_members(data):
    table = struct.unpack_from("<I", data)[0]
    require(0 < table <= 65536 and table % 4 == 0, "Bundle table")
    base = (table + 4 + 63) // 64 * 64
    start = base
    for i, end in enumerate(struct.unpack_from(f"<{table // 4}I", data, 4)):
        end += base
        require(start < end <= len(data), "Bundle member extent")
        yield i, start, data[start:end]
        start = (end + 63) // 64 * 64
    require(start == len(data), "Bundle end")


def titlebg_images():
    data = (SOURCE / "yajima/titlebg.bin").read_bytes()
    known = {r["sha256"] for r in read_index("txy")["images"]}
    for member, off, raw in bundle_members(data):
        if raw[:4] != b"txy\0":
            continue
        length = struct.unpack_from("<I", raw, 8)[0]
        require(not any(raw[length:]), "Titlebg TXY member padding")
        for pixels, detail in txy_images(raw[:length]):
            require(pixels is not None, f"Titlebg unresolved: {detail}")
            digest = pixel_sha(pixels)
            if digest not in known:
                known.add(digest)
                yield pixels, {"member": member, "member_offset": off, **detail}


def miscellaneous_images():
    for p in sorted(SOURCE.rglob("*.jpg")):
        with Image.open(p) as im:
            yield p.relative_to(SOURCE).as_posix(), np.array(im.convert("RGB"))
    for i, off, raw in bundle_members((SOURCE / "credit.bin").read_bytes()):
        with Image.open(io.BytesIO(raw)) as im:
            yield f"credit.bin #{i}", np.array(im.convert("RGB"))
    raw = (SOURCE / "yajima/titlefan.bin").read_bytes()
    require(len(raw) == 512 * 448 * 4, "Titlefan dimensions")
    yield "yajima/titlefan.bin", np.frombuffer(raw, np.uint8).reshape(448, 512, 4)


def door_images(data):
    require(len(data) == 115568 and struct.unpack_from("<I", data)[0] == 18, "Door image bundle differs")
    offsets = struct.unpack_from("<18I", data, 4)
    require(offsets == tuple(80 + i * 6416 for i in range(18)), "Door member boundaries differ")
    for i, off in enumerate(offsets):
        raw = data[off:off + 6416]
        require(raw[:4] == b"XTX\0" and struct.unpack_from("<3I", raw, 4) == (6416, 1, 16)
                and struct.unpack_from("<4H3I", raw, 16) == (48, 8, 33, 0, 0, 398, 48),
                "Door XTX upload differs")
        canvas = np.zeros((64, 512, 4), np.uint8)
        loaded = np.zeros_like(canvas, bool)
        canvas[:33, :48] = np.frombuffer(raw, np.uint8, offset=80).reshape(33, 48, 4)
        loaded[:33, :48] = True
        y, x = np.indices((64, 64))
        addresses = upload8(x, y, 1024)
        require(loaded.reshape(-1)[addresses].all(), "Door pixels read unloaded upload bytes")
        require(loaded[:16, 32:48].all(), "Door CLUT reads unloaded upload bytes")
        palette = alpha(clut(canvas[:16, 32:48].tobytes(), 8, convert_alpha=False), normalized=True)
        require(np.any(palette[..., :3]), "Door CLUT unexpectedly empty")
        pixels = palette[canvas.reshape(-1)[addresses]]
        yield pixels, {"member": i, "offset": off, "stored_bytes": len(raw),
                       "dimensions": [64, 64], "palette_xy": [32, 0]}


def review_doors():
    path = SOURCE / "iwase/img/door/doorimg.dat"
    data = path.read_bytes()
    source_rows = json.loads((DATA / "contents-report.json").read_text(encoding="utf-8"))["members"]
    expected = next(r["output"]["sha256"] for r in source_rows
                    if r["output"]["path"] == path.relative_to(REPO).as_posix())
    require(sha(data) == expected, "Original door image bundle hash differs")
    images = list(door_images(data))
    overview([(f"Door {d['member']:02} / CLUT32", np.repeat(np.repeat(p, 3, axis=0), 3, axis=1))
              for p, d in images], OUT / "door-reviewed.png", columns=6, tile=(210, 225))
    report = {"source": "iwase/img/door/doorimg.dat", "source_sha256": sha(data),
              "self_contained_palette": True, "palette_xy": [32, 0],
              "members": [{**d, "pixels_sha256": pixel_sha(p)} for p, d in images],
              "visual_review": "18 door/location thumbnails; no identifiable Japanese lettering found",
              "old_black_preview": "CLUT x=64 was outside each 48px upload; rejected, not proof of blank images",
              "actual_uv_or_game_scene_verified": False, "editable_pipeline_added": False}
    (OUT / "door-review.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("[DOORS] 18 self-contained 64x64 pictures; loaded CLUT at (32,0); no new editor added")


def overview(items, path, columns=3, tile=(520, 420)):
    canvas = Image.new("RGB", (columns * tile[0], ((len(items) + columns - 1) // columns) * tile[1]), (38, 40, 44))
    draw = ImageDraw.Draw(canvas)
    for i, (label, pixels) in enumerate(items):
        im = preview(pixels)
        im.thumbnail((tile[0] - 8, tile[1] - 28), Image.Resampling.NEAREST)
        x, y = i % columns * tile[0], i // columns * tile[1]
        draw.text((x + 4, y + 4), label, fill="white")
        canvas.paste(im, (x + 4, y + 26))
    canvas.save(path)


def final_evidence():
    selection = json.loads((OUT / "selection.json").read_text(encoding="utf-8"))
    indexes = {k: read_index(k) for k in ("xtx", "txy", "effects")}
    source_rows = json.loads((DATA / "contents-report.json").read_text(encoding="utf-8"))["members"]
    expected = {r["output"]["path"]: r["output"]["sha256"] for r in source_rows}
    paths = {r["path"] for index in indexes.values() for r in index["files"]}
    paths.update(p.relative_to(SOURCE).as_posix() for p in SOURCE.rglob("*.pxy"))
    paths.update(["credit.bin", "yajima/titlebg.bin", "yajima/titlefan.bin", "iwase/img/door/doorimg.dat"])
    paths.update(p.relative_to(SOURCE).as_posix() for p in SOURCE.rglob("*.jpg"))
    for path in sorted(paths):
        p = SOURCE / path
        require(sha(p.read_bytes()) == expected[p.relative_to(REPO).as_posix()], f"Original input changed: {path}")

    pxy = [{"path": p.relative_to(SOURCE).as_posix(), "sha256": sha(p.read_bytes()),
            **pxy_layout(p.read_bytes())} for p in sorted(SOURCE.rglob("*.pxy"))]
    (OUT / "pxy.json.gz").write_bytes(gzip.compress(json.dumps(pxy, separators=(",", ":")).encode(), mtime=0))
    menu = []
    for path, row in selection["menu"].items():
        data = (SOURCE / path).read_bytes()
        require(sha(data) == row["sha256"], "Selected menu hash changed")
        views = {d["name"]: p for p, d in xtx_views(data)}
        pixels = views["PSMT4-index-preview" if path.endswith("ctrl01.xtx") else "PSMT8-index-preview"]
        seen = set()
        for rect in row["rectangles"]:
            if rect["key"] in seen:
                continue
            seen.add(rect["key"])
            x, y, w, h = rect["xywh"]
            require(x + w <= pixels.shape[1] and y + h <= pixels.shape[0], "Menu crop outside view")
            menu.append((rect["key"], pixels[y:y + h, x:x + w]))
    overview(menu, OUT / "menu.png", columns=4, tile=(330, 90))
    labels = [indexes["effects"]["images"][r["id"]] for r in selection["labels"]]
    sheet("effects", labels, OUT / "labels.png", columns=3, tile=(530, 100))
    misc = dict(miscellaneous_images())
    raster_paths = ("yajima/mnt_set.jpg", "yajima/tuto.jpg", "yajima/titlefan.bin", "yajima/gameov2.jpg")
    selection["raster"] = [{"path": path, "sha256": sha((SOURCE / path).read_bytes()),
                            "shape": list(misc[path].shape),
                            "category": "logo" if path.endswith(("titlefan.bin", "gameov2.jpg")) else "instructions"}
                           for path in raster_paths]
    overview([(path, misc[path]) for path in raster_paths], OUT / "other-japanese.png", columns=2)
    bundled = list(titlebg_images())
    report = {
        "scope": "EXP translation-facing graphics analysis; production status is in exp-graphics-deployment.json",
        "original_files_hash_verified": len(paths), "unit_tests_last_passed": 10,
        "menu_editable_images": len(menu), "menu_write_rectangles": selection["menu_write_rectangles"],
        "map_label_resources": len(labels), "raster_images_with_japanese": len(raster_paths),
        "txy_unique": len(indexes["txy"]["images"]), "txy_named_visually_reviewed": 145,
        "txy_not_visually_reviewed": len(indexes["txy"]["images"]) - 145,
        "txy_unresolved": indexes["txy"]["unresolved"],
        "effect_unique": len(indexes["effects"]["images"]), "effect_unresolved": indexes["effects"]["unresolved"],
        "effect_visual_scope": "17 labels, named candidates, large-effect sample and two new effect variants; not every image",
        "pxy_files": len(pxy), "pxy_uninterpreted_tail_files": sum(r["uninterpreted_tail_bytes"] > 0 for r in pxy),
        "titlebg_additional_images_reviewed": [{"sha256": pixel_sha(p), **d} for p, d in bundled],
        "doorimg": {"members": 18, "self_contained_palette": True, "palette_xy": [32, 0],
                    "status": "Separate door-review.json records the corrected 64x64 pictures; actual scene UVs unverified"},
        "gpu_or_gameplay_verified": False, "root_editors_modified": False,
        "remaining": ["neon sign and combat-glyph decisions",
                      "unreviewed background/model textures", "door actual scene use", "memory-card graphic source/use",
                      "additional live executable strings and general relocation beyond the approved numeric cases",
                      "PCSX2 layout/loading checks"]}
    (OUT / "selection.json").write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] {len(paths)} original hashes; {len(menu)} menu crops; {len(labels)} labels; {len(raster_paths)} rasters")


def preview(pixels):
    if pixels.shape[2] == 4 and not np.any(pixels[..., :3]):
        return Image.fromarray(pixels[..., 3]).convert("RGB")
    return Image.fromarray(pixels[..., :3])


def sheet(kind, rows, path, columns=6, tile=(230, 170)):
    require(rows, "Empty review selection")
    canvas = Image.new("RGB", (columns * tile[0], ((len(rows) + columns - 1) // columns) * tile[1]), (38, 40, 44))
    draw = ImageDraw.Draw(canvas)
    for i, row in enumerate(rows):
        loc = row["locations"][0]; image = preview(get_pixels(kind, row))
        scale = min((tile[0] - 8) / image.width, (tile[1] - 38) / image.height, 3)
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.NEAREST)
        x, y = i % columns * tile[0], i // columns * tile[1]
        draw.text((x + 4, y + 2), f"{row['id']:05} {loc['name'][:28]}", fill="white")
        draw.text((x + 4, y + 16), Path(loc["path"]).name[:34], fill=(160, 190, 210))
        canvas.paste(image, (x + (tile[0] - image.width) // 2, y + 36))
    canvas.save(path)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("kind", choices=("effects", "txy", "xtx", "door", "final-evidence"))
    p.add_argument("--scan", action="store_true")
    p.add_argument("--selection", choices=("all", "named", "labels"), default="all")
    p.add_argument("--page", type=int)
    p.add_argument("--ids", nargs="*", type=int)
    a = p.parse_args()
    if a.kind == "final-evidence":
        final_evidence()
        return
    if a.kind == "door":
        review_doors()
        return
    report = scan(a.kind) if a.scan else read_index(a.kind)
    rows = report["images"]
    if a.selection == "named":
        rows = [r for r in rows if any(NAMES.search(o["name"]) for o in r["locations"])]
    if a.selection == "labels":
        rows = [r for r in rows if any(re.search(r"/ce0\d\d_999\.esd$", o["path"]) for o in r["locations"])]
    print(f"[SELECT] {len(rows)} images; {(len(rows) + 35) // 36} pages")
    if a.ids is not None:
        rows = [r for r in report["images"] if r["id"] in a.ids]
    elif a.page is not None:
        require(a.page > 0, "Page must be positive")
        rows = rows[(a.page - 1) * 36:a.page * 36]
    else:
        return
    sheet(a.kind, rows, OUT / "review.png")
    print("[REVIEW] " + str(OUT / "review.png"))


if __name__ == "__main__":
    main()
