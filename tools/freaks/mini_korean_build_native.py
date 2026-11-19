"""Original-code checks for the stage-local Korean MINI runtime build."""

from collections import Counter
import struct
from types import SimpleNamespace

from elf_probe import Elf
from mini_board_native import BoardMachine, simple_queries
from mini_candidate_native import CandidateMachine
import mini_dictionary as md
from mini_dictionary_native import verify_definition_layout
from mini_heap_native import HeapMachine
from mini_lexicon import Lexicon
import mini_korean_runtime as runtime
import mini_korean_tiles as tiles
from mini_sprite_native import SpriteMachine
import mini_sprites
from mini_stages import Stages
from unpack_contents import wwrom_parts

from build_paths import WORK
from unpack_disc import require


def readback_preview(elf, rom, profile, record):
    files = {r["name"].removeprefix("spr/"): raw for r, raw in wwrom_parts(elf, rom)
             if r["name"].startswith("spr/")}
    before = mini_sprites.Sprites(files)
    pixels = bytearray(files["textureimg.bin"])
    image = Elf(elf)
    for old_offset, address, _ in record["texture_reads"]:
        texture = next(item for item in before.textures if item.offset == old_offset)
        at = image.offset(address)
        pixels[old_offset:old_offset + len(texture.pixels)] = elf[at:at + len(texture.pixels)]
    after = mini_sprites.Sprites(dict(files, **{"textureimg.bin": bytes(pixels)}))
    handles = tiles.native_handles(elf, len(profile["alphabet"]))
    return tiles.comparison(before, after, handles,
                            title="Puzzle letter sprites: original / Korean | Actual ELF runtime-bank readback")


def view(dictionary, dix):
    files = dict(dictionary.files, **{"dicdata.dix": dix})
    header = bytearray(files["dicdata.dhd"])
    struct.pack_into("<I", header, 12, len(dix))
    files["dicdata.dhd"] = bytes(header)
    return md.Dictionary(files)


def snapshot(stages, profile, prefix):
    stage = stages.stages[profile["index"]]
    require(len(stage.board.pieces) == 1, "Route snapshots require one original piece group")
    pbf = bytearray(stage.board.data)
    groups_at, objects_at = struct.unpack_from("<2I", pbf, 68)
    group_at = 64 + groups_at + struct.unpack_from("<I", pbf, 64 + groups_at)[0]
    moves = profile["route"][:prefix]
    extra = b"".join(bytes((profile["tokens"][char], move["x"], move["y"], 0, 0, 0, 0, 0))
                     for char, move in zip(profile["inventory"], moves))
    cut = 64 + objects_at
    pbf[cut:cut] = extra
    struct.pack_into("<I", pbf, 8, len(pbf))
    struct.pack_into("<I", pbf, 72, objects_at + len(extra))
    struct.pack_into("<I", pbf, group_at, len(stage.board.pieces[0]) + prefix)
    raw = bytearray(stage.data[:304] + pbf + stage.data[stage.inventory_offset:])
    struct.pack_into("<I", raw, 8, len(raw))
    struct.pack_into("<2I", raw, 20, len(pbf), 304 + len(pbf))
    end = stages.stages[stage.index + 1].offset if stage.index + 1 < len(stages.stages) else len(stages.data)
    require(len(raw) <= end - stage.offset, "Route snapshot exceeds its original stage slot")
    data = bytearray(stages.data)
    data[stage.offset:end] = raw.ljust(end - stage.offset, b"\0")
    return Stages(bytes(data))


