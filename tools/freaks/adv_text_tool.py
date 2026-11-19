"""Lab ADV editor: immutable JPN, optional KOR, validated branch relocation."""

import argparse
import hashlib
import json
from pathlib import Path
import re

from adv_format import parse, rebuild, render_text, require


from build_paths import REPO, WORK

ORIGINALS = {
    "SCEN00.DAT": "1705a3513e49dc3b8539a610a6a55f0f2b3bbdb543629963bccd8cd7730b0cbf",
    "SCEN01.DAT": "4b3dbace418014449d6f0f51ad2ef439124fe21be58a21ada1f789ef7a03d02c",
    "SCEN02.DAT": "2f2497e04ab9d6433fbf5b6115a8e49e764094be3b80c403659695de965f80ac",
    "SCEN03.DAT": "adc714844236fdadf981377bbc2a8738fe09bde72ff975936470ddb35864fab9",
    "SCEN04.DAT": "efad4e8fae8e65cf64694f61c8473642be0c3aba130ea3d96f1016ad1365b874",
    "SCEN05.DAT": "839ad4a5124ad7b0ada063b8a3c580cb094594abb8fc98ebab525f712edbfd54",
    "SCEN06.DAT": "85e7ad203fa3b37ca93477e5035f7e0c5a935cdb7b93b9be98188f586bfd6d8e",
}
BLOCK = re.compile(r"^\[([0-9A-F]{8})\]\n\[JPN\]\n(.*?)\n\[KOR\]\n(.*?)\n\[END\](?:\n|$)", re.M | re.S)


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def text_commands(data):
    commands, _ = parse(data)
    return [r for r in commands if r.text_start is not None]


def export_text(data, name):
    parts = ["# FREAKS-ADV: JPN-KOR v1", f"# SOURCE: {name}",
             f"# SHA256: {checksum(data)}",
             "# Empty KOR keeps original bytes. JPN, IDs and control tokens are protected.", ""]
    for command in text_commands(data):
        original = render_text(data[command.text_start:command.text_end])
        kind = "MESSAGE" if command.opcode == 9 else "CHOICES"
        parts.extend([f"# {command.scene} / {kind}", f"[{command.offset:08X}]",
                      "[JPN]", original, "[KOR]", "", "[END]", ""])
    return "\n".join(parts)


def parse_editor(content, data, name):
    content = content.removeprefix("\ufeff").replace("\r\n", "\n")
    require("\r" not in content, "Stray CR in UTF-8 editor")
    require(content.startswith("# FREAKS-ADV: JPN-KOR v1\n"), "Wrong editor format")
    header = content.split("\n[", 1)[0]
    require(re.findall(r"^# SOURCE: (.+)$", header, re.M) == [name], "Editor/source filename mismatch")
    require(re.findall(r"^# SHA256: (.+)$", header, re.M) == [checksum(data)], "Editor/source hash mismatch")
    expected = text_commands(data)
    matches = list(BLOCK.finditer(content))
    require(len(matches) == len(expected), "Missing, duplicate or malformed editor blocks")
    result = {}
    cursor = 0
    for match, command in zip(matches, expected):
        gap = content[cursor:match.start()]
        require(all(not line or line.startswith("#") for line in gap.splitlines()),
                "Unexpected content outside an editor block")
        require(int(match[1], 16) == command.offset, "Text IDs changed or reordered")
        source_text = render_text(data[command.text_start:command.text_end])
        require(match[2] == source_text, f"JPN changed at {command.offset:08X}")
        result[command.offset] = match[3]
        cursor = match.end()
    require(all(not line or line.startswith("#") for line in content[cursor:].splitlines()),
            "Unparsed editor tail")
    return result


def load_original(name):
    data = (REPO / "unpacked/ADV/DAT00" / name).read_bytes()
    require(checksum(data) == ORIGINALS[name], f"Original changed: {name}")
    return data


