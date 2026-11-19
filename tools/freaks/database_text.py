"""DTB's 201-entry database: protected text editors and byte-based hyperlinks."""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import struct

from adv_format import encode_text, render_text, require, text_end, tokens
from elf_probe import Elf
from elf_text_slots import allocate
from gothic import fixed_width
from unpack_contents import digest

from build_paths import DATA, REPO, WORK


TABLE = 0x16c4f0
COUNT = 201
STRIDE = 40
BODY_WIDTH = 600
TEXT_POOL = (0x155f70, 0x16a830)
LINK_POOL = (0x16a830, 0x16bfb0)
SMALL_TITLES = (0x175478, 0x1755a0)
CATEGORY_HEADERS = (
    (0x1730e8, "\u30ad\u30e3\u30e9\u30af\u30bf\u30fc", "\uce90\ub9ad\ud130"),
    (0x173108, "\u30ef\u30fc\u30eb\u30c9", "\uc6d4\ub4dc"),
    (0x173120, "\u30d7\u30ed\u30c0\u30af\u30c4", "\ud504\ub85c\ub355\uce20"),
    (0x173140, "\u30ad\u30fc\u30ef\u30fc\u30c9", "\ud0a4\uc6cc\ub4dc"),
)
FIELDS = ("TITLE", "SHORT", "BODY")
EDITOR = WORK / "text/DATABASE.txt"
BLOCK = re.compile(r"^\[(\d{3})-(TITLE|SHORT|BODY)\]\n\[JPN\]\n(.*?)\n\[KOR\]\n(.*?)\n\[END\](?:\n|$)", re.M | re.S)
LINK_TAG = re.compile(r"\[LINK:(\d{3})\](.*?)\[/LINK\]", re.S)


@dataclass(frozen=True)
class Entry:
    index: int
    words: tuple
    texts: tuple
    links: tuple


def original_data():
    inventory = json.loads((DATA / "disc-inventory.json").read_text(encoding="utf-8"))
    expected = next(r["sha256"] for r in inventory["files"] if r["path"] == "DTB/DTB.ELF")
    data = (REPO / "unpacked/DTB/DTB.ELF").read_bytes()
    require(digest(data) == expected, "Original DTB.ELF hash changed")
    return data


def patch_category_headers(data, mapping):
    elf = Elf(data)
    output = bytearray(data)
    prefix, suffix = b"\x01\x01\x03\x02", b"\x03\x00\0"
    for address, japanese, korean in CATEGORY_HEADERS:
        at = elf.offset(address)
        original = prefix + f"\uff3b{japanese}\uff3d\u3000\u3000".encode("cp932") + suffix
        require(data[at:at + len(original)] == original, f"Original DTB category header differs: {address:08X}")
        translated = prefix + encode_text(f"\uff3b{korean}\uff3d\u3000\u3000", mapping) + suffix
        require(len(translated) <= len(original), f"DTB category exceeds its slot: {address:08X}")
        output[at:at + len(original)] = translated.ljust(len(original), b"\0")
    return bytes(output)


def plain_text(data):
    require(all(not control or raw == b"\n" for control, raw in tokens(data)),
            "Database text only permits ordinary characters and line breaks")


def width(data):
    return fixed_width(data)


def validate_layout(raw, links, kind, *, measure=width, body_width=620):
    plain_text(raw)
    lines = raw.split(b"\n")
    limits = {"TITLE": (1, 280), "SHORT": (4, 280), "BODY": (15, body_width)}
    rows, pixels = limits[kind]
    require(len(lines) <= rows, f"{kind}: too many lines ({len(lines)} > {rows})")
    require(all(measure(line) <= pixels for line in lines), f"{kind}: line exceeds {pixels}px; insert a line break")
    boundaries = {0}
    at = 0
    for _, token in tokens(raw):
        at += len(token)
        boundaries.add(at)
    previous = 0
    for start, length, target in links:
        require(previous <= start and 0 < length and start + length <= len(raw)
                and start in boundaries and start + length in boundaries,
                "Database link overlaps text or splits a character")
        require(0 <= target < COUNT and b"\n" not in raw[start:start + length],
                "Database link has an invalid target or crosses a line")
        require(start <= 65535 and length <= 65535, "Database link exceeds 16-bit range")
        previous = start + length
    require(len(links) <= 16, "Database exceeds the verified 16-link metadata budget")
    for number, line in enumerate(lines):
        extra = 4 * sum(raw[:start].count(b"\n") == number for start, _, _ in links)
        require(len(line) + extra + 1 <= 128, "Database highlighted line exceeds its 128-byte buffer")


