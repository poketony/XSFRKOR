"""MINI definition/UI editors, separate EUC font map and paired ELF/WWROM build."""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import struct

from elf_probe import Elf
from elf_text_slots import allocate
from font_tool import write_generated
import mini_dictionary as md
import mini_diagnostic_names as diagnosis
from mini_dictionary_native import DictionaryMachine, verify_definition_layout
import mini_font as mf
import mini_graphics
import mini_ending
import mini_messages
import mini_headings
import mini_conditions
import mini_circle_titles
import mini_record_titles
import mini_choices
import mini_diagnostic_titles
import mini_screen_settings
import mini_sprite_layout
import mini_opening
import save_assets
from mini_text_native import verify as verify_system
from system_text import BLOCK
from ui_labels import pair_address
from unpack_contents import digest

from build_paths import WORK
from unpack_disc import require
import wwrom
import xs2_hangul


DICTIONARY_EDITOR = WORK / "text/MINI_DICTIONARY.txt"
SYSTEM_EDITOR = WORK / "text/MINI_SYSTEM.txt"


@dataclass(frozen=True)
class Text:
    address: int
    end: int
    raw: bytes
    kind: str
    row: int = 0
    pointers: tuple = ()
    pairs: tuple = ()


def nul_end(data, start):
    return data.index(0, start)


def system_catalogue(data):
    elf = Elf(data)
    table = 0x1cc530
    pointers = struct.unpack_from("<230I", data, elf.offset(table))
    require(pointers[:5] == (0x217260,) * 5 and data[elf.offset(0x217260)] == 0,
            "MINI default diagnostic entries differ")
    require(len(set(pointers[5:])) == 225 and pointers[5] == 0x217268 and pointers[-1] == 0x218fa8,
            "MINI diagnostic table differs")
    require(struct.unpack_from("<I", data, elf.offset(0x13d1a4))[0] == 0x3c02001d
            and struct.unpack_from("<I", data, elf.offset(0x13d1ac))[0] == 0x2453c530,
            "MINI diagnostic table reference differs")
    records = [(address, "diagnostic", row, (table + row * 4,), ())
               for row, address in enumerate(pointers) if row >= 5]
    require(pair_address(data, elf, 0x1162dc, 0x1162e0) == 0x2145b8,
            "MINI no-words notice reference differs")
    records.append((0x2145b8, "no_words", 0, (), ((0x1162dc, 0x1162e0),)))
    require(struct.unpack_from("<I", data, elf.offset(0x1ceb38))[0] == 0x21ac40,
            "MINI random-stage notice reference differs")
    records.append((0x21ac40, "random_stage", 0, (0x1ceb38,), ()))
    result = []
    for address, kind, row, references, pairs in sorted(records):
        start = elf.offset(address)
        stop = nul_end(data, start)
        raw = data[start:stop]
        md.text_codes(raw)
        require(raw and len(raw) < 256 and all(len(c) == 1 or mf.glyph_index(c) >= 0 for c in md.text_codes(raw)),
                "MINI UI source has an unsupported character")
        end = (address + len(raw) + 8) & ~7
        require(not any(data[stop + 1:elf.offset(end - 1) + 1]), "MINI UI slot padding contains other data")
        result.append(Text(address, end, raw, kind, row, references, pairs))
    require(all(a.end <= b.address for a, b in zip(result, result[1:])), "MINI UI slots overlap")
    return result


def dictionary_hash(dictionary):
    return digest(b"".join(dictionary.files[n] for n in md.NAMES))


def export_dictionary(dictionary):
    lines = ["# FREAKS-MINI-DICTIONARY: JPN-KOR v1", f"# SHA256: {dictionary_hash(dictionary)}",
             "# Translate definitions only. Japanese readings, spellings, categories and puzzle IDs remain unchanged.",
             "# Empty KOR keeps original. One line per definition; the original renderer wraps automatically.", ""]
    for word in dictionary.words:
        lines.extend((f"# WORD {word.word_id:05d} / CATEGORY {word.category} / "
                      f"{word.reading.decode('euc_jp')} / {word.spelling.decode('euc_jp')}",
                      f"[{word.word_id:08X}]", "[JPN]", word.definition.decode("euc_jp"),
                      "[KOR]", "", "[END]", ""))
    return "\n".join(lines)


