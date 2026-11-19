"""Build supported Freaks assets with paired resources and engine-specific fonts."""

import argparse
import json
from pathlib import Path
import sys
from time import monotonic

import adv_text_tool as adv
import database_text as db
import exp_batdat
import exp_field
import exp_movie
import exp_executable
import exp_font
import exp_graphics
import exp_scripts
import exp_txd
from database_native import verify as verify_database
from font_tool import DEFAULT_FONT as DEFAULT_SHARED_FONT
from font_tool import font_container, missing_characters, original_font, validate_display_text, write_generated
from gothic import Gothic
import graphics_pipeline as graphics
import system_text as system
from system_native import verify as verify_system
import ui_labels
import mini_text
import mini_validation_cache
import mini_graphics
import save_assets
import gothic_punctuation as punctuation
import database_justify
from label_native import verify as verify_labels, verify_notice
from unpack_contents import digest

from build_paths import CAMPAIGN_MODULES, REPO, WORK
from adv_format import encode_text, require


DEFAULT_FONTS = {
    "shared": DEFAULT_SHARED_FONT,
    "mini": mini_text.mf.DEFAULT_FONT,
    "exp": exp_font.DEFAULT_FONT,
}


def plan(adv_editors, database_editor, graphics_edits, font_path=None, *, system_editors=None, label_editors=None,
         mini_editors=None, mini_graphics_edits=None, exp_editors=None, exp_txd_editors=None,
         exp_field_editors=None, exp_batdat_editor=None, exp_movie_editors=None, exp_executable_editor=None,
         exp_graphics_edits=None, save_icon_edit=None, mini_save_editor=None, progress=None, mini_campaign=None):
    if progress is None:
        progress = lambda message: None
    fonts = {name: font_path if font_path is not None else path for name, path in DEFAULT_FONTS.items()}
    require(system_editors is None or set(system_editors) == set(system.COMPONENTS),
            "System editor set must contain exactly LNCH and ADV")
    require(label_editors is None or set(label_editors) == set(system.COMPONENTS),
            "Label editor set must contain exactly LNCH and ADV")
    require(mini_editors is None or set(mini_editors) == {"SYSTEM", "DICTIONARY"},
            "MINI editor set must contain exactly SYSTEM and DICTIONARY")
    require((exp_editors is None) == (exp_txd_editors is None), "EXP SB and TXD editors must be supplied together")
    require(exp_editors is not None or (exp_field_editors is None and exp_batdat_editor is None
                                      and exp_movie_editors is None and exp_executable_editor is None),
            "EXP field/battle/movie/executable text needs the EXP base editors")
    progress("[2/9] ADV text and shared font")
    original = db.original_data()
    translations = db.parse_editor(database_editor, original)
    systems = {}
    for component in system.COMPONENTS:
        source = system.original_data(component)
        editor = system_editors[component] if system_editors is not None else system.export_text(source, component)
        systems[component] = source, system.parse_editor(editor, source, component)
    extra_characters = missing_characters(translations.values())
    if any(translations.values()):
        extra_characters.update(missing_characters(korean for _, _, korean in db.CATEGORY_HEADERS))
    extra_characters.update(missing_characters(text for _, texts in systems.values() for text in texts.values()))
    labels = {}
    for component, (source, _) in systems.items():
        editor = label_editors[component] if label_editors is not None else ui_labels.export_text(source, component)
        labels[component] = ui_labels.parse_editor(editor, source, component)
        extra_characters.update(missing_characters(labels[component].values()))
    prepared, adv_report, font_report = adv.plan_rebuild(
        adv_editors, fonts["shared"], extra_characters=extra_characters, font_size=17 if font_path is None else 18,
        measure=punctuation.dialogue_width)
    mapping = {char: bytes.fromhex(code) for char, code in font_report["mapping"].items()} if font_report else {}
    font_data = next((data for path, data in prepared if path == WORK / "font/GOTHIC.bin"), None)
    if font_data is None:
        font_data = original_font()
    punctuation_font, punctuation_report = punctuation.adjust(font_data)
    dialogue_font, dialogue_report = punctuation.adjust(font_data, dialogue=True)
    translated_database = any(translations.values())
    narrow_fonts = {WORK / "rebuilt/ADV/DAT00/GOTHIC.DAT.new": font_container(dialogue_font)}
    if translated_database:
        narrow_fonts[WORK / "rebuilt/DTB/DAT00/GOTHIC.DAT.new"] = font_container(punctuation_font)
    prepared = [(path, narrow_fonts.get(path, raw)) for path, raw in prepared]
    if font_report is not None:
        font_report["punctuation"] = {**punctuation_report,
                                     "components": ["ADV", "DTB"] if translated_database else ["ADV"],
                                     "decoded_sha256": digest(punctuation_font),
                                     "adv_dialogue": {**dialogue_report, "decoded_sha256": digest(dialogue_font)}}
    progress("[3/9] Database and system text")
    database_font = punctuation_font if translated_database else font_data
    database_measure = punctuation.width if translated_database else db.width
    database_translations, database_mapping = translations, mapping
    justified = None
    if translated_database:
        justified = database_justify.prepare(translations, mapping, database_font, database_measure)
        database_font, database_measure = justified.font, justified.measure
        database_translations, database_mapping = justified.translations, justified.mapping
        database_container = font_container(database_font)
        prepared = [(path, database_container if path == WORK / "rebuilt/DTB/DAT00/GOTHIC.DAT.new" else raw)
                    for path, raw in prepared]
    patched, database_report = db.rebuild(original, database_translations, database_mapping,
                                         measure=database_measure, compact=translated_database)
    if translated_database:
        patched = db.patch_category_headers(patched, mapping)
        database_report["typography"] = punctuation_report
        database_report["justification"] = justified.report
    font = Gothic(font_data)
    for translations_by_component in labels.values():
        for text in translations_by_component.values():
            if text:
                validate_display_text(encode_text(text, mapping), font)
    database_face = Gothic(database_font)
    for entry in db.read_entries(patched, measure=database_measure):
        for raw in entry.texts:
            validate_display_text(raw, database_face)
    elf_overrides = {"DTB": patched}
    system_report = {}
    label_report = {}
    for component, (source, texts) in systems.items():
        measure = punctuation.dialogue_width if component == "ADV" else db.width
        output, info, label_report[component] = ui_labels.rebuild(source, texts, labels[component], mapping, component,
                                                               measure=measure)
        elf_overrides[component] = output
        for item in system.catalogue(source, component):
            if item.kind != "bios" and item.address in texts and texts[item.address]:
                validate_display_text(encode_text(texts[item.address], mapping), font)
        system_report[component] = info
    progress("[4/9] LNCH / ADV / DTB graphics")
    outputs, graphics_report = graphics.plan(graphics_edits, elf_overrides=elf_overrides, progress=progress,
                                             database_measure=database_measure)
    progress("[5/9] Final system, labels and database validation")
    for component, (source, _) in systems.items():
        final = outputs.get(system.COMPONENTS[component]["file"], source)
        component_font = dialogue_font if component == "ADV" else font_data
        system_report[component]["native"] = verify_system(source, final, component, component_font)
        validator = verify_labels if component == "ADV" else verify_notice
        label_report[component]["native"] = validator(source, final, component_font)
    final_database = outputs.get("DTB/DTB.ELF", original)
    database_report["native"] = verify_database(final_database, database_font)
    auxiliary = graphics.previews(outputs, graphics_report)
    progress("[6/9] MINI text, font and graphics")
    mini_outputs, mini_auxiliary, mini_report = mini_text.plan(
        system_editor=mini_editors["SYSTEM"] if mini_editors is not None else None,
        dictionary_editor=mini_editors["DICTIONARY"] if mini_editors is not None else None, font_path=fonts["mini"],
        graphics_edits=mini_graphics_edits, save_editor=mini_save_editor)
    if mini_campaign is not None:
        require(mini_campaign in ("verify", "rebuild"), "Unknown MINI campaign build mode")
        progress("[6/9] MINI Korean campaign and versus stages")
        sys.path.insert(0, str(CAMPAIGN_MODULES))
        from build_958 import CAMPAIGN, plan as plan_campaign
        from boot_safety_958 import require_safe
        if mini_outputs:
            elf, rom = mini_outputs["MINI/MINI.ELF"], mini_outputs["MINI/WWROM.DAT"]
        else:
            from wwrom import originals
            elf, rom = originals()
        elf, rom, dictionary, stages, campaign = plan_campaign(CAMPAIGN, base=(elf, rom, mini_report))
        mini_outputs = {"MINI/MINI.ELF": elf, "MINI/WWROM.DAT": rom}
        versus_count = sum(p.get("mode") == "versus" for p in campaign["profiles"])
        summary = mini_report["campaign"] = {
            "solo": len(campaign["profiles"]) - versus_count, "versus": versus_count,
            "alphabet": campaign["alphabet_count"], "definitions": campaign["dictionary_layout"]["definitions"],
            "original_demo_stages": sorted(campaign["patch"]["demo_remaps"]),
            "korean_demo_stages": sorted(row["stage"] for row in campaign["patch"].get("korean_demos", ())),
            "inputs": campaign["source_sha256"],
            "definition_spacing": campaign["patch"]["definition_spacing"],
            "condition_spacing": campaign["patch"]["condition_spacing"],
            "random_personality": campaign["patch"]["random_personality"],
            "final_definition_layout": campaign["final_definition_layout"],
            "word_popup_layout": campaign["word_popup_layout"],
            "final_resource_heap": campaign["final_resource_heap"],
        }
        if mini_campaign == "verify":
            from verify_958 import verify as verify_campaign
            checked = verify_campaign(elf, rom, dictionary, stages, campaign)
            summary.update(route_moves=len(checked["native_route_steps"]), versus_openings=len(checked["versus"]),
                           native_dictionary_reads=checked["candidates"]["native_candidate_and_definition_reads"],
                           cursor_transfers=checked["cursor_transfers"])
            progress(f"[CHECK] {summary['route_moves']} MINI solo moves; {summary['versus_openings']} versus openings; "
                     f"{summary['cursor_transfers']} cursor transfers")
        else:
            require_safe(elf)
    require(not set(mini_outputs) & set(outputs) and not set(mini_auxiliary) & set(auxiliary),
            "Conflicting MINI pipeline output")
    outputs.update(mini_outputs)
    auxiliary.update(mini_auxiliary)
    progress("[7/9] EXP text, fonts and graphics")
    exp_report = None
    if exp_editors is not None:
        exp_outputs, exp_report = exp_scripts.plan_rebuild(exp_editors, fonts["exp"], txd_editors=exp_txd_editors,
                                                          field_editors=exp_field_editors, batdat_editor=exp_batdat_editor,
                                                          movie_editors=exp_movie_editors,
                                                          executable_editor=exp_executable_editor)
        require(not set(exp_outputs) & set(outputs), "Conflicting EXP pipeline output")
        outputs.update(exp_outputs)
    exp_graphics_outputs, exp_graphics_report = exp_graphics.plan(exp_graphics_edits or {})
    require(not set(exp_graphics_outputs) & set(outputs), "Conflicting EXP text/graphics output")
    outputs.update(exp_graphics_outputs)
    progress("[8/9] Save icon and output checks")
    save_outputs, save_report = save_assets.plan_icon(save_icon_edit)
    require(not set(save_outputs) & set(outputs), "Conflicting save-icon output")
    outputs.update(save_outputs)
    for path, data in prepared:
        if path.is_relative_to(WORK / "rebuilt"):
            name = path.relative_to(WORK / "rebuilt").as_posix().removesuffix(".new")
            require(name not in outputs, f"Conflicting pipeline output: {name}")
            outputs[name] = data
        else:
            auxiliary[path] = data
    report = {"scope": "ADV scripts, launcher/ADV labels, DTB database, system messages/options, shared GOTHIC, selected LNCH/ADV/DTB graphics, shared browser icon, MINI definitions/audited UI and browser title, separate font and selected sprites, EXP SB/BIN/TXD/slot writer, field/battle/movie/embedded text, paired OV02, movie allocation, separate fonts and selected EXP graphics",
              "adv": adv_report, "database": database_report, "system": system_report, "labels": label_report, "font": font_report,
              "graphics": graphics_report, "mini": mini_report, "exp": exp_report,
              "exp_graphics": exp_graphics_report,
              "save_icon": save_report,
              "outputs": [{"path": name, "size": len(data), "sha256": digest(data)} for name, data in sorted(outputs.items())]}
    return outputs, auxiliary, report