def verify(elf, rom, dictionary, stages, report):
    patch, profiles = report["patch"], report["profiles"]
    profile_records = {r["stage"]: r for r in patch["profiles"]}
    layout = verify_definition_layout(elf, dictionary, report["word_ids"].values())
    board = BoardMachine(elf, rom)
    heap = board.initialize_search()
    require(board.arena_start >= patch["heap_start"], "Native heap overlaps the ELF extension")
    japanese_index, japanese_entries = board.index, dict(board.entries)
    stage_row = next(row for row, _ in wwrom_parts(elf, rom) if row["name"] == "stage/1p.stg")
    descriptor = board.u32(0x224578 + stage_row["index"] * 36)
    require(descriptor, "Native stage resource is not decoded")
    stage_address = board.u32(descriptor)
    free = board.u32(board.root + 0x20)
    queries, reads = [], 0
    for profile in profiles:
        record = profile_records[profile["index"]]
        dix = board.read(record["dix_address"], record["dix_bytes"])
        lexicon = Lexicon(view(dictionary, dix))
        board.index = record["dix_address"]
        board.entries = {e.symbols: e.offset for e in lexicon.entries}
        require(len(profile["route"]) <= len(profile["inventory"]), "Route uses more tiles than its supply")
        for prefix, move in enumerate(profile["route"]):
            current = snapshot(stages, profile, prefix)
            board.uc.mem_write(stage_address, current.data)
            board.stages = current
            stage = current.stages[profile["index"]]
            require(all(row[3] == 1 for row in stage.board.cells), "Pilot route expects unit cells")
            model = SimpleNamespace(cells=tuple(bytes((1, *r[1:])) for r in stage.board.cells),
                                    pieces=stage.board.pieces)
            char = profile["inventory"][prefix]
            token = profile["tokens"][char]
            expected = simple_queries(model, token, move["x"], move["y"])
            matched = sorted("".join(profile["alphabet"][t] for t in q) for q in expected if q in board.entries)
            require(matched, "Korean route has a rejected placement")
            for variant in range(3):
                root = board.load(profile["index"], variant)
                require(board.u32(patch["state_address"]) == record["dix_address"], "Stage loader selected another dictionary")
                actual = board.queries(root, token, move["x"], move["y"], judge=True)
                require(Counter(actual) == Counter(expected), "Patched native Korean route differs")
                board.call(0x11cde0, root)
            queries.append({"stage": profile["index"], "move": prefix + 1, "tile": char, "words": matched})
        board.uc.mem_write(stage_address, stages.data)
        board.stages = stages
        root = board.load(profile["index"])
        scratch = board.STACK - 0x3000
        for entry in lexicon.entries:
            board.uc.mem_write(scratch, entry.symbols)
            require(board.call(0x114c18, scratch, len(entry.symbols)) == record["dix_address"] + entry.offset,
                    "Runtime Korean direct lookup selected another record")
            reads += 1
        board.call(0x11cde0, root)
    untranslated = next(i for i in range(len(stages.stages)) if i not in profile_records)
    board.index, board.entries = japanese_index, japanese_entries
    root = board.load(untranslated)
    require(board.read(patch["state_address"], 8) == b"\0" * 8, "Untranslated stage did not restore the original language")
    key, relative = next(iter(japanese_entries.items()))
    scratch = board.STACK - 0x3000
    board.uc.mem_write(scratch, key)
    require(board.call(0x114c18, scratch, len(key)) == japanese_index + relative,
            "Original Japanese lookup changed after the Korean stage")
    board.call(0x11cde0, root)
    require(board.u32(board.root + 0x20) == free and board.read(board.arena_start + board.arena_size, 16) == b"\xa5" * 16,
            "Runtime board check leaked memory or crossed arena end")
    candidates = CandidateMachine(elf, rom, dictionary)
    candidates.prepare()
    original_candidate_index = candidates.index
    for profile in profiles:
        record = profile_records[profile["index"]]
        dix = candidates.read(record["dix_address"], record["dix_bytes"])
        candidates.index = record["dix_address"]
        for entry in Lexicon(view(dictionary, dix)).entries:
            word_id, pointer = candidates.choose(entry)
            require(candidates.read_word(word_id, pointer) == dictionary.words[word_id - 1].raw,
                    "Runtime Korean candidate read differs")
    candidates.index = original_candidate_index
    require(candidates.call(0x114980) == 0, "Runtime dictionary did not release")
    graphics = SpriteMachine(elf, rom)
    sprites = graphics.sprites
    HeapMachine.verify(graphics)
    graphics.call(0x10a7c0, count=200_000_000)
    captures = []
    for profile in profiles:
        handles = tiles.native_handles(elf, len(profile["alphabet"]))
        korean, _ = tiles.build_tiles(sprites, profile["alphabet"], handles)
        for index, expected in ((untranslated, sprites), (profile["index"], korean), (untranslated, sprites)):
            graphics.sprites = expected
            before = len(graphics.image_uploads)
            graphics.call(runtime.STAGE_GRAPHICS, index, 0, count=200_000_000)
            captured = graphics.image_uploads[before:]
            require(captured, "Native stage-graphics entry uploaded nothing")
            captures.append({"stage": index, "textures": len(captured), "korean": index == profile["index"]})
        readback_preview(elf, rom, profile, profile_records[profile["index"]]).save(
            WORK / "evidence/mini-korean-build-tiles.png")
    require(graphics.upload is None and not graphics.pending
            and bytes(graphics.uc.mem_read(graphics.arena_start + graphics.arena_size, 16)) == b"\xa5" * 16,
            "Runtime graphics upload remained pending or crossed arena end")
    return {"definition_layout": layout, "patched_stage_route_checks": len(queries) * 3,
            "steps": queries, "korean_dix_record_lookups": reads,
            "original_japanese_lookup_after_korean": "pass", "korean_wix_wdt_candidate_reads": "pass",
            "original_korean_original_texture_uploads": captures,
            "heap_extension_no_overlap_and_board_allocations_released": True,
            "resource_heap": heap,
            "scope": "Actual injected selectors, original stage loads/query/DIX/WIX/WDT and sprite-group upload preparation. "
                     "Route states are input snapshots, GS transfers captured. Full gameplay clear/score/diagnosis and GPU not run."}
