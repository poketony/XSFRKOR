"""Original-layout EXP field TXT editors and bounded Shift-JIS rebuilding."""

from dataclasses import dataclass
import json
import re

import exp_sb as sb

from build_paths import DATA, REPO
from unpack_disc import require


PROFILES = {
    "iwase/Citizen.txt": {"count": 50, "stride": 0xcf, "entry": 0x229b88, "global": 0x6977f4,
                          "fields": (("Name", 0, 16), ("Sex", 0x11, 4), ("Age", 0x16, 16),
                                     ("Hobby", 0x27, 16), ("Profile", 0x38, 150))},
    "iwase/QuestMes.txt": {"count": 36, "stride": 0xb40, "entry": 0x229998, "global": 0x6977f0,
                           "fields": (("Subject", 0, 24), ("Locate", 0x19, 16), ("Client", 0x2a, 16),
                                      ("MesIPG", 0x3b, 256), ("MesSCD", 0x13c, 256),
                                      ("MesFLD", 0x23d, 256), ("UkunSCD", 0x33e, 1024),
                                      ("UkunFLD", 0x73f, 1024))},
    "iwase/Treasure.txt": {"count": 18, "stride": 0x9b, "entry": 0x22cd08, "global": 0x697804,
                           "fields": (("Item", 0, 16), ("Locate", 0x11, 16), ("Expl", 0x22, 120))},
}
RECORD = re.compile(r"^No\.(\d+)\s*\{(.*?)^\}", re.M | re.S)
VALUE = re.compile(r'^[ \t]*([A-Za-z]+):"([^"]*)"', re.M)


def sjis_pair(lead, trail):
    doubled = lead * 2
    first = doubled + (0x9f if (doubled & 0xfe) < 0x3f else 0x1f)
    if trail < 0x9f:
        second = trail + (0x61 if trail < 0x7f else 0x60)
    else:
        first += 1
        second = trail + 2
    return bytes((first & 255, second & 255))


INVERSE = {sjis_pair(a, b): bytes((a, b)) for a in (*range(0x81, 0xa0), *range(0xe0, 0xf0))
           for b in (*range(0x40, 0x7f), *range(0x80, 0xfd))}


def runtime_text(raw):
    result, at = bytearray(), 0
    while at < len(raw):
        c = raw[at]
        if raw[at:at + 2] == b"\r\n":
            at += 2
        elif c in (32, 9):
            at += 1
        elif c >= 128:
            require(at + 1 < len(raw), "Incomplete EXP Shift-JIS pair")
            result.extend(sjis_pair(c, raw[at + 1]))
            at += 2
        elif c == 92:
            require(raw[at:at + 2] == b"\\n", "Unsupported EXP quoted-field escape")
            result.append(10)
            at += 2
        else:
            require(c >= 32 and c != 34, "Unexpected control/quote in EXP field")
            result.append(c)
            at += 1
    return bytes(result)


@dataclass(frozen=True)
class Field:
    key: str
    start: int
    end: int
    value: str


def fields(text, name):
    require(name in PROFILES and "\0" not in text, "Unknown or NUL-containing EXP field TXT")
    profile = PROFILES[name]
    records = list(RECORD.finditer(text))
    require(len(records) == profile["count"] and [int(r[1]) for r in records] == list(range(1, len(records) + 1)),
            f"EXP record count/order changed: {name}")
    result = []
    for record in records:
        values = list(VALUE.finditer(record[2]))
        require([value[1] for value in values] == [key for key, _, _ in profile["fields"]],
                f"EXP field names/order/quotes changed: {name}:No.{record[1]}")
        for value in values:
            result.append(Field(f"{int(record[1]):02d}.{value[1]}", record.start(2) + value.start(2),
                                record.start(2) + value.end(2), value[2]))
    return result


def limits(name):
    return {f"{record:02d}.{key}": limit for record in range(1, PROFILES[name]["count"] + 1)
            for key, _, limit in PROFILES[name]["fields"]}


def layout(text, values):
    result, end = [], 0
    for field in values:
        result.extend((text[end:field.start], "\0"))
        end = field.end
    result.append(text[end:])
    return re.sub(r"\s+", "", "".join(result))


