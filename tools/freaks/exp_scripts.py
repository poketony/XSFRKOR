"""Build EXP text banks and both fonts with one character mapping."""

import exp_batdat as batdat
from exp_batdat_native import BattleMachine
import exp_field as field
from exp_field_native import FieldMachine
import exp_movie as movie
from exp_movie_native import MovieMachine
import exp_executable as executable
from exp_executable_native import ExecutableMachine
import exp_font
import exp_sb as sb
import exp_txd as txd
from unpack_disc import require


CF_LIMIT = 0x20000
MAP_SCRIPT_LIMIT = 0x12000


def runtime_limit(name, source_size, aliases):
    if name.startswith("cf/"):
        return CF_LIMIT
    if aliases:
        return MAP_SCRIPT_LIMIT
    # Other think scripts include debug/test resources with a separate loader.
    return source_size


def editor_name(name):
    return "EXP/" + name.rsplit("/", 1)[-1].rsplit(".", 1)[0] + ".txt"


def editors():
    sources, _ = sb.originals()
    return {name: sb.export_editor(data, name) for name, data in sources.items() if sb.Script(data).texts()}


def plan_rebuild(editors, font_path=None, *, txd_editors=None, field_editors=None, batdat_editor=None, movie_editors=None,
                 executable_editor=None):
    sources, aliases = sb.originals()
    records = {name: sb.Script(data).strings for name, data in sources.items()}
    expected = {name for name, rows in records.items() if any(any(b >= 128 for b in raw) for raw in rows.values())}
    require(set(editors) == expected, "EXP SB editor set differs from its original sources")
    translations = {name: sb.parse_editor(editors[name], sources[name], name) for name in expected}
    txd_sources = {} if txd_editors is None else txd.originals()
    require(txd_editors is None or set(txd_editors) == set(txd_sources), "EXP TXD editor set differs")
    menus = {name: txd.parse_editor(txd_editors[name], data, name) for name, data in txd_sources.items()}
    field_sources = {} if field_editors is None else field.originals()
    require(field_editors is None or set(field_editors) == set(field_sources), "EXP field TXT editor set differs")
    residents = {name: field.parse_editor(field_editors[name], data, name) for name, data in field_sources.items()}
    battle_source = None if batdat_editor is None else batdat.original()
    battle = {} if battle_source is None else batdat.parse_editor(batdat_editor, battle_source)
    movie_sources = {} if movie_editors is None else {name: data for name, data in movie.originals().items() if movie.editable(data)}
    require(movie_editors is None or set(movie_editors) == set(movie_sources), "EXP movie editor set differs")
    movies = {name: movie.parse_editor(movie_editors[name], data, name) for name, data in movie_sources.items()}
    executable_sources = None if executable_editor is None else executable.originals()
    embedded = {} if executable_sources is None else executable.parse_editor(executable_editor, executable_sources)
    text_banks = [*translations.values(), *menus.values(), *residents.values(), battle, *movies.values(), embedded]
    characters = {char for entries in text_banks for text in entries.values() for char in text
                  if "\uac00" <= char <= "\ud7a3"}
    exclude = set(txd_sources) | set(field_sources) | set(movie_sources) | ({batdat.NAME} if battle_source is not None else set())
    retained = exp_font.non_sb_text_sources(exclude=exclude)
    omitted_hangul = {char: b"" for char in characters}
    # Retained movie syntax still needs nonempty ruby bases before a real code is assigned.
    omitted_movie_hangul = {char: b"?" for char in characters}
    for name, strings in records.items():
        replacements = translations.get(name, {})
        for at, raw in strings.items():
            try:
                retained.append(sb.encode(replacements[at], omitted_hangul) if at in replacements else raw)
            except (UnicodeError, ValueError) as error:
                raise ValueError(f"{name}:{at:08X}: {error}") from error
    for name, data in txd_sources.items():
        for index, raw in enumerate(txd.Table(data, name).strings):
            retained.append(txd.encode(menus[name][index], name, index, omitted_hangul)[0]
                            if index in menus[name] else raw)
    for name, data in field_sources.items():
        for item in field.source_fields(data, name):
            retained.append(field.runtime_bytes(residents[name][item.key], omitted_hangul)
                            if item.key in residents[name] else field.runtime_text(item.value.encode("cp932")))
    if battle_source is not None:
        table = batdat.BattleData(battle_source)
        retained.append(battle_source[:84])
        # Keep numeric/non-text bytes conservatively reserved; release only replaced text.
        for index, (start, end) in enumerate(table.bounds):
            retained.append(battle_source[start:table.pools[index][0] if index in table.pools else end])
        retained.extend(sb.encode(battle[item.pointer_at], omitted_hangul) if item.pointer_at in battle else item.raw
                        for item in table.entries)
    for name, data in movie_sources.items():
        for cue in movie.cues(data):
            retained.append(movie.encode(movies[name][cue.index], omitted_movie_hangul)
                            if cue.index in movies[name] else cue.raw)
    if embedded:
        embedded_rows = {row.address: row for row in executable.entries(executable_sources)}
        retained.extend(executable.encode(text, embedded_rows[address], omitted_hangul)[0]
                        for address, text in embedded.items())
    protected = exp_font.protected_indices(retained)
    mapping, fonts, font_report = exp_font.prepare_font(characters, protected, font_path)
    outputs, changed = {}, []
    for name, entries in translations.items():
        if not entries:
            continue
        replacements = {at: sb.encode(text, mapping) for at, text in entries.items()}
        output, changes = sb.rebuild(sources[name], replacements)
        limit = runtime_limit(name, len(sources[name]), aliases[name])
        require(len(output) <= limit,
                f"EXP script buffer exceeded: {name} ({len(output)} > {limit}); no output written")
        if not changes:
            continue
        for target in [name, *aliases[name]]:
            outputs["EXP/FILES/" + target] = output
        changed.append({"file": name, "aliases": aliases[name], "changes": changes,
                        "source_size": len(sources[name]), "size": len(output), "limit": limit})
    if fonts:
        outputs.update({f"EXP/FILES/font{index}.tex": data for index, data in enumerate(fonts)})
    menu_report, slot_patch = [], False
    for name, entries in menus.items():
        output, report = txd.rebuild(txd_sources[name], name, entries, mapping)
        if report["changed"]:
            outputs["EXP/FILES/" + name] = output
            menu_report.append(dict(report, file=name))
            slot_patch |= report["slot_metadata"]
    field_report, field_outputs = [], dict(field_sources)
    for name, entries in residents.items():
        output, report = field.rebuild(field_sources[name], name, entries, mapping)
        field_outputs[name] = output
        if report["changed"]:
            outputs["EXP/FILES/" + name] = output
            field_report.append(report)
    for report in field_report:
        report["native"] = FieldMachine(field_outputs).verify(report["file"])
    battle_report = None
    if battle_source is not None:
        output, battle_report = batdat.rebuild(battle_source, battle, mapping)
        if battle_report["changed"]:
            outputs["EXP/FILES/" + batdat.NAME] = output
            battle_report["native"] = BattleMachine(output).verify()
    executable_report = None
    if executable_sources is not None:
        elf_output, boot_output, executable_report = executable.rebuild(executable_sources, embedded, mapping)
        if executable_report["changed"]:
            outputs["EXP/EXP.ELF"] = elf_output
            if executable_report["paired_ov02"]:
                outputs["EXP/OV02.OVL"] = boot_output
            counts = [row for row in executable_report["changed"] if row["address"] in (0x683000, 0x696998)]
            if counts:
                current_battle = outputs.get("EXP/FILES/" + batdat.NAME)
                current_battle = batdat.original() if current_battle is None else current_battle
                maximum_name = max(len(item.raw) for item in batdat.BattleData(current_battle).entries if item.field == "name")
                require(all(maximum_name + row["bytes"] < 256 for row in counts),
                        "EXP item name plus translated count fragment exceeds the 256-byte message buffer")
                executable_report["maximum_battle_name_bytes"] = maximum_name
    if slot_patch:
        outputs["EXP/EXP.ELF"] = txd.patch_slots(outputs.get("EXP/EXP.ELF", txd.original_elf()))
    movie_report = []
    prepared_movies = {name: movie.rebuild(movie_sources[name], entries, mapping) for name, entries in movies.items()}
    if any(report["changed"] for _, report in prepared_movies.values()):
        for name, source in movie.originals().items():
            output, report = prepared_movies[name] if name in prepared_movies else movie.rebuild(source, {})
            report["paired_original"] = not bool(report["changed"])
            if report["paired_original"]:
                # The same patched loader handles all three files; terminate the unchanged ones too.
                output = output.rstrip(b"\0") + bytes(16)
                report["size"] = len(output)
            report["file"] = name
            report["native"] = []
            for font in fonts or exp_font.original_fonts():
                machine = MovieMachine(font)
                native = machine.verify(output, translated=True)
                native["loading"] = machine.verify_loading(name, output)
                report["native"].append(native)
            outputs["EXP/FILES/" + name] = output
            movie_report.append(report)
    if movie_report:
        outputs["EXP/EXP.ELF"] = movie.patch_loader(outputs.get("EXP/EXP.ELF", txd.original_elf()))
    if executable_sources is not None and "EXP/EXP.ELF" in outputs:
        outputs["EXP/OV02.OVL"] = boot_output
        executable_report["boot_text_changed"] = executable_report["paired_ov02"]
        executable_report["paired_ov02"] = True
    if executable_report and executable_report["changed"]:
        executable_report["native"] = []
        for font in fonts or exp_font.original_fonts():
            machine = ExecutableMachine(outputs["EXP/EXP.ELF"], font)
            widths, numeric = [], 0
            for row in executable_report["changed"]:
                raw, _ = executable.encode(embedded[row["address"]], embedded_rows[row["address"]], mapping)
                widths.append({"address": row["address"], "width": machine.embedded_width(raw)})
                if row["address"] in (0x683000, 0x696998):
                    for count in (0, 99):
                        expected = raw[:row["number_at"]] + f"{count:02d}".encode("ascii") + raw[row["number_at"] + 2:]
                        require(machine.numeric_block(short=row["address"] == 0x696998, count=count) == b"Aqua S" + expected,
                                "EXP native numeric message differs from the common font build")
                        numeric += 1
                elif row["address"] == 0x3e6f88:
                    for index in (0, 35):
                        expected = raw[:row["number_at"]] + f"{index + 1:02d}".encode("ascii") + raw[row["number_at"] + 2:]
                        require(machine.campaign(index, row["target"]) == expected,
                                "EXP native campaign differs from the common font build")
                        numeric += 1
            executable_report["native"].append({"widths": widths, "numeric_cases": numeric,
                                                  "scope": "Real width/help/digit code and original scalar libc paths; MMI fast paths omitted; inventory notification modeled; no GPU"})
    return outputs, {"scripts": changed, "font": font_report, "editors": len(editors),
                     "txd": menu_report, "txd_editors": len(txd_sources), "slot_writer_patched": slot_patch,
                     "fields": field_report, "field_editors": len(field_sources), "batdat": battle_report,
                     "movies": movie_report, "movie_editors": len(movie_sources), "movie_loader_patched": bool(movie_report),
                     "executable": executable_report, "executable_editor": executable_sources is not None,
                     "source_SB_and_BIN_hashes_checked": True,
                     "scope": "In-memory plan; source files and external ROM workspace are never written"}