def current_inputs(workspace=REPO):
    editors = {name: (workspace / "text" / (Path(name).stem + ".txt")).read_text(encoding="utf-8") for name in adv.ORIGINALS}
    return editors, (workspace / "text/DATABASE.txt").read_text(encoding="utf-8"), graphics.load_edits(workspace / "graphics")


def execute(mode, font_path=None, *, workspace=REPO):
    require(mode in ("verify", "rebuild"), "Unknown pipeline mode")
    workspace = Path(workspace).resolve()
    require(workspace == REPO.resolve(), "Asset workspace must be this repository")
    started = monotonic()

    def progress(message):
        print(f"[{monotonic() - started:7.1f}s] {message}", flush=True)

    progress(f"[START] {mode.upper()}: {workspace}")
    progress("[1/9] Reading TXT and PNG inputs")
    systems = {c: (workspace / "text" / system.editor_path(c).name).read_text(encoding="utf-8") for c in system.COMPONENTS}
    labels = {c: (workspace / "text" / ui_labels.editor_path(c).name).read_text(encoding="utf-8") for c in system.COMPONENTS}
    mini = {"SYSTEM": (workspace / "text/MINI_SYSTEM.txt").read_text(encoding="utf-8"),
            "DICTIONARY": (workspace / "text/MINI_DICTIONARY.txt").read_text(encoding="utf-8")}
    exp = {name: (workspace / "text" / exp_scripts.editor_name(name)).read_text(encoding="utf-8")
           for name in exp_scripts.editors()}
    menus = {name: (workspace / "text" / exp_scripts.editor_name(name)).read_text(encoding="utf-8")
             for name in exp_txd.TABLES}
    fields = {name: (workspace / "text" / exp_scripts.editor_name(name)).read_text(encoding="utf-8")
              for name in exp_field.PROFILES}
    battle = (workspace / "text" / exp_scripts.editor_name(exp_batdat.NAME)).read_text(encoding="utf-8")
    movies = {name: (workspace / "text" / exp_scripts.editor_name(name)).read_text(encoding="utf-8")
              for name in exp_movie.editors()}
    embedded = (workspace / "text" / exp_executable.EDITOR).read_text(encoding="utf-8")
    with mini_validation_cache.reuse(mode == "rebuild", progress) as checks:
        outputs, auxiliary, report = plan(*current_inputs(workspace), font_path=font_path, system_editors=systems,
                                      label_editors=labels, mini_editors=mini,
                                      mini_graphics_edits=mini_graphics.load_edits(workspace / "graphics"),
                                      exp_editors=exp, exp_txd_editors=menus,
                                      exp_field_editors=fields, exp_batdat_editor=battle, exp_movie_editors=movies,
                                      exp_executable_editor=embedded,
                                      exp_graphics_edits=exp_graphics.load_edits(workspace / "graphics"),
                                      save_icon_edit=save_assets.load_edit(workspace / "graphics"),
                                      mini_save_editor=(workspace / "text" / save_assets.EDITOR).read_text(encoding="utf-8"),
                                          progress=progress, mini_campaign=mode)
    if checks is not None:
        report["validation_cache"] = {"scope": "MINI native definitions, dictionary records, whole-resource heap and sprites",
                                      "reused": checks.hits, "fresh": checks.misses}
    if mode == "rebuild":
        progress("[9/9] Writing verified files and reports")
        for index, (name, data) in enumerate(outputs.items(), 1):
            write_generated(workspace / "rebuilt" / name, data)
            progress(f"[WRITE {index}/{len(outputs)}] {name}")
        write_generated(WORK / "evidence/pipeline-rebuild.json",
                        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        write_generated(workspace / "build.json",
                        (json.dumps({"format": "FREAKS-ASSET-OUTPUTS-v1", "outputs": report["outputs"]},
                                    ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    else:
        progress("[9/9] Verification complete; no files written")
    print(f"[ADV] {sum(row['changed'] for row in report['adv'])} changed texts")
    print(f"[DATABASE] {len(report['database']['changed'])} changed texts; {len(report['database']['moved'])} relocated")
    print(f"[SYSTEM] {sum(len(row['changed']) for row in report['system'].values())} changed texts")
    print(f"[LABELS] {sum(len(row['changed']) for row in report['labels'].values())} changed texts")
    print(f"[GRAPHICS] {sum(len(row['changed']) for row in report['graphics'])} changed images")
    print(f"[MINI] {len(report['mini']['system']['changed'])} UI texts; "
          f"{len(report['mini']['dictionary']['changed'])} definitions; "
          f"{report['mini']['font']['characters']}/{report['mini']['font'].get('safe_code_budget', 2350)} Hangul glyphs used")
    print(f"[MINI GRAPHICS] {len(report['mini']['graphics']['rebuild']['changed'])} changed images")
    campaign = report["mini"]["campaign"]
    print(f"[MINI PUZZLES] {campaign['solo']} solo and {campaign['versus']} versus stages; "
          f"{campaign['alphabet']} available Hangul tile IDs")
    print(f"[EXP] {len(report['exp']['scripts'])} changed scripts; "
          f"{sum(len(row['changed']) for row in report['exp']['txd'])} changed menu texts; "
          f"slot writer patched: {report['exp']['slot_writer_patched']}")
    print(f"[EXP FIELD] {sum(len(row['changed']) for row in report['exp']['fields'])} changed fields; "
          "ASCII spaces converted to fullwidth in changed fields")
    print(f"[EXP BATDAT] {len(report['exp']['batdat']['changed'])} changed name/description/message fields")
    print(f"[EXP MOVIE] {sum(len(row['changed']) for row in report['exp']['movies'])} changed captions; "
          f"movie allocation patched: {report['exp']['movie_loader_patched']}")
    print(f"[EXP EXECUTABLE] {len(report['exp']['executable']['changed'])} changed texts; "
          f"paired OV02: {report['exp']['executable']['paired_ov02']}")
    print(f"[EXP GRAPHICS] {len(report.get('exp_graphics', {}).get('changed', []))} changed images")
    print(f"[SAVE] Shared icon changed: {report.get('save_icon', {}).get('changed', False)}; "
          f"MINI browser title changed: {report['mini'].get('save', {}).get('changed', False)}")
    print(f"[PASS] {len(outputs)} outputs; shared font, database links and GRP/ELF references agree")
    print("[NOTE] Apply the listed outputs manually, then use the independent ROM builder.")
    if mode == "verify":
        print("[NO OUTPUT]")
    progress("[DONE] All stages completed")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("verify", "rebuild"))
    parser.add_argument("--font", type=Path)
    args = parser.parse_args()
    execute(args.mode, args.font)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"[ERROR] {error}")
