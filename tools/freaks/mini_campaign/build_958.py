"""Build a separate playable MINI pair; keep every existing editor and build untouched."""

import csv
import io
from dataclasses import replace
from collections import Counter
from functools import cache
from pathlib import Path
import struct
import tomllib

from build_paths import DATA, REPO


ROOT = DATA / "mini"
CAMPAIGN = ROOT / "StagesKorean.toml"
CAMPAIGN_IDS = ROOT / "CampaignWordIds.tsv"
SOLO_MENU = set(range(35)) - {10, 13, 14}

import mini_dictionary as md
from elf_probe import Elf
from mini_dictionary_native import verify_definition_layout, verify_final_definition_layout, verify_popup_definition_layout
from mini_font_native import MiniFontMachine
import mini_graphics
import mini_korean_build as old
import mini_lexicon_source as corpus
import mini_text
from mini_stages import Stages, remove_preplaced
from unpack_contents import digest, wwrom_parts

from unpack_disc import require
import wwrom
import runtime_958 as runtime
import mini_db_words as db_words
import demo_958
from stage_lexicon import eligible_words, audit_banks


@cache
def base_inputs():
    print("[1/4] Existing MINI text, font and graphic edits", flush=True)
    outputs, _, base_report = mini_text.plan(
        system_editor=(REPO / "text/MINI_SYSTEM.txt").read_text(encoding="utf-8"),
        dictionary_editor=(REPO / "text/MINI_DICTIONARY.txt").read_text(encoding="utf-8"),
        graphics_edits=mini_graphics.load_edits(REPO / "graphics"),
        save_editor=(REPO / "text/MINI_SAVE.txt").read_text(encoding="utf-8"))
    source_elf, source_rom = wwrom.originals()
    base_elf = outputs.get("MINI/MINI.ELF", source_elf)
    base_rom = outputs.get("MINI/WWROM.DAT", source_rom)
    return base_elf, base_rom, base_report


def compile_goals(stage, targets, words, tokens):
    if targets is None:
        require(all(g[0] not in (9, 10) for g in stage.goals), "Missing Korean goal targets")
        return None, None
    require(len(targets) == len(stage.goals), "Korean goal variant count differs")
    aliases, records = {}, []
    for target, goal in zip(targets, stage.goals):
        require(isinstance(target, str) and bool(target) == (goal[0] in (9, 10)),
                "Unexpected Korean goal target")
        if not target:
            records.append(None)
            continue
        key = corpus.search_key(target)
        if goal[0] == 9:
            require(key in words and 2 <= len(key) <= 9,
                    f"Korean goal must match a playable 2–9-tile dictionary key: {target}")
            payload = bytes(aliases.setdefault(char, len(aliases)) for char in key)
            records.append(goal[:2] + bytes((len(key), goal[3])) + payload.ljust(12, b"\0"))
        else:
            require(len(key) == 1 and any(key in word for word in words),
                    f"Korean goal tile must occur in a playable dictionary key: {target}")
            alias = aliases.setdefault(key, len(aliases))
            records.append(goal[:2] + bytes((alias,)) + goal[3:])
    require(0 < len(aliases) <= 72, "Invalid Korean goal alias count")
    return records, [tokens[c] for c in aliases] + [0xffff] * (72 - len(aliases))


def apply_goal_rule(stage, profile):
    rule = profile.get("goal_rule")
    if rule is None:
        if "minimum_length" not in profile:
            return stage
        require(profile["index"] == 32 and profile["minimum_length"] == 6
                and all(g[:3] == bytes((4, 4, 7)) for g in stage.goals),
                "Only the legacy Jiggy minimum-length adjustment is supported")
        rule = {"kind": 4, "count": 4, "minimum": 6}
    require(profile["index"] != 34, "A.G.W.S. goal rules are frozen")
    require(isinstance(rule, dict) and set(rule) in ({"kind", "count"}, {"kind", "count", "minimum"}),
            "Invalid authored goal rule fields")
    require(all(type(v) is int for v in rule.values()) and rule["kind"] in (0, 1, 2, 3, 4, 8, 9, 10)
            and 0 <= rule["count"] <= 255, "Invalid authored goal rule values")
    require(("minimum" in rule) == (rule["kind"] in (3, 4))
            and ("minimum" not in rule or 2 <= rule["minimum"] <= 9),
            "Only length-count goals accept a 2–9-tile length")
    require((rule["kind"] in (0, 1) and rule["count"] == 0)
            or (rule["kind"] not in (0, 1) and rule["count"] > 0),
            "Fill-all/star goals use count zero; counted goals require a positive count")
    goals = []
    for original in stage.goals:
        goal = bytearray(16)
        goal[0], goal[1] = rule["kind"], rule["count"]
        if rule["kind"] in (3, 4):
            goal[2] = rule["minimum"]
        elif rule["kind"] in (9, 10):
            # compile_goals supplies the Korean target aliases and payload.
            goal[2:] = original[2:]
        goals.append(bytes(goal))
    data = bytearray(stage.data)
    for variant, goal in enumerate(goals):
        data[0x68 + variant * 16:0x78 + variant * 16] = goal
    return replace(stage, data=bytes(data), goals=tuple(goals))


