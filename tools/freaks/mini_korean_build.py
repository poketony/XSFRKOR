"""Lab-only stage-local Korean MINI build, preserving untranslated stages."""

import argparse
import csv
import io
import json
import struct
import tomllib
from types import SimpleNamespace

from font_tool import write_generated
import mini_dictionary as md
import mini_lexicon_source as corpus
import mini_korean_runtime as runtime
import mini_korean_tiles as tiles
from mini_lexicon import Lexicon, build_index
import mini_sprites
import mini_graphics
import mini_text
from mini_stages import Stages
from unpack_contents import digest, wwrom_parts

from build_paths import DATA, REPO, WORK
from unpack_disc import require
import wwrom
import xs2_hangul


ROOT = DATA / "mini"
WORDS = ROOT / "StageWords.tsv"
PROFILES = ROOT / "KoreanStages.toml"
IDS = ROOT / "KoreanWordIds.tsv"


def tsv(data, fields):
    reader = csv.DictReader(io.StringIO(data), delimiter="\t")
    require(tuple(reader.fieldnames or ()) == fields, "Korean build TSV columns differ")
    rows = list(reader)
    require(rows and all(set(r) == set(fields) and all(isinstance(v, str) for v in r.values()) for r in rows),
            "Malformed Korean build TSV")
    return rows


def word_rows(original, source, content):
    fields = ("word", "source_id", "source_sense", "jp_reference", "definition")
    header = tuple(next(csv.reader(io.StringIO(content), delimiter="\t"), ()))
    require(header in (fields, (*fields, "display"), (*fields, "pronunciation"),
                       (*fields, "display", "pronunciation"), (*fields, "pronunciation", "display")),
            "Korean word-editor columns differ")
    rows = tsv(content, header)
    by_id = {(r["entry_id"], r["sense_id"]): r for r in source}
    supported = {c for c, _ in xs2_hangul.rows()}
    for row in rows:
        display = row.get("display") or row["word"].strip()
        row["word"] = corpus.search_key(row["word"])
        if row["source_id"].startswith("JPN:"):
            reference = row["source_id"].removeprefix("JPN:")
            require(reference.isdecimal() and 1 <= int(reference) <= len(original.words)
                    and reference == row["jp_reference"] and row["source_sense"] == "1",
                    "Original-game term source/reference differs")
        else:
            source_row = by_id.get((row["source_id"], row["source_sense"]))
            require(source_row is not None and source_row["key"] == row["word"],
                    f"Korean word source does not match: {row['word']}")
            source_display = source_row["display"]
            if not row.get("display") and any(c.isspace() for c in source_display):
                display = source_display
        minimum = 1 if row["source_id"].startswith("JPN:") else 2
        require(minimum <= len(row["word"]) <= 9, "Korean word needs 2..9 syllables (original names allow 1)")
        require(set(row["word"]) <= supported and row["definition"]
                and all(c in supported or c == "\u3000" or 32 <= ord(c) <= 126 for c in row["definition"]),
                "Korean word/definition uses an unsupported character")
        require("<sp_no" not in row["definition"].lower() and "</sp_no" not in row["definition"].lower(),
                "Korean definition still contains source-reference metadata")
        require(len(row["definition"].encode("euc_kr")) <= 215, "Korean definition record is too long")
        require(all(c in supported or 32 <= ord(c) <= 126 for c in display)
                and len(display.encode("euc_kr")) <= 18,
                f"Korean word display exceeds its original 18-byte field: {display!r} (key {row['word']})")
        row["display"] = display
        if "pronunciation" in row:
            pronunciation = row["pronunciation"]
            require(pronunciation and pronunciation == pronunciation.strip()
                    and all(c in supported or c == " " for c in pronunciation)
                    and len(pronunciation.encode("euc_kr")) <= 18,
                    f"Korean pronunciation needs Hangul/spaces within 18 bytes: {row['word']}")
        require(row["jp_reference"].isdecimal() and 1 <= int(row["jp_reference"]) <= len(original.words),
                "Japanese category reference is not an original word ID")
    require(len({r["word"] for r in rows}) == len(rows), "Duplicate Korean word editor row after space normalization")
    return rows


