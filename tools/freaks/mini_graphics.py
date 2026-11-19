"""Selected MINI translation/adaptation PNGs with native group audit and WWROM edits."""

import argparse
import io
from pathlib import Path
import struct

from PIL import Image

from elf_probe import Elf
from font_tool import write_generated
from mini_sprites import originals, Sprites
from mini_png import palette_reference, reference_fingerprint
import mini_diagnostic_names as diagnosis
import mini_demo_layout as demo
import mini_opening as opening
import mini_ending as ending
import mini_messages as messages
import mini_headings as headings
import mini_conditions as conditions
import mini_circle_titles as circles
import mini_record_titles as records
import mini_choices as choices
import mini_diagnostic_titles as diagnostic_titles
import mini_screen_settings as screen_settings
import mini_sprite_layout as layouts
import mini_tutorial_layout as tutorial

from build_paths import WORK
from unpack_disc import require
from verify_codecs import Native
import wwrom
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import UC_MIPS_REG_0, UC_MIPS_REG_SP, UC_MIPS_REG_GP, UC_MIPS_REG_PC


JAPANESE = {
    "opening_adaptation": (15,),
    "menu_help": (0, 1, 18, 31, 38, 55, 57, 59, 61, 73, 76, 80, 81, 84, 86, 90, 99, 101, 102,
                  106, 108, 109, 110, 111, 112, 117, 126, 127, 148, 149, 151, 157, 177, 178,
                  179, 180, 181, 182, 184, 208, 285, 295, 300, 301),
    "tutorial": (16, 17, 24, 25, 32, 33, 34, 54, 103, 122, 123, 124, 159, 160, 185, 186, 188,
                 209, *range(271, 281), *range(290, 294), 296, 297),
    "results": (79, 94, 100, 104, 105, 107, 114, 115, 140, 141, 142, 143, 144, 145, 187, 192, 193),
    "diagnostic_name_tiles": diagnosis.SHEETS,
    "logos": (9, 11, 12, 190, 206),
    "credits": (189, 212, 213, 214),
    "background_text": (231,),
}
PUZZLE_LETTERS = tuple(range(46, 53))


def image_name(index):
    return f"MINI_SPR_{index:03}.png"


def editor_image(sprites, index):
    if index in demo.EDITORS:
        return demo.image(sprites, index)
    return tutorial.image(sprites) if index == tutorial.EDITOR else layouts.image(sprites, index)


def source_groups(elf_data):
    elf = Elf(elf_data)
    references = elf.refs(0x10ad08)
    require(len(references) == 57 and all(kind == "jump/call" for _, kind in references),
            "MINI group loader references differ")
    groups = set()
    word = lambda va: struct.unpack_from("<I", elf_data, elf.offset(va))[0]
    dynamic = {0x11e30c, 0x11e350, 0x1826a4}
    for address, _ in references:
        if address in dynamic:
            continue
        previous, delay = word(address - 4), word(address + 4)
        if address == 0x11e2d0:
            require(word(0x11e290) == 0x24040007 and word(0x11e28c) == 0x10400010,
                    "MINI alternate object group branch differs")
            group = 7
        elif previous & 0xffff0000 == 0x24040000:
            group = previous & 65535
        else:
            require(previous == 0x0000202d, "Unknown MINI immediate group source")
            group = 0
        if delay & 0xffff0000 == 0x24050000:
            kind = delay & 65535
        else:
            require(delay == 0x0000282d, "Unknown MINI group kind source")
            kind = 0
        require(0 <= kind <= 3, "MINI group kind outside directory")
        groups.add((kind, group))
    native = Native(elf_data)
    native.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, u: uc.emu_stop(), begin=0x10ad08, end=0x10ad08)
    for entry, register, count in ((0x11e2f4, 18, 128), (0x11e330, 4, 128), (0x182690, None, 10)):
        for index in range(count):
            native.uc.reg_write(UC_MIPS_REG_GP, native.elf.gp)
            native.uc.reg_write(UC_MIPS_REG_SP, native.STACK)
            if register is None:
                native.uc.reg_write(UC_MIPS_REG_0 + 16, native.SOURCE)
                native.uc.mem_write(native.SOURCE + 0x38, struct.pack("<I", index))
            else:
                native.uc.reg_write(UC_MIPS_REG_0 + register, index)
            native.uc.emu_start(entry, native.STOP, count=100)
            require(native.uc.reg_read(UC_MIPS_REG_PC) == 0x10ad08, "MINI computed group path did not reach loader")
            group, kind = (native.uc.reg_read(UC_MIPS_REG_0 + r) & 0xffffffff for r in (4, 5))
            require(kind == 2 and group < 62, "MINI computed background group differs")
            groups.add((kind, group))
    return groups


