"""Freaks ADV script boundaries, inline text, and relative branch relocation."""

from dataclasses import dataclass
import re
import struct


SIZES = (0, 3, 3, 3, 5, 3, 5, 5, 3, 0, 0, 3, 3, 3, 0, 3, 6, 3, 3,
         1, 5, 3, 7, 4, 1, 8, 1, 1, 5, 1, 1, 1, 3, 3, 2, 2, 2, 3, 3)
CONTROL_SIZES = {1: 2, 2: 5, 3: 2, 8: 2, 9: 1, 10: 1, 11: 1, 12: 1,
                 13: 1, 14: 2, 0xfd: 2, 0xfe: 1, 0xff: 1}
SCRIPT_LIMIT = 0x20000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def token_size(data, at):
    require(at < len(data), f"Text outside input at {at:#x}")
    byte = data[at]
    if byte in CONTROL_SIZES:
        size = CONTROL_SIZES[byte]
    else:
        size = 2 if 0x81 <= byte <= 0x9f or 0xe0 <= byte <= 0xfc else 1
    require(at + size <= len(data), f"Truncated text token at {at:#x}")
    return size


def text_end(data, start):
    at = start
    while at < len(data):
        if data[at] == 0:
            return at
        at += token_size(data, at)
    raise ValueError(f"Missing text terminator at {start:#x}")


def tokens(data):
    at = 0
    while at < len(data):
        require(data[at] != 0, "NUL inside editor text")
        size = token_size(data, at)
        raw = data[at:at + size]
        control = raw[0] < 32 or raw[0] in CONTROL_SIZES or raw[0] in (0x7f, 0x80)
        yield control, raw
        at += size


def render_text(data, table=None):
    result = []
    reverse = {code: char for char, code in (table or {}).items()}
    for control, raw in tokens(data):
        if raw == b"\n":
            result.append("\n")
        elif control:
            result.append("{" + ":".join(f"{v:02X}" for v in raw) + "}")
        else:
            text = reverse[raw] if raw in reverse else raw.decode("cp932")
            result.append(text.replace("{", "{{").replace("}", "}}"))
    return "".join(result)


def encode_text(text, table=None):
    result = bytearray()
    cursor = 0
    while cursor < len(text):
        char = text[cursor]
        if char in "{}":
            if text.startswith(char * 2, cursor):
                result.extend(char.encode("ascii"))
                cursor += 2
                continue
            match = re.match(r"\{([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2})*)\}", text[cursor:])
            require(match is not None, f"Malformed control token near {text[cursor:cursor + 24]!r}")
            raw = bytes.fromhex(match[1].replace(":", ""))
            require(raw[0] != 0 and token_size(raw, 0) == len(raw), "Invalid control token length")
            require(raw[0] < 32 or raw[0] in CONTROL_SIZES or raw[0] in (0x7f, 0x80),
                    "Ordinary text must not be entered as a control token")
            result.extend(raw)
            cursor += len(match[0])
            continue
        require(char == "\n" or ord(char) >= 32, "Literal control characters are not allowed")
        if table is not None and char in table:
            result.extend(table[char])
        else:
            try:
                result.extend(char.encode("cp932"))
            except UnicodeEncodeError as error:
                raise ValueError(f"No verified font mapping for {char!r}; do not encode Korean as CP949") from error
        cursor += 1
    tuple(tokens(bytes(result)))
    return bytes(result)


def control_signature(data):
    return tuple(raw for control, raw in tokens(data) if control and raw != b"\n")


def display_signature(data):
    controls, styles = [], []
    previous_style = None
    current_style = 0
    line_start = True
    for control, raw in tokens(data):
        if raw == b"\n":
            # ADV renders separate line buffers, each with the default style.
            current_style = 0
            line_start = True
        elif control:
            if raw[0] == 3:
                if not (line_start and raw == previous_style):
                    controls.append(raw)
                previous_style = raw
                current_style = raw[1]
            else:
                controls.append(raw)
                previous_style = None
                if raw == b"\x0b":
                    current_style = 0
                    line_start = True
        elif raw not in (b" ", b"\x81\x40"):
            line_start = False
            if not styles or styles[-1] != current_style:
                styles.append(current_style)
    return tuple(controls), tuple(styles)