def word_ids(original_count, rows, previous=None):
    words = {r["word"] for r in rows}
    if previous is None:
        assigned = {}
    else:
        parsed = tsv(previous, ("word", "word_id"))
        require(all(r["word_id"].isdecimal() for r in parsed), "Invalid Korean word ID")
        assigned = {r["word"]: int(r["word_id"]) for r in parsed}
        require(len(assigned) == len(parsed) and set(assigned) <= words, "Do not remove a recorded Korean word ID")
        require(sorted(assigned.values()) == list(range(original_count + 1, original_count + 1 + len(assigned))),
                "Korean word ID ledger is not contiguous")
    for word in sorted(words - assigned.keys()):
        assigned[word] = original_count + len(assigned) + 1
    require(original_count + len(assigned) < 1 << 20, "Korean word IDs exceed the native range")
    return assigned


def dictionary(original, rows, ids):
    data = bytearray(original.data)
    mapping = xs2_hangul.mapping([c for c, _ in xs2_hangul.rows()], "euc_jp")
    encode = lambda text: mini_text.encode(text, mapping)
    offsets = list(struct.unpack(f"<{len(original.words)}I", original.files["dicdata.wix"]))
    for row in sorted(rows, key=lambda r: ids[r["word"]]):
        require(ids[row["word"]] == len(offsets) + 1, "Korean word ID assignment changed")
        offsets.append(len(data))
        word, definition = encode(row["word"]), encode(row["definition"])
        display = encode(row.get("display") or row["word"])
        # DIX owns the search key. These two WDT fields are the panel title and
        # bracketed text; keep legacy editors unchanged until explicitly migrated.
        title, bracket = (display, encode(row["pronunciation"])) if "pronunciation" in row else (word, display)
        require(len(title) <= 18 and len(bracket) <= 18 and len(definition) <= 215,
                "Dictionary text exceeds a native WDT field")
        category = original.words[int(row["jp_reference"]) - 1].category
        data.extend(bytes((40 + len(definition), category)) + title.ljust(18, b"\0")
                    + bracket.ljust(18, b"\0") + definition + b"\0\0")
    require(len(data) < 1 << 24, "Korean WDT exceeds the native offset range")
    files = dict(original.files)
    files["dicdata.wix"] = struct.pack(f"<{len(offsets)}I", *offsets)
    files["dicdata.wdt"] = bytes(data)
    header = bytearray(original.files["dicdata.dhd"])
    struct.pack_into("<4I", header, 12, len(original.index), len(files["dicdata.wix"]), len(data), len(offsets))
    files["dicdata.dhd"] = bytes(header)
    checked = md.Dictionary(files)
    require(checked.words[:len(original.words)] == original.words and checked.index == original.index,
            "Original Japanese dictionary changed")
    return checked


def stage_bytes(stage, profile, tokens, *, goal_records=None):
    require(set(profile) == {"index", "name", "description", "hints", "preplaced", "inventory", "route"},
            "Korean stage fields differ")
    require(len(profile["hints"]) == 2 and len(profile["preplaced"]) == sum(map(len, stage.board.pieces)),
            "Korean stage hints/initial piece count differs")
    raw = bytearray(stage.data[:stage.inventory_offset + 4])
    require(0 < len(profile["inventory"]) <= 65535, "Invalid Korean supply count")
    raw.extend(bytes(tokens[c] for c in profile["inventory"]))
    struct.pack_into("<I", raw, 8, len(raw))
    struct.pack_into("<H", raw, stage.inventory_offset + 2, len(profile["inventory"]))
    supported = {c for c, _ in xs2_hangul.rows()}
    for at, size, text in ((0x1c, 46, profile["description"]), (0x4a, 18, profile["name"]),
                           (0xa0, 64, profile["hints"][0]), (0xe0, 64, profile["hints"][1])):
        require(isinstance(text, str) and all(c in supported or 32 <= ord(c) <= 126 for c in text),
                "Korean stage text uses an unsupported character")
        encoded = text.encode("euc_kr")
        require(len(encoded) < (42 if at == 0x1c else size), "Korean stage text exceeds the original field")
        raw[at:at + size] = encoded.ljust(size, b"\0")
    groups_at = struct.unpack_from("<I", stage.board.data, 68)[0]
    directory = struct.unpack_from("<8I", stage.board.data, 64 + groups_at)
    letters = iter(profile["preplaced"])
    for group, relative in zip(stage.board.pieces, directory):
        for i, _ in enumerate(group):
            raw[304 + 68 + groups_at + relative + i * 8] = tokens[next(letters)]
    # Explicit word goals must be translated explicitly, never silently reinterpreted.
    require(goal_records is None or len(goal_records) == len(stage.goals),
            "Korean goal variant count differs")
    for i, goal in enumerate(stage.goals):
        translated = goal_records[i] if goal_records is not None else None
        require((goal[0] in (9, 10)) == (translated is not None),
                "Goal kinds 9/10 need a supported explicit translated target")
        if translated is not None:
            require(len(translated) == 16 and translated[:2] == goal[:2] and translated[3] == 0,
                    "Korean target changes the original goal kind or count")
            at = 0x68 + i * 16
            raw[at:at + 16] = translated
    return bytes(raw)