def versus_profiles(elf_data, stages, path):
    elf = Elf(elf_data)
    table = struct.unpack_from("<72I", elf_data, elf.offset(0x2136d8))
    menu = {table[slot * 3 + lane] & 0xffff for slot in range(24) for lane in range(2)}
    weights = Counter(struct.unpack_from("<132I", elf_data, elf.offset(0x2157b8)))
    pools = {"compact": "기차표사나무과고자", "wide": "가나다키리도날스개무표래차기고"}
    profiles = []
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            index = int(row["index"])
            pool = row.get("alphabet") or pools[row["pool"]]
            require(len(pool) == len(set(pool)) and 2 <= len(pool) <= 32,
                    f"Invalid versus letter pool: {index}")
            stage = stages.stages[index]
            require(sorted(stage.inventory) == list(range(72)), f"Unexpected versus stock: {index}")
            totals, letters = dict.fromkeys(pool, 0), [""] * 72
            for token in sorted(range(72), key=lambda t: (-weights[t], (t + index) % 72)):
                char = min(pool, key=totals.__getitem__) if weights[token] else pool[(index + token) % len(pool)]
                letters[token] = char
                totals[char] += weights[token]
            require(min(totals.values()) >= 4 and max(totals.values()) - min(totals.values()) <= 4,
                    f"Unbalanced Korean random tile weights: {index}")
            profile = {"index": index, "mode": "versus", "name": row["name"],
                             "description": row["description"], "hints": ["", ""], "route": [],
                             "preplaced": "".join("?" if r[0] == 0x45 else letters[r[0]]
                                                  for g in stage.board.pieces for r in g),
                             "inventory": "".join(letters[t] for t in stage.inventory)}
            if "lore_words" in row:
                profile["lore_words"] = [w for w in row["lore_words"].split("|") if w]
            profiles.append(profile)
    require(len(menu) == len(profiles) == 48 and {p["index"] for p in profiles} == menu,
            "Korean versus inputs do not match the native menu")
    return profiles