def read_entries(data, *, measure=width):
    elf = Elf(data)
    result = []
    for index in range(COUNT):
        words = struct.unpack_from("<10I", data, elf.offset(TABLE + index * STRIDE))
        texts = []
        for address in words[2:5]:
            require(TEXT_POOL[0] <= address < TEXT_POOL[1]
                    or SMALL_TITLES[0] <= address < SMALL_TITLES[1], "Database text pointer outside owned pools")
            offset = elf.offset(address)
            raw = data[offset:text_end(data, offset)]
            texts.append(raw)
        links = []
        if words[5]:
            require(LINK_POOL[0] <= words[5] < LINK_POOL[1], "Database link pointer outside link pool")
            for slot in range(17):
                address = words[5] + slot * 6
                require(address + 6 <= LINK_POOL[1], "Database links have no terminator")
                start, length, target = struct.unpack_from("<3H", data, elf.offset(address))
                if not length:
                    require((start, target) == (0, 0), "Database link terminator differs")
                    break
                links.append((start, length, target))
            else:
                raise ValueError("Database link count exceeds verified budget")
        for kind, raw in zip(FIELDS, texts):
            validate_layout(raw, links if kind == "BODY" else (), kind, measure=measure)
        result.append(Entry(index, words, tuple(texts), tuple(links)))
    require(data[elf.offset(TABLE + COUNT * STRIDE):elf.offset(TABLE + COUNT * STRIDE) + STRIDE]
            == bytes(STRIDE), "Database table sentinel differs")
    return result


def annotated(raw, links):
    result = []
    cursor = 0
    for start, length, target in links:
        result.extend((render_text(raw[cursor:start]), f"[LINK:{target:03}]",
                       render_text(raw[start:start + length]), "[/LINK]"))
        cursor = start + length
    return "".join(result) + render_text(raw[cursor:])


def encode_annotated(text, original_links, mapping=None):
    raw = bytearray()
    links = []
    cursor = 0
    for match in LINK_TAG.finditer(text):
        prefix, label = text[cursor:match.start()], match[2]
        require("[LINK" not in prefix and "[/LINK" not in prefix
                and "[LINK" not in label and "[/LINK" not in label, "Malformed or nested database link")
        raw.extend(encode_text(prefix, mapping))
        encoded = encode_text(label, mapping)
        links.append((len(raw), len(encoded), int(match[1])))
        raw.extend(encoded)
        cursor = match.end()
    tail = text[cursor:]
    require("[LINK" not in tail and "[/LINK" not in tail, "Malformed database link tag")
    raw.extend(encode_text(tail, mapping))
    require([target for _, _, target in links] == [target for _, _, target in original_links],
            "Database link targets/order changed; preserve the original LINK tags")
    return bytes(raw), tuple(links)


def export_text(data):
    lines = ["# FREAKS-DATABASE: JPN-KOR v1", f"# SHA256: {digest(data)}",
             "# Empty KOR preserves the original. Keep LINK targets and order; translate their labels.", ""]
    for entry in read_entries(data):
        for kind, raw in zip(FIELDS, entry.texts):
            text = annotated(raw, entry.links if kind == "BODY" else ())
            lines.extend((f"# {entry.index:03}: {render_text(entry.texts[0])}",
                          f"[{entry.index:03}-{kind}]", "[JPN]", text, "[KOR]", "", "[END]", ""))
    return "\n".join(lines)