def plan_rebuild(editors, font_path=None, extra_characters=(), *, font_size=18, measure=None):
    from adv_format import encode_text
    from adv_spacing import FULL_SPACE, LAYOUT_SPACE, script_layout
    from adv_layout import validate_layout
    from font_tool import missing_characters, prepare_font, validate_display_text
    from gothic import Gothic, fixed_width

    if measure is None:
        measure = fixed_width

    require(set(editors) == set(ORIGINALS), "Editor set must contain all seven original scripts")
    parsed = []
    for name in ORIGINALS:
        data = load_original(name)
        parsed.append((name, data, parse_editor(editors[name], data, name)))
    characters = missing_characters(text for _, _, translations in parsed for text in translations.values())
    characters.update(extra_characters)
    mapping, decoded_font, container, font_report = prepare_font(characters, font_path, size=font_size)
    font = Gothic(decoded_font)
    prepared = []
    report = []
    for name, data, translations in parsed:
        layouts = []
        for command in text_commands(data):
            text = translations[command.offset]
            if text:
                try:
                    layouts.append(validate_layout(encode_text(text, mapping), command.opcode, measure=measure))
                except ValueError as error:
                    raise ValueError(f"{name} [{command.offset:08X}]: {error}") from error
        output, info = rebuild(data, translations, mapping)
        spacing = []
        if measure(b"\x81\x40") < 20:
            output, spacing = script_layout(output)
        changed_positions = {info["positions"][at] for at in info["changed"]}
        for command in text_commands(output):
            if command.offset in changed_positions:
                validate_display_text(output[command.text_start:command.text_end].replace(LAYOUT_SPACE, FULL_SPACE), font)
        if info["changed"] or spacing:
            prepared.append((WORK / "rebuilt/ADV/DAT00" / (name + ".new"), output))
        report.append({"file": name, "texts": len(translations), "changed": len(info["changed"]),
                       "old_size": len(data), "new_size": len(output), "branches": info["branches"],
                       "sha256": checksum(output), "layout_checked": len(layouts),
                       "layout_spacing_commands": [f"{at:08X}" for at in spacing],
                       "named_line_max": max((r["max_width"] for r in layouts if r["named"]), default=0),
                       "wide_line_max": max((r["max_width"] for r in layouts if not r["named"]), default=0)})
    if container is not None:
        prepared.extend((WORK / "rebuilt" / component / "DAT00/GOTHIC.DAT.new", container)
                        for component in ("LNCH", "ADV", "DTB"))
        prepared.append((WORK / "font/GOTHIC.bin", decoded_font))
        prepared.append((WORK / "font/mapping.json",
                         (json.dumps(font_report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")))
    return prepared, report, font_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("export", "verify", "rebuild"))
    parser.add_argument("--font", type=Path, help="Korean TTF/OTF; required when KOR contains Hangul")
    args = parser.parse_args()
    if args.mode != "export":
        from rebuild_pipeline import execute
        execute(args.mode, args.font)
        return
    prepared = []
    report = []
    for name in ORIGINALS:
        data = load_original(name)
        editor = WORK / "text" / (Path(name).stem + ".txt")
        require(editor.resolve().is_relative_to(WORK), "Editor escapes lab")
        exported = export_text(data, name)
        require(not editor.exists() or editor.read_text(encoding="utf-8") == exported,
                f"Existing editor preserved; not overwritten: {editor}")
        prepared.append((editor, exported.encode("utf-8")))
        report.append({"file": name, "texts": len(text_commands(data))})
    for target, _ in prepared:
        require(target.resolve().is_relative_to(WORK), "Output escapes lab")
        require(not target.with_name(target.name + ".partial").exists(), f"Stale partial output: {target}")
    for target, data in prepared:
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        with partial.open("xb") as stream:
            stream.write(data)
        require(checksum(partial.read_bytes()) == checksum(data), "Output verification failed")
        partial.replace(target)
    destination = WORK / "evidence/adv-export.json"
    outputs = [{"path": path.relative_to(WORK).as_posix(), "size": len(data), "sha256": checksum(data)}
               for path, data in prepared]
    destination.write_text(json.dumps({"mode": "export", "files": report, "font": None, "outputs": outputs},
                                      ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in report:
        print(json.dumps(row))
    print("[PASS] ADV editors exported; existing translations and originals preserved")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f"[ERROR] {error}")