def catalogue():
    sprites = originals()
    groups = source_groups(wwrom.originals()[0])
    known = {(kind, group) for kind, group, _ in sprites.groups}
    require(groups <= known, "MINI load call selects an unknown group")
    referenced = {i for kind, group, ids in sprites.groups if (kind, group) in groups for i in ids}
    categories = {i: name for name, ids in JAPANESE.items() for i in ids}
    require(sum(map(len, JAPANESE.values())) == len(categories), "Duplicate MINI Japanese selection")
    selected = {i: name for i, name in categories.items() if i in referenced}
    return sprites, selected, {"declared_textures": 302, "declared_sprites": 2060,
                               "direct_loader_sites": 57, "referenced_groups": sorted(groups),
                               "selected": [{"index": i, "category": name,
                                             "file": image_name(tutorial.EDITOR if i == tutorial.CONTINUATION
                                                                else 292 if i == demo.CONTINUATION else i)}
                                            for i, name in sorted(selected.items())],
                               "no_group_reference": sorted(set(range(302)) - referenced),
                               "puzzle_letter_sheets_pending_korean_rules": list(PUZZLE_LETTERS),
                               "reviewed_no_japanese_or_english_only": sorted(set(range(302)) - set(selected)
                                                                            - set(PUZZLE_LETTERS)
                                                                            - (set(range(302)) - referenced)),
                               "original_uv_overruns_preserved": sprites.original_uv_overruns,
                               "assembled_editor_textures": sorted({tutorial.EDITOR, *layouts.ASSEMBLED}
                                                                    - {demo.CONTINUATION}),
                               "combined_tutorial_sources": [tutorial.EDITOR, tutorial.CONTINUATION],
                               "combined_demo_sources": sorted(demo.SOURCES),
                               "demo_editor_messages": demo.EDITORS,
                               "editor_png_count": len(selected) - sum(i in selected for i in
                                                                       (tutorial.CONTINUATION, demo.CONTINUATION)),
                               "legacy_only_editor_textures": [tutorial.CONTINUATION, demo.CONTINUATION],
                               "reachability_scope": "All direct group-loader sites and both indexed background tables; not proof that every containing scene is reachable"}