def parse_editor(content, data):
    content = content.removeprefix("\ufeff").replace("\r\n", "\n")
    require("\r" not in content and content.startswith("# FREAKS-DATABASE: JPN-KOR v1\n"), "Wrong database editor format")
    require(re.findall(r"^# SHA256: (.+)$", content, re.M) == [digest(data)], "Database source hash differs")
    expected = [(entry, kind, raw) for entry in read_entries(data) for kind, raw in zip(FIELDS, entry.texts)]
    matches = list(BLOCK.finditer(content))
    require(len(matches) == len(expected), "Missing, duplicate or malformed database blocks")
    result = {}
    cursor = 0
    for match, (entry, kind, raw) in zip(matches, expected):
        require(all(not line or line.startswith("#") for line in content[cursor:match.start()].splitlines()),
                "Unexpected content outside database block")
        key = (entry.index, kind)
        require((int(match[1]), match[2]) == key, "Database IDs changed or reordered")
        require(match[3] == annotated(raw, entry.links if kind == "BODY" else ()), f"Database JPN changed at {key}")
        result[key] = match[4]
        cursor = match.end()
    require(all(not line or line.startswith("#") for line in content[cursor:].splitlines()), "Unparsed database editor tail")
    return result


def owned_slots(data, entries):
    elf = Elf(data)
    addresses = sorted({address for entry in entries for address in entry.words[2:5]})
    require(len(addresses) == COUNT * 3, "Unexpected shared database text pointer")
    slots = {}
    for low, high in (TEXT_POOL, SMALL_TITLES):
        subset = [a for a in addresses if low <= a < high]
        require(subset and subset[0] == low, "Database pool start differs")
        for address, end in zip(subset, subset[1:] + [high]):
            offset, limit = elf.offset(address), elf.offset(end - 1) + 1
            terminator = text_end(data, offset)
            require(terminator < limit and not any(data[terminator + 1:limit]),
                    "Database string slot contains non-text data")
            slots[address] = end
    return slots


def compact_strings(data, elf, slots, replacements):
    def owned(address):
        return any(low <= address < high for low, high in (TEXT_POOL, SMALL_TITLES))

    for section in elf.sections:
        if not section[2] & 2 or section[1] == 8:
            continue
        words = struct.unpack_from(f"<{section[5] // 4}I", data, section[4])
        for i, word in enumerate(words):
            address = section[3] + i * 4
            # Media offsets and packed link triples can resemble interior addresses.
            if word in slots:
                require(TABLE <= address < TABLE + COUNT * STRIDE and (address - TABLE) % STRIDE in (8, 12, 16),
                        f"Unreviewed DB string pointer at {address:08X}")
            if not section[2] & 4 or word >> 26 != 15:
                continue
            register = word >> 16 & 31
            stop_after_delay = False
            for low in words[i + 1:i + 17]:
                op, rt = low >> 26, low >> 16 & 31
                if low >> 21 & 31 == register and op in (9, 13, 25, 32, 33, 35, 36, 37, 40, 41, 43, 49, 57, 55, 63):
                    value = ((word & 65535) << 16) + (low & 65535 if op == 13 else (low & 32767) - (low & 32768))
                    require(not owned(value), f"Unreviewed DB code reference at {address:08X}")
                writes = (rt == register and op in (*range(8, 16), 24, 25, 26, 27, *range(32, 40), 48, 52, 55))
                writes |= op == 0 and low >> 11 & 31 == register and low & 63 not in (8, 17, 19, 24, 25, 26, 27)
                if writes or stop_after_delay:
                    break
                stop_after_delay = op in (2, 3) or op == 0 and low & 63 in (8, 9)
    output, positions = bytearray(data), {}
    for low, high in (TEXT_POOL, SMALL_TITLES):
        addresses = [a for a in sorted(slots) if low <= a < high]
        records = []
        for address in addresses:
            at = elf.offset(address)
            raw = replacements.get(address, data[at:text_end(data, at)])
            records.append((address, raw))
        needed = sum((len(raw) + 4) & ~3 for _, raw in records)
        require(needed <= high - low,
                f"Owned database text pool needs {needed} bytes, only {high - low} available")
        output[elf.offset(low):elf.offset(high - 1) + 1] = bytes(high - low)
        cursor = low
        for address, raw in records:
            positions[address] = cursor
            at = elf.offset(cursor)
            output[at:at + len(raw) + 1] = raw + b"\0"
            cursor += (len(raw) + 4) & ~3
    return output, positions