def rebuild_stages(source, replacements, extra=()):
    records = [replacements.get(s.index, s.data) for s in source.stages] + list(extra)
    base = 512 + len(records) * 4
    data = bytearray(source.data[:512]) + bytearray(base - 512)
    struct.pack_into("<2I", data, 12, len(records), base - 512)
    for index, raw in enumerate(records):
        relative = len(data) - base
        struct.pack_into("<I", data, 512 + index * 4, relative)
        data.extend(raw)
        data.extend(b"\0" * (-(len(data) - base) % 512))
    checked = Stages(bytes(data))
    require([s.data for s in checked.stages] == records, "Rebuilt stage content differs")
    return checked


def build():
    source = corpus.common_rows()
    base_outputs, _, base_report = mini_text.plan(
        system_editor=(REPO / "text/MINI_SYSTEM.txt").read_text(encoding="utf-8"),
        dictionary_editor=(REPO / "text/MINI_DICTIONARY.txt").read_text(encoding="utf-8"),
        graphics_edits=mini_graphics.load_edits(REPO / "graphics"),
        save_editor=(REPO / "text/MINI_SAVE.txt").read_text(encoding="utf-8"))
    source_elf, source_rom = wwrom.originals()
    source_elf = base_outputs.get("MINI/MINI.ELF", source_elf)
    source_rom = base_outputs.get("MINI/WWROM.DAT", source_rom)
    base_files = {r["name"]: raw for r, raw in wwrom_parts(source_elf, source_rom)}
    original = md.Dictionary({name: base_files["dic/" + name] for name in md.NAMES})
    rows = word_rows(original, source, WORDS.read_text(encoding="utf-8"))
    ids = word_ids(len(original.words), rows, IDS.read_text(encoding="utf-8") if IDS.exists() else None)
    compiled = dictionary(original, rows, ids)
    config = tomllib.loads(PROFILES.read_text(encoding="utf-8"))
    require(set(config) == {"version", "stages"} and config["version"] == 1 and config["stages"], "Invalid Korean stage config")
    source_stages = Stages(base_files["stage/1p.stg"])
    sprites = mini_sprites.Sprites({name: base_files["spr/" + name] for name in mini_sprites.NAMES})
    replacements, runtime_profiles, profiles = {}, [], []
    candidates = {r["key"] for r in source}
    candidates.update(row["word"] for row in rows if row["source_id"].startswith("JPN:"))
    for profile in config["stages"]:
        index = profile["index"]
        require(type(index) is int and 0 <= index < len(source_stages.stages) and index not in replacements,
                "Duplicate/invalid translated stage index")
        alphabet = sorted(set(profile["preplaced"] + profile["inventory"]), key=lambda c: c.encode("euc_kr"))
        require(0 < len(alphabet) <= 72, "A Korean stage exceeds 72 syllables")
        tokens = {c: i for i, c in enumerate(alphabet)}
        projected = sorted(w for w in candidates if set(w) <= set(alphabet))
        missing = sorted(set(projected) - ids.keys())
        require(projected and not missing,
                f"StageWords.tsv lacks {len(missing)} common-corpus candidates: {', '.join(missing[:20])}")
        dix = build_index(((bytes(tokens[c] for c in w), ids[w]) for w in projected), len(compiled.words))
        decoded = Lexicon(SimpleNamespace(index=dix, words=compiled.words))
        require({"".join(alphabet[t] for t in entry.symbols) for entry in decoded.entries} == set(projected)
                and all(list(entry.word_ids) == [ids["".join(alphabet[t] for t in entry.symbols)]]
                        for entry in decoded.entries), "Korean DIX differs from the complete projected vocabulary")
        handles = tiles.native_handles(source_elf, len(alphabet))
        korean_sprites, tile_report = tiles.build_tiles(sprites, alphabet, handles)
        textures = []
        for texture in tile_report["textures"]:
            item = sprites.textures[texture]
            length = item.width * item.height
            pixels = korean_sprites.files["textureimg.bin"][item.offset:item.offset + length]
            require(len(pixels) == length, "Korean texture read bank is truncated")
            textures.append((item.offset, pixels))
        replacements[index] = stage_bytes(source_stages.stages[index], profile, tokens)
        runtime_profiles.append({"stage": index, "dix": dix, "textures": textures})
        profiles.append({**profile, "alphabet": alphabet, "tokens": tokens, "words": projected,
                         "tile_report": tile_report})
    stages = rebuild_stages(source_stages, replacements)
    edits = {"dic/" + name: raw for name, raw in compiled.files.items()}
    edits["stage/1p.stg"] = stages.data
    elf, rom, archive = wwrom.rebuild(source_elf, source_rom, edits)
    elf, patch = runtime.install(elf, runtime_profiles)
    readback = {r["name"]: raw for r, raw in wwrom_parts(elf, rom)}
    require(all(readback[name] == edits.get(name, data) for name, data in base_files.items()),
            "Final Korean build lost an existing text/font/graphics/save edit")
    return elf, rom, compiled, stages, {"profiles": profiles, "patch": patch, "word_ids": ids,
                                      "archive": archive, "base_editors": base_report,
                                      "vocabulary_sha256": {p.name: digest(p.read_bytes())
                                                            for p in (corpus.LOOKUP,)},
                                      "word_editor_sha256": digest(WORDS.read_bytes()), "profile_sha256": digest(PROFILES.read_bytes())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Write the verified lab ELF/WWROM pair")
    args = parser.parse_args()
    print("[BUILD] Compiling paired dictionary, stage, font and runtime banks...", flush=True)
    elf, rom, dictionary, stages, report = build()
    print("[CHECK] Running original stage, word and graphics code...", flush=True)
    from mini_korean_build_native import verify
    report["native"] = verify(elf, rom, dictionary, stages, report)
    if args.write:
        out = ROOT / "rebuilt"
        write_generated(out / "MINI.ELF", elf)
        write_generated(out / "WWROM.DAT", rom)
        require((out / "MINI.ELF").read_bytes() == elf and (out / "WWROM.DAT").read_bytes() == rom,
                "Written Korean build pair differs")
        ledger = "word\tword_id\n" + "".join(f"{w}\t{i}\n" for w, i in sorted(report["word_ids"].items(), key=lambda r: r[1]))
        write_generated(IDS, ledger.encode("utf-8"))
    report["outputs"] = {"MINI.ELF": {"bytes": len(elf), "sha256": digest(elf)},
                         "WWROM.DAT": {"bytes": len(rom), "sha256": digest(rom)}}
    report["written_to_lab"] = args.write
    report["scope"] = "Translated profiles only; original Japanese DIX/records/sprite resources retained. Emulator, full scoring/clear and diagnosis results remain for playtesting."
    write_generated(WORK / "evidence/mini-korean-build.json", (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"[PASS] {len(report['profiles'])} Korean stage profiles / {len(report['word_ids'])} words; lab output={args.write}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"[ERROR] {error}")
