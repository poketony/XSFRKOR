"""Repository-facing editable TXT/PNG files, sharing the verified asset builder."""

import argparse
from pathlib import Path

import adv_text_tool as adv
import database_text as db
import exp_batdat
import exp_field
import exp_movie
import exp_executable
import exp_scripts
import exp_txd
import graphics_pipeline as graphics
import mini_dictionary
import mini_text
import prepare_originals
import rebuild_pipeline
import save_assets
import system_text
import ui_labels
import wwrom
from unpack_contents import digest

from build_paths import REPO
from unpack_disc import require


def editors():
    result = {Path(name).stem + ".txt": adv.export_text(adv.load_original(name), name) for name in adv.ORIGINALS}
    result["DATABASE.txt"] = db.export_text(db.original_data())
    for component in system_text.COMPONENTS:
        original = system_text.original_data(component)
        result[f"SYSTEM_{component}.txt"] = system_text.export_text(original, component)
        result[f"LABELS_{component}.txt"] = ui_labels.export_text(original, component)
    result["MINI_SYSTEM.txt"] = mini_text.export_system(wwrom.originals()[0])
    result["MINI_DICTIONARY.txt"] = mini_text.export_dictionary(mini_dictionary.originals())
    result[save_assets.EDITOR] = save_assets.export_title()
    exp = {**exp_scripts.editors(), **exp_txd.editors(), **exp_field.editors(), **exp_movie.editors()}
    names = {exp_scripts.editor_name(name): text for name, text in exp.items()}
    require(len(names) == len(exp) and not names.keys() & result.keys(), "EXP editor filenames collide")
    result.update(names)
    result[exp_scripts.editor_name(exp_batdat.NAME)] = exp_batdat.export_editor(exp_batdat.original())
    require(exp_executable.EDITOR not in result, "EXP embedded editor filename collides")
    result[exp_executable.EDITOR] = exp_executable.export_editor(exp_executable.originals())
    return result


def extract(workspace):
    workspace = Path(workspace).resolve()
    require(workspace == REPO.resolve(), "Asset workspace must be this repository")
    texts = editors()
    scripts, _ = exp_scripts.sb.originals()
    exp_sources = {exp_scripts.editor_name(name): (exp_scripts.sb, name, data) for name, data in scripts.items()}
    exp_sources.update({exp_scripts.editor_name(name): (exp_txd, name, data) for name, data in exp_txd.originals().items()})
    exp_sources.update({exp_scripts.editor_name(name): (exp_field, name, data) for name, data in exp_field.originals().items()})
    exp_sources.update({exp_scripts.editor_name(name): (exp_movie, name, data) for name, data in exp_movie.originals().items()})
    battle_source = exp_batdat.original()
    for name, content in texts.items():
        target = workspace / "text" / name
        if target.exists():
            require(target.is_file(), f"TXT destination is not a file: {target}")
            # Existing translations are the user's input, never extraction output.
            current = target.read_text(encoding="utf-8")
            if name.startswith("SCEN"):
                adv.parse_editor(current, adv.load_original(Path(name).with_suffix(".DAT").name),
                                 Path(name).with_suffix(".DAT").name)
            elif name == "DATABASE.txt":
                db.parse_editor(current, db.original_data())
            elif name.startswith("SYSTEM_"):
                component = Path(name).stem.removeprefix("SYSTEM_")
                system_text.parse_editor(current, system_text.original_data(component), component)
            elif name.startswith("LABELS_"):
                component = Path(name).stem.removeprefix("LABELS_")
                ui_labels.parse_editor(current, system_text.original_data(component), component)
            elif name == "MINI_SYSTEM.txt":
                source = wwrom.originals()[0]
                mini_text.parse_editor(current, "SYSTEM", digest(source),
                                       ((item.address, item.raw) for item in mini_text.system_catalogue(source)))
            elif name == "MINI_DICTIONARY.txt":
                dictionary = mini_dictionary.originals()
                mini_text.parse_editor(current, "DICTIONARY", mini_text.dictionary_hash(dictionary),
                                       ((word.word_id, word.definition) for word in dictionary.words))
            elif name == save_assets.EDITOR:
                save_assets.parse_title(current)
            elif name == exp_scripts.editor_name(exp_batdat.NAME):
                exp_batdat.parse_editor(current, battle_source)
            elif name == exp_executable.EDITOR:
                exp_executable.parse_editor(current, exp_executable.originals())
            else:
                module, source_name, data = exp_sources[name]
                module.parse_editor(current, data, source_name)
    for name, content in texts.items():
        target = workspace / "text" / name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
    graphics.extract(workspace / "graphics")
    print(f"[PASS] {len(texts)} TXT files; existing text/PNG edits were not overwritten")


def main(workspace=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "extract", "verify", "rebuild"))
    parser.add_argument("--font", type=Path)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--iso", type=Path)
    source.add_argument("--source-tree", type=Path)
    args = parser.parse_args()
    workspace = REPO if workspace is None else Path(workspace)
    if args.mode == "prepare":
        if args.iso is None and args.source_tree is None or args.font is not None:
            parser.error("prepare requires --iso or --source-tree and does not take --font")
        prepare_originals.prepare(args.iso, args.source_tree, root=workspace)
        return
    if args.iso is not None or args.source_tree is not None:
        parser.error("--iso and --source-tree are only for prepare")
    prepare_originals.check()
    if args.mode == "extract":
        extract(workspace)
    else:
        rebuild_pipeline.execute(args.mode, args.font, workspace=workspace)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"[ERROR] {error}")