def rebuild(data, translations, mapping=None, *, measure=width, compact=False):
    entries = read_entries(data)
    slots = owned_slots(data, entries)
    elf = Elf(data)
    known = {(entry.index, kind) for entry in entries for kind in FIELDS}
    require(set(translations) <= known, "Unknown database translation ID")
    replacements = {}
    new_links = {}
    changed = []
    for entry in entries:
        for column, (kind, raw) in enumerate(zip(FIELDS, entry.texts), 2):
            key = (entry.index, kind)
            text = translations.get(key, "")
            original_links = entry.links if kind == "BODY" else ()
            if not text or text == annotated(raw, original_links):
                continue
            encoded, links = encode_annotated(text, original_links, mapping)
            try:
                validate_layout(encoded, links, kind, measure=measure, body_width=BODY_WIDTH)
            except ValueError as error:
                raise ValueError(f"[{entry.index:03}-{kind}] {error}") from error
            if encoded == raw and links == original_links:
                continue
            replacements[entry.words[column]] = encoded
            if kind == "BODY":
                new_links[entry.index] = links
            changed.append(key)
    if not changed:
        return data, {"changed": [], "moved": [], "links": sum(len(e.links) for e in entries)}

    output, positions = (compact_strings(data, elf, slots, replacements) if compact
                         else allocate(data, elf, slots, replacements, "database"))
    for entry in entries:
        for column in (2, 3, 4):
            struct.pack_into("<I", output, elf.offset(TABLE + entry.index * STRIDE + column * 4),
                             positions[entry.words[column]])
        if entry.index in new_links:
            require(len(new_links[entry.index]) == len(entry.links), "Link table allocation changed")
            for index, link in enumerate(new_links[entry.index]):
                struct.pack_into("<3H", output, elf.offset(entry.words[5] + index * 6), *link)
    rebuilt = bytes(output)
    actual = read_entries(rebuilt, measure=measure)
    for before, after in zip(entries, actual):
        require(before.words[:2] == after.words[:2] and before.words[5:] == after.words[5:],
                "Database non-text fields changed")
        for column, raw in enumerate(before.texts, 2):
            require(after.texts[column - 2] == replacements.get(before.words[column], raw), "Database text readback differs")
        require(after.links == new_links.get(before.index, before.links), "Database link readback differs")
    require(len(rebuilt) == len(data), "Database ELF size changed")
    return rebuilt, {"changed": [f"{i:03}-{k}" for i, k in changed],
                     "moved": [{"from": f"{a:08X}", "to": f"{b:08X}"} for a, b in positions.items() if a != b],
                     "links": sum(len(e.links) for e in entries)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("export", "verify"))
    parser.add_argument("--font", type=Path)
    args = parser.parse_args()
    if args.mode == "verify":
        from rebuild_pipeline import execute
        execute("verify", args.font)
        return
    data = original_data()
    if args.mode == "export":
        text = export_text(data)
        require(not EDITOR.exists() or EDITOR.read_text(encoding="utf-8") == text,
                f"Existing database translation preserved: {EDITOR}")
        if not EDITOR.exists():
            from font_tool import write_generated
            write_generated(EDITOR, text.encode("utf-8"))
    translations = parse_editor(EDITOR.read_text(encoding="utf-8"), data)
    print(f"[PASS] 201 entries; 603 texts; 718 hyperlinks; {sum(bool(t) for t in translations.values())} KOR blocks")
    print("[NOTE] Use Rebuild.bat to rebuild with the shared ADV/database font mapping.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"[ERROR] {error}")