def extract(directory=None):
    directory = WORK / "graphics" if directory is None else Path(directory)
    sprites, selected, report = catalogue()
    prepared = []
    for index in selected:
        if index in (tutorial.CONTINUATION, demo.CONTINUATION):
            continue
        image = editor_image(sprites, index)
        path = directory / image_name(index)
        if path.exists():
            with Image.open(path) as existing:
                if existing.size == image.size and existing.convert("RGBA").tobytes() == image.tobytes():
                    continue
                candidates = [sprites.image(index)]
                if index in demo.EDITORS:
                    candidates.append(layouts.image(sprites, index))
                require((index in layouts.ASSEMBLED or index == tutorial.EDITOR) and any(
                    existing.size == original.size and existing.convert("RGBA").tobytes() == original.tobytes()
                    for original in candidates),
                        f"Existing MINI PNG preserved; not overwritten: {path}")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        prepared.append((path, buffer.getvalue()))
    for path, data in prepared:
        write_generated(path, data)
    import json
    write_generated(WORK / "evidence/mini-graphics-catalogue.json", (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    count = len(selected) - sum(i in selected for i in (tutorial.CONTINUATION, demo.CONTINUATION))
    print(f"[MINI] {count} editor PNGs from {len(selected)} selected textures; existing edits preserved")


def load_edits(directory=None):
    directory = WORK / "graphics" if directory is None else Path(directory)
    sprites, selected, _ = catalogue()
    names = {Path(image_name(i)).stem.casefold() + "_kor.png" for i in selected}
    edits = {}
    for path in directory.glob("MINI*_KOR.png"):
        require(path.name.casefold() in names, f"Unknown MINI edited PNG: {path.name}")
    for index in selected:
        path = directory / image_name(index).replace(".png", "_KOR.png")
        if path.exists():
            with Image.open(path) as image:
                require(image.format == "PNG", "Edited MINI image must be PNG")
                converted = image.convert("RGBA")
                if image.mode == "P" and image.info.get("mini_original_indices") == "v1":
                    converted.info["mini_palette_indices"] = image.tobytes()
                elif (reference := palette_reference(image, sprites.textures[index].colors)) is not None:
                    converted.info["mini_palette_reference_indices"] = reference
                    converted.info["mini_palette_reference_rgba_sha256"] = reference_fingerprint(image)
                edits[index] = converted
    return edits


def plan(edits):
    sprites, selected, report = catalogue()
    require(set(edits) <= set(selected), "MINI edit is not a selected Japanese texture")
    ending_captions = ending.requested(sprites, edits)
    game_messages = messages.requested(sprites, edits)
    all_clear = messages.all_clear_requested(sprites, edits)
    menu_headings = headings.requested(sprites, edits)
    circle_titles = circles.requested(sprites, edits)
    record_titles = records.requested(sprites, edits)
    choice_captions = choices.requested(sprites, edits)
    diagnostic_captions = diagnostic_titles.requested(sprites, edits)
    screen_captions = screen_settings.requested(sprites, edits)
    raw_edits = tutorial.to_textures(sprites, edits)
    raw_edits = demo.to_textures(sprites, raw_edits)
    condition_captions = conditions.requested(sprites, raw_edits)
    raw_edits, name_table, name_tiles = diagnosis.prepare(sprites, raw_edits)
    name_table = layouts.translated_rule_table(sprites, raw_edits, name_table)
    if choice_captions:
        name_table = choices.prepare_table(sprites, name_table)
    if diagnostic_captions:
        name_table = diagnostic_titles.prepare_table(sprites, name_table)
    if screen_captions:
        name_table = screen_settings.prepare_table(sprites, name_table)
    hero = opening.HERO in edits and edits[opening.HERO].tobytes() != sprites.image(opening.HERO).tobytes()
    title = all(i in edits and edits[i].tobytes() != sprites.image(i).tobytes() for i in opening.TITLE_SHEETS)
    feedback_button = 123 in edits and edits[123].tobytes() != sprites.image(123).tobytes()
    base = Sprites({**sprites.files, "clutimg.bin": opening.hero_palette(sprites)}) if hero else sprites
    files, changes = base.rebuild({i: layouts.to_texture(sprites, i, image) for i, image in raw_edits.items()})
    demo_sources = {row['index'] for row in changes['changed']} & demo.SOURCES
    demo_trim = []
    condition_report = None
    if condition_captions:
        raw_rebuilt = Sprites({**files, 'spritetbl.bin': name_table})
        name_table, condition_report = conditions.trim_table(sprites, raw_rebuilt, name_table)
    if demo_sources:
        raw_rebuilt = Sprites({**files, 'spritetbl.bin': name_table})
        name_table, demo_trim = demo.trim_table(sprites, raw_rebuilt, demo_sources, name_table)
    files = {**files, "spritetbl.bin": name_table}
    changes["palette_and_sprite_tables_preserved"] = not hero and name_table == sprites.files["spritetbl.bin"]
    members = {"spr/" + n: raw for n, raw in files.items() if raw != sprites.files[n]}
    auxiliary = {}
    rebuilt = Sprites(files)
    preview_indices = {change["index"] for change in changes["changed"]}
    preview_indices = (preview_indices - demo.SOURCES) | demo.affected_editors(sprites, preview_indices)
    if preview_indices & {tutorial.EDITOR, tutorial.CONTINUATION}:
        preview_indices.discard(tutorial.CONTINUATION)
        preview_indices.add(tutorial.EDITOR)
    for index in sorted(preview_indices):
        buffer = io.BytesIO()
        editor_image(rebuilt, index).save(buffer, format="PNG")
        auxiliary[WORK / "evidence/graphics-rebuilt" / image_name(index)] = buffer.getvalue()
    return members, auxiliary, {"catalogue": report, "rebuild": changes, "diagnosis_name_tiles": name_tiles,
                                "opening_title_coordinates": title,
                                "feedback_button_coordinates": feedback_button,
                                "ending_captions": ending.layout(rebuilt) if ending_captions else None,
                                "game_message_captions": messages.layout(rebuilt) if game_messages else None,
                                "all_clear_caption": messages.all_clear_layout(rebuilt) if all_clear else None,
                                "menu_heading_captions": headings.layout(rebuilt, menu_headings) if menu_headings else None,
                                "circle_title_captions": circles.layout(rebuilt, circle_titles) if circle_titles else None,
                                "record_title_captions": records.layout(rebuilt) if record_titles else None,
                                "choice_captions": choices.layout(rebuilt) if choice_captions else None,
                                "diagnostic_captions": diagnostic_titles.layout(rebuilt) if diagnostic_captions else None,
                                "screen_setting_captions": screen_settings.layout(rebuilt) if screen_captions else None,
                                "demo_caption_trim": demo_trim,
                                "condition_captions": condition_report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("extract", "verify"))
    parser.add_argument("--font", type=Path)
    args = parser.parse_args()
    if args.mode == "extract":
        extract()
    else:
        from rebuild_pipeline import execute
        execute("verify", args.font)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"[ERROR] {error}")
