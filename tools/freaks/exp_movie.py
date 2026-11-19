"""EXP movie subtitle source editors; timing, clears and setup remain original."""

from dataclasses import dataclass
import json
import re
import struct

from elf_probe import Elf
import exp_sb as sb

from build_paths import DATA, REPO
from unpack_disc import require


NAMES = ("movie/s010310.txt", "movie/s010610.txt", "movie/t010700.txt")
RESERVE = 0x4000
MAX_LINES = 2
ALLOCATION_ARGUMENT = 0x1e72f8
TIME = re.compile(rb"\+?(?:[0-9]+:[0-9]{1,2}:[0-9]{1,2}|[0-9]+)")
LINE = re.compile(rb"([^ \t\r\n]+)([ \t]*)([^\r\n]*)")
RUBY = re.compile(rb"\[([^\[\]/\\$\r\n]+)/([^\[\]/\\$\r\n]+)\]")


@dataclass(frozen=True)
class Cue:
    index: int
    stamp: str
    frame: int
    start: int
    end: int
    raw: bytes


def originals():
    report = json.loads((DATA / "contents-report.json").read_text(encoding="utf-8"))
    sources = {}
    for row in report["members"]:
        if row["source"] != "EXP/XENOSAGA.00~02" or row["path"] not in NAMES:
            continue
        data = (REPO / row["output"]["path"]).read_bytes()
        require(sb.checksum(data) == row["output"]["sha256"], "Original EXP movie TXT differs")
        sources[row["path"]] = data
    require(set(sources) == set(NAMES), "EXP movie TXT inventory differs")
    return sources


def cues(data):
    content = data.rstrip(b"\0")
    require(b"\0" not in content, "NUL inside EXP movie source")
    require(content.decode(sb.ENCODING).encode(sb.ENCODING) == content, "EXP movie encoding is not reversible")
    result, at, previous, ended = [], 0, 0, False
    for raw_line in content.splitlines(keepends=True):
        line = raw_line.rstrip(b"\r\n")
        if line == b"end":
            require(not ended, "Repeated EXP movie end")
            ended = True
        elif line and not line.startswith(b";"):
            require(not ended, "EXP movie data after end")
            match = LINE.fullmatch(line)
            require(match is not None and TIME.fullmatch(match[1]) is not None,
                    "Malformed EXP movie time line")
            stamp = match[1].decode("ascii")
            relative, digits = stamp.startswith("+"), stamp.lstrip("+")
            units = [int(value) for value in digits.split(":")]
            if len(units) == 3:
                require(units[1] < 60 and units[2] < 30, "EXP movie time is not 30-fps MM:SS:FF")
                frame = (units[0] * 60 + units[1]) * 30 + units[2]
            else:
                frame = units[0]
            frame += previous if relative else 0
            require(previous <= frame < 0xffff, "EXP movie time is out of order or exceeds u16")
            previous = frame
            body = match[3]
            require(not body or match[2], "EXP movie body has no separator")
            start = at + match.start(3)
            result.append(Cue(len(result), stamp, frame, start, start + len(body), body))
        at += len(raw_line)
    require(ended and result and result[0].frame == 0, "EXP movie setup/end is missing")
    require(all(cue.frame > 0 for cue in result[1:]), "Repeated EXP movie setup")
    return tuple(result)


def pages(raw):
    """Expand only the actual source grammar, not arbitrary renderer commands."""
    result = []
    for line in raw.split(b"\\n"):
        require(not any(value < 32 for value in line) and b"\\" not in line and b"$" not in line,
                "EXP movie captions allow literal \\n, not raw controls or font commands")
        output, at = bytearray(), 0
        while at < len(line):
            if line[at] == 0x5b:
                match = RUBY.match(line, at)
                require(match is not None, "Use [base/ruby] with nonempty, unnested fields")
                output.extend(b"$rubyb;" + match[1] + b"$rubys;" + match[2] + b"$rubye;")
                at = match.end()
            else:
                require(line[at] != 0x5d, "Unmatched EXP movie ruby bracket")
                output.append(line[at])
                at += 1
        result.append(bytes(output))
    return tuple(result)