def plan(profile_path=None, *, base=None, word_editor=None, word_ids=None, versus_editor=None):
    pilot = profile_path is None
    profile_path = ROOT / "Stage958.toml" if pilot else Path(profile_path).resolve()
    require(profile_path.is_relative_to(DATA), "Wide stage configuration must stay in the build data directory")
    for path in (word_editor, word_ids, versus_editor):
        require(path is None or Path(path).resolve().is_relative_to(DATA),
                "Staged build inputs must stay in the build data directory")
    with (ROOT / "DBC-HANGUL.tsv").open(encoding="utf-8", newline="") as stream:
        alphabet = [r["hangul"] for r in csv.DictReader(stream, delimiter="\t") if int(r["occurrences"])]
    require(len(alphabet) == 958 and len(set(alphabet)) == 958, "DBC alphabet differs")
    tokens = {c: i for i, c in enumerate(alphabet)}
    base_elf, base_rom, base_report = base_inputs() if base is None else base
    definition_panel = None
    if not pilot:
        panel_at = Elf(base_elf).offset(0x214608)
        before = struct.unpack_from("<4h", base_elf, panel_at)
        require(before == (350, 234, 180, 40), "Unexpected MINI definition panel")
        panel = (340, 234, 200, 40)
        patched = bytearray(base_elf)
        struct.pack_into("<4h", patched, panel_at, *panel)
        base_elf = bytes(patched)
        definition_panel = {"before": before, "after": panel, "center_x": 440,
                            "book_graphic_moved": False, "font_size_changed": False}
    files = {r["name"]: raw for r, raw in wwrom_parts(base_elf, base_rom)}
    original = md.Dictionary({name: files["dic/" + name] for name in md.NAMES})
    source = [r for r in corpus.common_rows() if not r["entry_id"].startswith("DBC:")]
    words_paths = [old.WORDS]
    if not pilot:
        words_paths.append(Path(word_editor).resolve() if word_editor is not None else ROOT / "CampaignWords.tsv")
    fields = ("word", "source_id", "source_sense", "jp_reference", "definition", "pronunciation")
    editor = io.StringIO(newline="")
    writer = csv.DictWriter(editor, fieldnames=(*fields, "display"), delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for path in words_paths:
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            require(tuple(reader.fieldnames or ()) in (fields, (*fields, "display")),
                    f"Korean word-editor columns differ: {path.name}")
            for row in reader:
                writer.writerow({**row, "display": row.get("display", "")})
    rows = old.word_rows(original, source, editor.getvalue())
    db_all = [] if pilot else db_words.read_editor(ROOT / "XenosagaWords.txt")
    japanese_source = md.originals() if db_all else None
    for row in db_all:
        source_id = row["source_word_id"]
        require(0 <= source_id <= len(original.words), "Unknown original DB dictionary source")
        if source_id:
            require(row["original_definition"] == japanese_source.words[source_id - 1].definition.decode("euc_jp"),
                    f"DB {row['db_id']:03}: original dictionary quotation changed")
    db_rows = [r for r in db_all if db_words.native_slot(r)]
    db_keys = {r["word"] for r in db_rows}
    rows = [r for r in rows if r["word"] not in db_keys]
    ledger = old.IDS if pilot else Path(word_ids).resolve() if word_ids is not None else CAMPAIGN_IDS
    active = rows + db_words.records(db_rows)
    all_ids, retired = db_words.retire_and_assign(
        original, active, ledger.read_text(encoding="utf-8"), old)
    ids = {r["word"]: all_ids[r["word"]] for r in active}
    dictionary = old.dictionary(original, active + retired, all_ids)
    layout = verify_definition_layout(base_elf, dictionary, [ids[r["word"]] for r in rows]) if not pilot else None
    db_layout = None if pilot else db_words.verify_layout(base_elf, dictionary, ids, db_rows)
    if layout:
        layout["definitions"] += db_layout["definitions"]
    stages = Stages(files["stage/1p.stg"])
    config = tomllib.loads(profile_path.read_text(encoding="utf-8"))
    require(set(config) == {"version", "stages"} and config["version"] == 1 and config["stages"],
            "Invalid wide stage configuration")
    replacements, profiles, descriptions, banks = {}, [], [], {}
    candidates = {r["key"] for r in source}
    candidates.update(r["word"] for r in db_rows if 2 <= len(r["word"]) <= 9)
    candidates.update(r["word"] for r in rows if r["source_id"].startswith("JPN:") and len(r["word"]) >= 2)
    stage_profiles = config["stages"]
    versus_path = Path(versus_editor).resolve() if versus_editor is not None else ROOT / "VersusKorean.tsv"
    if profile_path == CAMPAIGN:
        require({p["index"] for p in stage_profiles} == SOLO_MENU
                and all(p.get("mode", "solo") == "solo" for p in stage_profiles),
                "Korean solo inputs do not cover the playable campaign and practice boards")
    if not pilot:
        stage_profiles = stage_profiles + versus_profiles(base_elf, stages, versus_path)
    for profile in stage_profiles:
        index = profile["index"]
        require(type(index) is int and 0 <= index < len(stages.stages) and index not in replacements,
                "Duplicate or invalid wide stage index")
        require(not pilot or index == 15, "The egg pilot belongs in the first playable menu entry")
        mode = profile.get("mode", "solo")
        require(mode in ("solo", "versus"), f"Unsupported stage mode: {mode}")
        stage = stages.stages[0 if pilot else index]
        stage = apply_goal_rule(stage, profile)
        require(pilot or len(profile["inventory"]) == len(stage.inventory)
                or index == 32 and len(profile["inventory"]) == len(stage.inventory) + 5,
                "Korean stage must retain its original supply count")
        initial = [row for group in stage.board.pieces for row in group]
        require(len(profile["preplaced"]) == len(initial), "Korean initial piece count differs")
        if "." in profile["preplaced"]:
            require(index == 29 and mode == "solo", "Empty preplaced markers are only supported for X-Buster")
            empty = {(row[1], row[2]) for char, row in zip(profile["preplaced"], initial) if char == "."}
            stage = remove_preplaced(stage, empty)
            profile = {**profile, "preplaced": profile["preplaced"].replace(".", "")}
            initial = [row for group in stage.board.pieces for row in group]
        require(all((char == "?") == (mode == "versus" and row[0] == 0x45)
                    for char, row in zip(profile["preplaced"], initial)),
                "Use ? for every original random preplaced versus piece, and nowhere else")
        random_letters = None
        if mode == "versus":
            require(sorted(stage.inventory) == list(range(72)), "Versus random mapping requires the original 72-ID stock")
            random_letters = [0] * 72
            for source_token, char in zip(stage.inventory, profile["inventory"]):
                require(char in tokens, "Versus stock uses a syllable outside the DBC 958")
                random_letters[source_token] = tokens[char]
        used = set(profile["preplaced"].replace("?", "") + profile["inventory"])
        require(used <= tokens.keys(), "Stage uses a syllable outside the DBC 958")
        require(stage.board.cells, "A Korean profile needs a nonempty playable board")
        projected = {w for w in candidates if set(w) <= used}
        words = (eligible_words(projected, used, db_keys, profile["lore_words"], index,
                                excluded_words=profile.get("word_exclusions", ()))
                 if "lore_words" in profile else sorted(projected))
        banks[index] = words
        missing = sorted(set(words) - ids.keys())
        require(words and not missing,
                f"Stage {index} word editor lacks {len(missing)} projected words: {', '.join(missing[:12])}")
        goals, goal_letters = compile_goals(stage, profile.get("goal_targets"), words, tokens)
        # Original archive readers still see a valid byte-format STF/PBF. Full letters
        # are installed from the runtime banks, with no 72/256-letter stage limit.
        stage_profile = {k: v for k, v in profile.items()
                         if k not in ("goal_targets", "mode", "minimum_length", "goal_rule", "lore_words", "word_exclusions")}
        source_tokens = dict.fromkeys(used, 0)
        if random_letters is not None:
            source_tokens["?"] = 0x45
        replacements[index] = old.stage_bytes(stage, stage_profile, source_tokens, goal_records=goals)
        preplaced = [(row[1], row[2], 0xffff if char == "?" else tokens[char])
                     for char, row in zip(profile["preplaced"], initial)]
        profiles.append({"stage": profile["index"], "preplaced": preplaced,
                         "inventory": [tokens[c] for c in profile["inventory"]],
                         "words": [(tuple(tokens[c] for c in w), ids[w]) for w in words],
                         "goal_letters": goal_letters, "mode": int(mode == "versus"),
                         "random_letters": random_letters})
        descriptions.append({**profile, "source_board": stage.index, "word_count": len(words),
                             "tokens": {c: tokens[c] for c in sorted(used)}})
    source_stages = stages
    stages = old.rebuild_stages(stages, replacements)
    replay, demos = demo_958.plan(base_elf, files["replay/replaypad.bin"], profiles, descriptions)
    require(all(stages.stages[i].data == stage.data for i, stage in enumerate(source_stages.stages)
                if i not in replacements), "Wide build changed an unselected stage")
    for profile in descriptions:
        before, after = source_stages.stages[profile["source_board"]], stages.stages[profile["index"]]
        require((after.board.cells, after.board.objects, after.data[0x5c:0x64])
                == (before.board.cells, before.board.objects, before.data[0x5c:0x64]),
                "Wide build changed board geometry, objects or time")
        expected_stage = apply_goal_rule(before, profile)
        for old_goal, new_goal in zip(expected_stage.goals, after.goals):
            if old_goal[0] == 9:
                unchanged = old_goal[:2] == new_goal[:2] and old_goal[3] == new_goal[3]
            elif old_goal[0] == 10:
                unchanged = old_goal[:2] == new_goal[:2] and old_goal[3:] == new_goal[3:]
            else:
                unchanged = old_goal == new_goal
            require(unchanged, "Wide build changed an original goal rule")
    lexicon_audit = None
    if not pilot and all(p["index"] == 34 or "lore_words" in p for p in stage_profiles):
        lexicon_audit = audit_banks(stage_profiles, banks, candidates, db_keys)
        require(not lexicon_audit["errors"], "; ".join(lexicon_audit["errors"]))
        require(all(len(owners) == 1 for owners in lexicon_audit["long_db_owners"].values()),
                "An exclusive long DB word was assigned to multiple stages")
    edits = {"dic/" + name: raw for name, raw in dictionary.files.items()}
    edits["stage/1p.stg"] = stages.data
    if demos:
        edits["replay/replaypad.bin"] = replay
    print("[2/4] Existing-font descriptors and 958-letter runtime", flush=True)
    font = MiniFontMachine(base_elf)
    palette_base = font.call(0x10ea50, -1, 0, 0) + font.call(0x10eab8, 8, 0x14)
    font_rows = []
    for char in alphabet:
        index = font.index(char.encode("euc_kr"))
        # glyph_layout is a fragment test with synthetic CLUT 1/2. Production
        # descriptors must include the original renderer's real palette prelude.
        font.centered(char.encode("euc_kr"), x=100, y=100, width=16, height=16)
        raw = bytearray(font.uc.mem_read(font.OUTPUT, 64))
        tex0, = struct.unpack_from("<Q", raw, 0x30)
        require(tex0 >> 37 & 0x3fff == palette_base + (index & 1),
                "Full font renderer did not select its installed CLUT bank")
        tex0 = tex0 & ~(0x3fff << 37) | (runtime.TILE_CLUT + (index & 1)) << 37
        struct.pack_into("<Q", raw, 0x30, tex0)
        font_rows.append(bytes(raw[0x28:0x38]))
    elf, rom, archive = wwrom.rebuild(base_elf, base_rom, edits)
    elf, patch = runtime.install(elf, alphabet, profiles, font_rows,
                                 discovery_words=len(dictionary.words))
    patch.update(demo_remaps={}, korean_demos=demos)
    final_layout = verify_final_definition_layout(elf, dictionary)
    popup_layout = verify_popup_definition_layout(elf, dictionary, ids.values())
    from mini_heap_native import HeapMachine
    from mini_validation_cache import checked
    final_heap = checked("resource-heap", (elf, rom), lambda: HeapMachine(elf, rom).verify())
    final = {r["name"]: raw for r, raw in wwrom_parts(elf, rom)}
    require(all(final[name] == edits.get(name, raw) for name, raw in files.items()),
            "Wide build lost an existing editor/font/graphic edit")
    inputs = (*words_paths, profile_path, ledger, ROOT / "DBC-HANGUL.tsv", demo_958.RECORDINGS)
    if not pilot:
        inputs += (versus_path, ROOT / "XenosagaWords.txt")
    report = {"alphabet": alphabet, "alphabet_count": 958, "profiles": descriptions, "patch": patch,
              "definition_panel": definition_panel, "stage_lexicon": lexicon_audit,
              "dictionary_layout": layout,
              "final_definition_layout": final_layout,
              "word_popup_layout": popup_layout,
              "final_resource_heap": final_heap,
              "db_layout": db_layout,
              "menu_wiring": {"pilot": pilot,
                              "source_boards_by_entry": {p["index"]: p["source_board"] for p in descriptions},
                              "unselected_records_unchanged": len(source_stages.stages) - len(replacements)},
              "archive": archive, "base_editors": base_report,
              "dictionary_word_count": len(dictionary.words), "korean_word_ids": ids,
              "recorded_word_ids": all_ids, "retired_word_ids": [all_ids[r["word"]] for r in retired],
              "reviewed_db": db_rows,
              "db_deferred": [r for r in db_all if not db_words.native_slot(r)],
              "source_sha256": {str(p.relative_to(REPO)): digest(p.read_bytes()) for p in inputs}}
    return elf, rom, dictionary, stages, report


def main():
    raise SystemExit(
        "[NOTE] Use the repository's Rebuild.bat for the full Korean campaign. "
        "Do not use old files produced by the standalone pilot."
    )


if __name__ == "__main__":
    main()