@dataclass(frozen=True)
class Command:
    offset: int
    end: int
    opcode: int
    text_start: int | None = None
    text_end: int | None = None
    scene: str = ""


@dataclass(frozen=True)
class Branch:
    command: int
    field: int
    width: int
    target: int


def parse(data):
    commands = []
    branches = []
    cursor = 0
    scene = ""
    while cursor < len(data):
        opcode = data[cursor]
        require(opcode < len(SIZES), f"Unknown opcode {opcode:#x} at {cursor:#x}")
        require(cursor + max(1, SIZES[opcode]) <= len(data), f"Truncated opcode at {cursor:#x}")
        start = end = None
        if opcode == 0:
            end_label = text_end(data, cursor + 1)
            scene = data[cursor + 1:end_label].decode("ascii")
            destination = end_label + 1
        elif opcode == 9:
            require(cursor + 2 <= len(data), "Truncated message header")
            start = cursor + 2
            end = text_end(data, start)
            destination = end + 1
        elif opcode == 10:
            require(cursor + 2 <= len(data), "Truncated selection header")
            start = cursor + 2 + data[cursor + 1] * 2
            end = text_end(data, start)
            destination = end + 1
        elif opcode == 14:
            require(cursor + 3 <= len(data), "Truncated SFX header")
            destination = cursor + 3 + data[cursor + 2] * 4
        else:
            destination = cursor + SIZES[opcode]
        require(cursor < destination <= len(data), f"Command outside input at {cursor:#x}")
        command = Command(cursor, destination, opcode, start, end, scene)
        commands.append(command)
        fields = []
        if opcode == 4:
            fields.append((cursor + 1, 4))
        elif opcode in (5, 37):
            fields.append((cursor + 1, 2))
        elif opcode in (6, 7):
            fields.append((cursor + 3, 2))
        elif opcode == 10:
            fields.extend((cursor + 2 + i * 2, 2) for i in range(data[cursor + 1]))
        for field, width in fields:
            target = field + int.from_bytes(data[field:field + width], "little", signed=True)
            branches.append(Branch(cursor, field, width, target))
        cursor = destination
    boundaries = {r.offset for r in commands} | {len(data)}
    for branch in branches:
        require(branch.target in boundaries,
                f"Branch target is not a command boundary: {branch.field:#x} -> {branch.target:#x}")
    return commands, branches


def rebuild(data, translations, table=None):
    commands, branches = parse(data)
    known = {r.offset for r in commands if r.text_start is not None}
    require(not set(translations) - known, "Translation references an unknown text command")
    output = bytearray()
    positions = {}
    changed = []
    for command in commands:
        positions[command.offset] = len(output)
        translated = translations.get(command.offset, "")
        original = data[command.offset:command.end]
        if translated and translated != render_text(data[command.text_start:command.text_end]):
            text = encode_text(translated, table)
            require(display_signature(text) == display_signature(data[command.text_start:command.text_end]),
                    f"Display controls changed at {command.offset:#x}")
            output.extend(data[command.offset:command.text_start])
            output.extend(text + b"\0")
            changed.append(command.offset)
        else:
            output.extend(original)
    positions[len(data)] = len(output)
    for branch in branches:
        field = positions[branch.command] + branch.field - branch.command
        target = positions[branch.target]
        difference = target - field
        require(-(1 << (8 * branch.width - 1)) <= difference < 1 << (8 * branch.width - 1),
                f"Relative branch overflow at {branch.field:#x}; no output written")
        output[field:field + branch.width] = difference.to_bytes(branch.width, "little", signed=True)
    parsed, moved = parse(output)
    require(len(parsed) == len(commands) and [x.opcode for x in parsed] == [x.opcode for x in commands],
            "Rebuilt command sequence differs")
    require([(b.command, b.field, b.width, b.target) for b in moved]
            == [(positions[b.command], positions[b.command] + b.field - b.command,
                 b.width, positions[b.target]) for b in branches], "Rebuilt branch graph differs")
    require(len(output) <= SCRIPT_LIMIT,
            f"ADV script buffer exceeded: {len(output)} > {SCRIPT_LIMIT}; no output written")
    return bytes(output), {"changed": changed, "positions": positions, "branches": len(branches)}