def encode(text, mapping=None):
    require(not any(char in text for char in "{}\r\n\t"), "Use literal \\n in an EXP movie translation")
    require(not text.startswith((" ", "\t")), "Leading ASCII movie whitespace would be discarded")
    for match in re.finditer(RUBY.pattern.decode("ascii"), text):
        require(not any("\uac00" <= char <= "\ud7a3" for char in match[2]),
                "EXP's 8-pixel ruby bank has no Hangul; use Hangul in the base caption instead")
        for char in match[2]:
            unit = sb.encode(char)
            supported = len(unit) == 1 and char.isascii() and char.isalnum()
            if len(unit) == 2:
                lead, tail = unit
                supported |= (lead == 0xa3 and (0xb0 <= tail <= 0xb9 or 0xc1 <= tail <= 0xda or 0xe1 <= tail <= 0xfa)
                              or lead == 0xa4 and 0xa1 <= tail <= 0xf3 or lead == 0xa5 and 0xa1 <= tail <= 0xf6
                              or lead == 0xa1 and tail in (0xa2, 0xa3, 0xa6, 0xbc, 0xdd))
            require(supported, "EXP ruby supports alphanumerics, standard kana and limited punctuation, not kanji")
    raw = sb.encode(text, mapping)
    require(len(pages(raw)) <= MAX_LINES, "EXP movie captions fit at most two lines at original Y=390")
    return raw


def editable(data):
    return tuple(cue for cue in cues(data) if cue.frame and cue.raw)


def editors():
    return {name: export_editor(data, name) for name, data in originals().items() if editable(data)}


def export_editor(data, name):
    rows = ["# FREAKS-EXP-MOVIE: JPN-KOR v1", f"# SOURCE: {name}", f"# SHA256: {sb.checksum(data)}",
            "# Empty KOR preserves original. Timing, clears, setup and comments are protected.",
            "# Write literal \\n for a new line; ruby is [base/ruby].", ""]
    for cue in editable(data):
        rows.extend((f"# TIME: {cue.stamp} ({cue.frame} frames)", f"[{cue.index:08X}]", "[JPN]",
                     cue.raw.decode(sb.ENCODING), "[KOR]", "", "[END]", ""))
    return "\n".join(rows)


def parse_editor(content, data, name):
    normalized = content.replace("\r\n", "\n")
    template = export_editor(data, name)
    original_blocks = list(sb.BLOCK.finditer(template))
    blocks = list(sb.BLOCK.finditer(normalized))
    require(len(blocks) == len(original_blocks), "EXP movie editor block count differs")
    # Reconstruct every byte outside KOR spans; even timing annotations are protected.
    previous, parts, replacements = 0, [], {}
    for block, original in zip(blocks, original_blocks):
        require(block[1] == original[1] and block[2] == original[2], "EXP movie ID/JPN differs")
        parts.extend((normalized[previous:block.start(3)], ""))
        previous = block.end(3)
        text = block[3]
        if text and text != block[2]:
            require("\n" not in text and "\r" not in text, "Use literal \\n, not physical movie lines")
            replacements[int(block[1], 16)] = text
    parts.append(normalized[previous:])
    require("".join(parts) == template, "EXP movie source, timing or protected layout differs")
    return replacements


def rebuild(data, replacements, mapping=None):
    rows = {cue.index: cue for cue in editable(data)}
    require(set(replacements) <= set(rows), "EXP movie translation targets a setup, clear or unknown cue")
    changes, output = [], data
    for index in sorted(replacements, reverse=True):
        cue, text = rows[index], replacements[index]
        raw = encode(text, mapping)
        if raw == cue.raw:
            continue
        output = output[:cue.start] + raw + output[cue.end:]
        changes.append({"index": index, "frame": cue.frame, "old_bytes": len(cue.raw), "bytes": len(raw)})
    if changes:
        rows = cues(output)
        compiled_bound = 32
        for cue in rows:
            lines = (cue.raw,) if not cue.frame else pages(cue.raw)
            compiled_bound += (2 if cue.frame else 0) + sum(len(line) + 3 for line in lines)
        # Keep every in-place write behind all unread captions, even with many ruby expansions.
        headroom = b";" + b" " * compiled_bound + b"\n"
        # The original end scanner also walks stale input; terminate it deterministically.
        output = headroom + output.rstrip(b"\0") + bytes(16)
        require(len(output) <= RESERVE, "EXP movie source exceeds its 16-KiB reserved prefix")
        before, after = cues(data), cues(output)
        require([(cue.frame, cue.stamp) for cue in before] == [(cue.frame, cue.stamp) for cue in after],
                "EXP movie timing changed")
    return output, {"changed": sorted(changes, key=lambda item: item["index"]),
                    "source_size": len(data), "size": len(output), "timing_unchanged": True,
                    "compiler_comment_headroom": len(headroom) if changes else 0}


def patch_loader(data):
    elf = Elf(data)
    at = elf.offset(ALLOCATION_ARGUMENT)
    require(data[at - 4:at + 4] == struct.pack("<II", 0x0c000000 | 0x1e0e28 // 4, 0x0040202d),
            "EXP movie allocator call/argument differs")
    output = bytearray(data)
    struct.pack_into("<I", output, at, 0x24440000 | RESERVE)
    return bytes(output)
