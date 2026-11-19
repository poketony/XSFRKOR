"""Reviewed Freaks encyclopedia entries, not the old battle-name import."""

import re
import struct

from unpack_disc import require
import mini_lexicon_source as corpus
import mini_text
import xs2_hangul


def read_review(path):
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not re.match(r"^\| \d{3} \|", line):
            continue
        fields = [part.strip().replace("&#124;", "|").replace("<br>", "\n")
                  for part in line.strip("|").split("|")]
        require(len(fields) == 7, "Malformed encyclopedia review row")
        index, japanese, display, reading, original, definition, note = fields
        require(index.isdecimal() and 0 <= int(index) < 201, "Unknown encyclopedia ID")
        key = corpus.search_key(reading)
        require(display and definition and len(key) >= 2
                and all("가" <= c <= "힣" for c in key),
                f"Missing Hangul reading in DB {index}")
        rows.append({"db_id": int(index), "word": key, "display": display,
                     "pronunciation": reading, "definition": definition,
                     "original_title": japanese, "original_definition": original,
                     "note": note})
    require(len(rows) == 199 and {r["db_id"] for r in rows} == set(range(201)) - {88, 164},
            "Reviewed DB must exclude only the one-letter names Jin and water")
    require(len({r["word"] for r in rows}) == len(rows), "Duplicate reviewed DB reading")
    return rows


def records(rows):
    return [{"word": r["word"], "display": r["display"],
             "pronunciation": r["pronunciation"], "definition": r["definition"],
             "jp_reference": str(r["source_word_id"] or 1)}
            for r in rows]


BLOCK = re.compile(r"^\[DB:(\d{3})\]\n\[TITLE\]\n(.*?)\n\[READING\]\n(.*?)\n"
                   r"\[SOURCE\]\n(\d+)\n\[JPN\]\n(.*?)\n\[KOR\]\n(.*?)\n\[END\](?:\n|$)", re.M | re.S)


def read_editor(path):
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    rows, cursor = [], 0
    for match in BLOCK.finditer(text):
        require(all(not line or line.startswith("#") for line in text[cursor:match.start()].splitlines()),
                "Unexpected DB editor content")
        index, display, reading, source, japanese, definition = match.groups()
        require(all("\n" not in field and "\0" not in field for field in (display, reading, definition)),
                "DB editor fields must be single lines")
        key = corpus.search_key(reading)
        require(len(key) >= 2 and all("가" <= c <= "힣" for c in key) and definition,
                f"Invalid DB editor key/definition: {index}")
        rows.append({"db_id": int(index), "word": key, "display": display,
                     "pronunciation": reading, "definition": definition,
                     "source_word_id": int(source), "original_definition": japanese})
        cursor = match.end()
    require(all(not line or line.startswith("#") for line in text[cursor:].splitlines()),
            "Unparsed DB editor tail")
    require(len(rows) == 199 and {r["db_id"] for r in rows} == set(range(201)) - {88, 164}
            and len({r["word"] for r in rows}) == len(rows), "DB editor IDs/keys differ")
    return rows


def native_slot(row):
    mapping = xs2_hangul.mapping([c for c, _ in xs2_hangul.rows()], "euc_jp")
    return (2 <= len(row["word"]) <= 9
            and len(mini_text.encode(row["display"], mapping)) <= 18
            and len(mini_text.encode(row["pronunciation"], mapping)) <= 18)


def verify_layout(elf, dictionary, ids, rows):
    from mini_font_native import MiniFontMachine
    native = MiniFontMachine(elf)
    box = struct.unpack_from("<4h", elf, native.elf.offset(0x214608))
    require(box in ((350, 234, 180, 40), (340, 234, 200, 40)), "Unexpected DB book panel")
    count, half_cell_edges = 0, []
    for row in rows:
        word_id = ids[row["word"]]
        result = native.layout(dictionary.words[word_id - 1].definition, box)
        # The native wrap test can draw a full glyph 10px past its 180px box
        # after a half-width space. The existing book paper extends to x=570;
        # even the centered wider panel's x<=550 glyphs stay on that paper.
        require(result["drawn"] == result["characters"] and all(
            box[0] <= x and x + width <= box[0] + box[2] + 10
            and box[1] <= y and y + height <= box[1] + box[3]
            for x, y, width, height in result["rectangles"]),
            f"DB {row['db_id']:03}: shorten the definition; it exceeds the native book")
        if not result["fits"]:
            half_cell_edges.append(row["db_id"])
        count += 1
    return {"definitions": count, "native_renderer": "pass", "panel": box,
            "ui_changed": box != (350, 234, 180, 40),
            "half_cell_overhang_within_existing_paper": half_cell_edges}


def retire_and_assign(original, active, previous, old):
    previous_rows = old.tsv(previous, ("word", "word_id"))
    active_keys = {r["word"] for r in active}
    retired = [{"word": r["word"], "display": "-", "pronunciation": "",
                "definition": "", "jp_reference": "1"}
               for r in previous_rows if r["word"] not in active_keys]
    assigned = old.word_ids(len(original.words), active + retired, previous)
    return assigned, retired