def export_system(data):
    lines = ["# FREAKS-MINI-SYSTEM: JPN-KOR v1", f"# SHA256: {digest(data)}",
             "# Empty KOR keeps original. One line per block. EUC-JP and the MINI font map are generated automatically.", ""]
    for item in system_catalogue(data):
        budget = "256x16px" if item.kind == "diagnostic" else "200x40px" if item.kind == "no_words" else "224px centered"
        lines.extend((f"# {item.kind.upper()} / {budget}", f"[{item.address:08X}]", "[JPN]",
                      item.raw.decode("euc_jp"), "[KOR]", "", "[END]", ""))
    return "\n".join(lines)


def parse_editor(content, kind, source_hash, records):
    content = content.removeprefix("\ufeff").replace("\r\n", "\n")
    require("\r" not in content and content.startswith(f"# FREAKS-MINI-{kind}: JPN-KOR v1\n"),
            "Wrong MINI editor format")
    require(re.findall(r"^# SHA256: (.+)$", content, re.M) == [source_hash], "MINI source hash differs")
    cursor, result = 0, {}
    matches = BLOCK.finditer(content)
    for identifier, raw in records:
        match = next(matches, None)
        require(match is not None, "Missing or malformed MINI editor block")
        require(all(not line or line.startswith("#") for line in content[cursor:match.start()].splitlines()),
                "Unexpected content outside MINI block")
        require(int(match[1], 16) == identifier and match[2] == raw.decode("euc_jp"), "MINI block ID/JPN changed")
        require("\n" not in match[3] and "\0" not in match[3], "MINI KOR must be a single line without NUL")
        if match[3] and match[3] != match[2]:
            result[identifier] = match[3]
        cursor = match.end()
    require(next(matches, None) is None, "Extra or duplicate MINI editor block")
    require(all(not line or line.startswith("#") for line in content[cursor:].splitlines()), "Unparsed MINI editor tail")
    return result


def encode(text, mapping):
    raw = bytearray()
    for char in text:
        try:
            raw.extend(mapping[char] if char in mapping else char.encode("euc_jp"))
        except UnicodeEncodeError as error:
            raise ValueError(f"Unsupported MINI character: {char!r}") from error
    result = bytes(raw)
    codes = md.text_codes(result, mapping.values())
    require(all(len(c) == 1 or mf.glyph_index(c) >= 0 for c in codes), "MINI character is not supported by its renderer")
    return result


def rebuild_system(data, texts, mapping):
    elf = Elf(data)
    items = system_catalogue(data)
    require(set(texts) <= {i.address for i in items}, "Unknown MINI UI ID")
    replacements = {address: encode(text, mapping) for address, text in texts.items()}
    for item in items:
        raw = replacements.get(item.address, item.raw)
        require(len(raw) < 256, "MINI UI exceeds its 256-byte text state")
        if item.kind == "random_stage":
            require(len(raw) <= len(item.raw), "MINI random-stage text exceeds its original envelope")
    try:
        output, positions = allocate(data, elf, {i.address: i.end for i in items}, replacements, "MINI UI", find_end=nul_end)
    except ValueError as error:
        if not str(error).startswith("No owned MINI UI slot fits "):
            raise
        output, positions = _compact_system(data, elf, items, replacements)
    for item in items:
        destination = positions[item.address]
        for pointer in item.pointers:
            struct.pack_into("<I", output, elf.offset(pointer), destination)
        for upper, lower in item.pairs:
            for at, immediate in ((upper, (destination + 0x8000) >> 16), (lower, destination & 65535)):
                word = struct.unpack_from("<I", data, elf.offset(at))[0]
                struct.pack_into("<I", output, elf.offset(at), word & 0xffff0000 | immediate)
        at = elf.offset(destination)
        require(output[at:nul_end(output, at)] == replacements.get(item.address, item.raw), "MINI UI readback differs")
    require(len(output) == len(data), "MINI text changed ELF size")
    return bytes(output), items, positions, replacements