def originals():
    report = json.loads((DATA / "contents-report.json").read_text(encoding="utf-8"))
    result = {}
    for name in PROFILES:
        row = next(r for r in report["members"] if r["source"] == "EXP/XENOSAGA.00~02" and r["path"] == name)
        data = (REPO / row["output"]["path"]).read_bytes()
        require(sb.checksum(data) == row["output"]["sha256"], f"Original EXP field TXT differs: {name}")
        source_fields(data, name)
        result[name] = data
    return result


def source_fields(data, name):
    text = data.decode("cp932")
    require(text.encode("cp932") == data, f"Non-roundtrippable original EXP Shift-JIS: {name}")
    values = fields(text, name)
    maxima = limits(name)
    for field in values:
        raw = runtime_text(field.value.encode("cp932"))
        require(len(raw) <= maxima[field.key], f"Original EXP field exceeds its runtime slot: {name}:{field.key}")
    return values


def export_editor(data, name):
    source_fields(data, name)
    return data.decode("cp932")


def editors():
    return {name: export_editor(data, name) for name, data in originals().items()}


def parse_editor(content, data, name):
    content = content.removeprefix("\ufeff").replace("\r\n", "\n")
    original = data.decode("cp932").replace("\r\n", "\n")
    before, after = fields(original, name), fields(content, name)
    require(layout(original, before) == layout(content, after), f"Only quoted EXP field text may change: {name}")
    result = {}
    for old, new in zip(before, after):
        if old.value != new.value:
            result[old.key] = re.sub(r"\n[ \t]*", "", new.value).replace("\\n", "\n")
    return result


def runtime_bytes(text, mapping=None):
    mapping = {} if mapping is None else mapping
    require(not any(c in text for c in ('"', "\\", "\r", "\t", "\0")),
            "EXP field text cannot contain quotes, backslashes, tabs or CR/NUL")
    text = text.replace(" ", "\u3000")
    result = bytearray()
    for char in text:
        unit = mapping[char] if char in mapping else char.encode(sb.ENCODING)
        require(char in mapping or unit == b"\n" or len(unit) == 1 and 0x21 <= unit[0] <= 0x7e
                or len(unit) == 2 and all(0xa1 <= b <= 0xfe for b in unit),
                "EXP field renderer cannot read this one/two-byte character")
        result.extend(unit)
    return bytes(result)


def encode(text, mapping=None):
    desired = runtime_bytes(text, mapping)
    result, at = bytearray(), 0
    while at < len(desired):
        c = desired[at]
        if c == 10:
            result.extend(b"\\n")
            at += 1
        elif 0x21 <= c <= 0x7e and c not in (34, 92):
            result.append(c)
            at += 1
        else:
            pair = desired[at:at + 2]
            require(pair in INVERSE, f"EXP field has no Shift-JIS inverse: {pair.hex()}")
            result.extend(INVERSE[pair])
            at += 2
    require(runtime_text(result) == desired, "EXP quoted-field conversion differs")
    return bytes(result), desired


def rebuild(data, name, translations, mapping=None):
    values, maxima = source_fields(data, name), limits(name)
    require(set(translations) <= maxima.keys(), f"Unknown EXP field translation: {name}")
    text = data.decode("cp932")
    changes = []
    for field in values:
        if field.key not in translations:
            continue
        try:
            raw, desired = encode(translations[field.key], mapping)
            require(len(desired) <= maxima[field.key],
                    f"EXP field exceeds runtime slot ({len(desired)} > {maxima[field.key]})")
        except (ValueError, UnicodeError) as error:
            raise ValueError(f"{name}:{field.key}: {error}") from error
        old = runtime_text(field.value.encode("cp932"))
        if old == desired:
            continue
        start = len(text[:field.start].encode("cp932"))
        end = start + len(field.value.encode("cp932"))
        changes.append((field.key, start, end, raw, desired))
    output = bytearray(data)
    for _, start, end, raw, _ in reversed(changes):
        output[start:end] = raw
    after = {field.key: runtime_text(field.value.encode("cp932")) for field in source_fields(bytes(output), name)}
    require(all(after[key] == desired for key, _, _, _, desired in changes), "Rebuilt EXP field bytes differ")
    return bytes(output), {"file": name, "changed": [{"id": key, "runtime_bytes": len(desired), "limit": maxima[key]}
                                                   for key, _, _, _, desired in changes],
                           "source_size": len(data), "size": len(output), "ascii_spaces": "automatic-fullwidth"}
