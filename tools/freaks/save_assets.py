"""One shared browser-icon subtitle and the fixed MINI browser title."""

import io
from pathlib import Path
import re
import struct

import numpy as np
from PIL import Image

from font_tool import write_generated
from ps2_icon import Icon
from system_text import BLOCK
from unpack_contents import digest

from build_paths import REPO
from unpack_disc import require


ICONS = ("LNCH/DAT00/ICON.ICO", "ADV/DAT00/ICON.ICO")
ICON_HASH = "8db28ecb856c2a42a2556e52127ec77c110a8f5a664ec2639672322f69997c5e"
BOX = (7, 24, 43, 9)
IMAGE = "SAVE_ICON_SUBTITLE.png"
EDITOR = "MINI_SAVE.txt"
TITLE_MEMBER = "icon/icon.sys"
TITLE_HASH = "7f292089bb9dc299f809fd5dc29610b6dc3c6943ba6aefde942598c7733ba205"
TITLE_OFFSET = 0xc0
TITLE_BYTES = 64
TITLE_LIMIT = 26


def original_icon():
    sources = [(REPO / "unpacked" / name).read_bytes() for name in ICONS]
    require(all(digest(data) == ICON_HASH for data in sources), "Original shared save icon changed")
    icon = Icon(sources[0])
    require(icon.texture_offset == 40376 and icon.flags == 7 and icon.vertices == 1680
            and icon.shapes == 1, "Shared save icon profile changed")
    return icon


def original_title():
    data = (REPO / "decompressed/MINI/WWROM.DAT" / TITLE_MEMBER).read_bytes()
    require(digest(data) == TITLE_HASH and len(data) == 964 and data[:4] == b"PS2D",
            "Original MINI browser title changed")
    return data


def icon_image():
    x, y, w, h = BOX
    return Image.fromarray(original_icon().rgb()[y:y + h, x:x + w])


def extract(directory):
    path = Path(directory) / IMAGE
    image = icon_image()
    if path.exists():
        with Image.open(path) as existing:
            require(existing.size == image.size and existing.convert("RGB").tobytes() == image.tobytes(),
                    f"Existing save-icon PNG preserved; not overwritten: {path}")
    else:
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        write_generated(path, buffer.getvalue())
    print("[PASS] 1 shared Japanese save-icon subtitle PNG; existing KOR preserved")


def load_edit(directory):
    path = Path(directory) / (Path(IMAGE).stem + "_KOR.png")
    if not path.exists():
        return None
    with Image.open(path) as image:
        require(image.format == "PNG", "Edited save icon must be PNG")
        return image.copy()


def plan_icon(image=None):
    icon = original_icon()
    report = {"changed": False, "sources": list(ICONS), "box": list(BOX), "max_quantization_error": 0}
    if image is None:
        return {}, report
    require(image.mode in ("RGB", "RGBA") and image.size == BOX[2:], "Save icon must be an opaque 43x9 RGB PNG")
    rgba = np.array(image.convert("RGBA"))
    require(np.all(rgba[..., 3] == 255), "Save icon subtitle must be opaque")
    output = icon.replace_region(BOX, rgba[..., :3])
    x, y, w, h = BOX
    actual = Icon(output).rgb()[y:y + h, x:x + w]
    report["max_quantization_error"] = int(np.abs(actual.astype(int) - rgba[..., :3].astype(int)).max())
    report["changed"] = output != icon.data
    return ({name: output for name in ICONS} if report["changed"] else {}), report


def title_text(data):
    return data[TITLE_OFFSET:TITLE_OFFSET + TITLE_BYTES].split(b"\0", 1)[0].decode("cp932")


def export_title():
    data = original_title()
    return "\n".join(("# FREAKS-MINI-SAVE: JPN-KOR v1", f"# SHA256: {TITLE_HASH}",
                     "# PS2 browser system font: CP932/ASCII only; no custom Hangul. Single line, <=26 encoded bytes.",
                     "# Empty KOR keeps original. Existing memory-card saves may retain their old title/icon.",
                     "", f"[{TITLE_OFFSET:08X}]", "[JPN]", title_text(data), "[KOR]", "", "[END]", ""))


def parse_title(content):
    content = content.removeprefix("\ufeff").replace("\r\n", "\n")
    require("\r" not in content and content.startswith("# FREAKS-MINI-SAVE: JPN-KOR v1\n"),
            "Wrong MINI save-title editor format")
    require(re.findall(r"^# SHA256: (.+)$", content, re.M) == [TITLE_HASH], "MINI save-title source hash differs")
    matches = list(BLOCK.finditer(content))
    require(len(matches) == 1, "MINI save title needs exactly one block")
    match = matches[0]
    require(int(match[1], 16) == TITLE_OFFSET and match[2] == title_text(original_title()),
            "MINI save-title ID/JPN changed")
    outside = content[:match.start()] + content[match.end():]
    require(all(not line or line.startswith("#") for line in outside.splitlines()), "Unexpected MINI save-title content")
    text = match[3]
    require(all(ord(c) >= 32 and ord(c) != 127 for c in text), "MINI save title must be a single line without controls")
    try:
        raw = text.encode("cp932")
    except UnicodeEncodeError as error:
        raise ValueError("PS2 browser title cannot use custom Hangul; use CP932/ASCII") from error
    require(len(raw) <= TITLE_LIMIT, "MINI save title exceeds the original 26-byte line")
    return text


def plan_title(editor=None):
    source = original_title()
    text = "" if editor is None else parse_title(editor)
    if not text or text == title_text(source):
        return {}, {"changed": False}
    raw = text.encode("cp932")
    output = bytearray(source)
    output[TITLE_OFFSET:TITLE_OFFSET + TITLE_BYTES] = raw.ljust(TITLE_BYTES, b"\0")
    struct.pack_into("<H", output, 6, len(raw))
    return {TITLE_MEMBER: bytes(output)}, {"changed": True, "encoded_bytes": len(raw), "source": TITLE_MEMBER}