def _compact_system(data, elf, items, replacements):
    """Repack only audited string slots when retaining old starts fragments them."""
    spans = []
    for item in items:
        if spans and spans[-1][1] == item.address:
            spans[-1] = spans[-1][0], item.end
        else:
            spans.append((item.address, item.end))
    output = bytearray(data)
    for start, end in spans:
        at = elf.offset(start)
        output[at:at + end - start] = bytes(end - start)
    free, positions = list(spans), {}
    rows = [(item.address, replacements.get(item.address, item.raw)) for item in items]
    for address, raw in sorted(rows, key=lambda row: (-len(row[1]), row[0])):
        size = len(raw) + 1
        slot = next((i for i, (start, end) in enumerate(free) if size <= end - start), None)
        require(slot is not None,
                f"MINI UI text exceeds its audited string pool at {address:08X}; shorten translations")
        start, end = free.pop(slot)
        positions[address] = start
        at = elf.offset(start)
        output[at:at + size] = raw + b"\0"
        aligned = (start + size + 3) & ~3
        if aligned < end:
            free.insert(slot, (aligned, end))
    return output, positions


def plan(system_editor=None, dictionary_editor=None, font_path=None, *, graphics_edits=None, save_editor=None):
    font_path = mf.DEFAULT_FONT if font_path is None else font_path
    elf, rom = wwrom.originals()
    dictionary = md.originals()
    items = system_catalogue(elf)
    system_texts = {} if system_editor is None else parse_editor(system_editor, "SYSTEM", digest(elf),
                                                               ((i.address, i.raw) for i in items))
    definitions = {} if dictionary_editor is None else parse_editor(dictionary_editor, "DICTIONARY", dictionary_hash(dictionary),
                                                                   ((w.word_id, w.definition) for w in dictionary.words))
    chars = {c for text in (*system_texts.values(), *definitions.values()) for c in text
             if 0x1100 <= ord(c) <= 0x11ff or 0x3130 <= ord(c) <= 0x318f or 0xac00 <= ord(c) <= 0xd7a3}
    font = mf.original_font()
    mapping = xs2_hangul.mapping(chars, "euc_jp")
    all_mapping = xs2_hangul.mapping((k for k, _ in xs2_hangul.rows()), "euc_jp")
    new_font = mf.replace_fixed_hangul(font, all_mapping, mf.render_glyphs(all_mapping, font_path))
    encoded = {identifier: encode(text, mapping) for identifier, text in definitions.items()}
    files, dictionary_report = dictionary.rebuild_definitions(encoded, mapping.values())
    rebuilt_dictionary = md.Dictionary(files, mapping.values()) if encoded else dictionary
    patched, items, positions, replacements = rebuild_system(elf, system_texts, mapping)
    system_report = {"changed": [f"{a:08X}" for a in sorted(replacements)], "texts": len(items),
                     "moved": sum(a != b for a, b in positions.items()),
                     "native": verify_system(patched, items, positions, replacements)}
    dictionary_report["layout"] = verify_definition_layout(patched, rebuilt_dictionary, encoded)
    if encoded:
        dictionary_report["native"] = DictionaryMachine(patched, rebuilt_dictionary).verify()
    edits = {"dic/" + n: value for n, value in files.items() if value != dictionary.files[n]}
    if new_font != font:
        edits["font/zenkaku.fnt"] = new_font
    graphics_members, graphics_auxiliary, graphics_report = mini_graphics.plan(graphics_edits or {})
    patched = diagnosis.patch_elf(patched, graphics_report["diagnosis_name_tiles"])
    if graphics_report["opening_title_coordinates"]:
        patched = mini_opening.patch_title(patched)
    if graphics_report.get("feedback_button_coordinates"):
        patched = mini_sprite_layout.patch_feedback_button(patched)
    if graphics_report["ending_captions"]:
        patched = mini_ending.patch_tables(patched, graphics_report["ending_captions"])
    if graphics_report["game_message_captions"]:
        patched = mini_messages.patch_tables(patched, graphics_report["game_message_captions"])
    if graphics_report["all_clear_caption"]:
        patched = mini_messages.patch_all_clear(patched, graphics_report["all_clear_caption"])
    if graphics_report["menu_heading_captions"]:
        patched = mini_headings.patch_tables(patched, graphics_report["menu_heading_captions"])
    if graphics_report["condition_captions"]:
        patched = mini_conditions.patch_tables(patched, graphics_report["condition_captions"])
    if graphics_report["circle_title_captions"]:
        patched = mini_circle_titles.patch_elf(patched, graphics_report["circle_title_captions"])
    if graphics_report.get("record_title_captions"):
        patched = mini_record_titles.patch_tables(patched, graphics_report["record_title_captions"])
    if graphics_report.get("choice_captions"):
        patched = mini_choices.patch_tables(patched, graphics_report["choice_captions"])
    if graphics_report.get("diagnostic_captions"):
        patched = mini_diagnostic_titles.patch_tables(patched, graphics_report["diagnostic_captions"])
    if graphics_report.get("screen_setting_captions"):
        patched = mini_screen_settings.patch_elf(patched, graphics_report["screen_setting_captions"])
    require(not set(edits) & set(graphics_members), "Conflicting MINI dictionary/font/graphics member")
    edits.update(graphics_members)
    save_members, save_report = save_assets.plan_title(save_editor)
    require(not set(edits) & set(save_members), "Conflicting MINI save-title member")
    edits.update(save_members)
    output_elf, output_rom, archive_report = wwrom.rebuild(patched, rom, edits)
    if graphics_members:
        from mini_sprite_native import SpriteMachine
        from mini_validation_cache import checked
        graphics_report["native"] = checked("sprite-groups", (output_elf, output_rom),
                                            lambda: SpriteMachine(output_elf, output_rom).verify_graphics())
    outputs = {} if output_elf == elf and output_rom == rom else {"MINI/MINI.ELF": output_elf, "MINI/WWROM.DAT": output_rom}
    font_report = {"characters": len(mapping), "safe_code_budget": 2350, "installed_hangul_glyphs": 2350,
                   "font_file": str(font_path.resolve()), "hangul_order": "EUC-KR / XS2 fixed kanji table",
                   "replaced_kanji_cells": 2350,
                   "mapping": {c: code.hex().upper() for c, code in mapping.items()},
                   "decoded_size": len(new_font), "sha256": digest(new_font), "original_other_glyphs_preserved": True}
    auxiliary = dict(graphics_auxiliary)
    if new_font != font:
        auxiliary[WORK / "font/MINI-zenkaku.fnt"] = new_font
        auxiliary[WORK / "evidence/mini-font-map.json"] = (json.dumps(font_report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return outputs, auxiliary, {"system": system_report, "dictionary": dictionary_report,
                                "font": font_report, "graphics": graphics_report, "save": save_report,
                                "archive": archive_report}


def load_editors():
    return {"SYSTEM": SYSTEM_EDITOR.read_text(encoding="utf-8"),
            "DICTIONARY": DICTIONARY_EDITOR.read_text(encoding="utf-8")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("export", "verify"))
    parser.add_argument("--font", type=Path)
    args = parser.parse_args()
    if args.mode == "verify":
        from rebuild_pipeline import execute
        execute("verify", args.font)
        return
    elf, _ = wwrom.originals()
    dictionary = md.originals()
    for path, exported in ((SYSTEM_EDITOR, export_system(elf)), (DICTIONARY_EDITOR, export_dictionary(dictionary))):
        if not path.exists():
            write_generated(path, exported.encode("utf-8"))
    editors = load_editors()
    parse_editor(editors["SYSTEM"], "SYSTEM", digest(elf), ((i.address, i.raw) for i in system_catalogue(elf)))
    parse_editor(editors["DICTIONARY"], "DICTIONARY", dictionary_hash(dictionary), ((w.word_id, w.definition) for w in dictionary.words))
    print(f"[PASS] {len(system_catalogue(elf))} UI texts; {len(dictionary.words)} definitions; existing KOR preserved")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"[ERROR] {error}")
