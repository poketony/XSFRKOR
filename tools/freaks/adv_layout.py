"""Conservative ADV line budgets with the installed shared-font metrics."""

from adv_format import require, tokens
from gothic import fixed_width


NAMED_LINE_LIMIT = 512
WIDE_LINE_LIMIT = 600


def validate_layout(data, opcode=9, *, measure=fixed_width):
    items = list(tokens(data))
    controls = [raw for control, raw in items if control]
    named = opcode == 9 and b"\x0d" in controls
    limit = NAMED_LINE_LIMIT if named else WIDE_LINE_LIMIT
    widths = [measure(line) for line in data.split(b"\n")]
    require(all(value <= limit for value in widths),
            f"ADV line exceeds {limit}px ({max(widths)}px); adjust the KOR line breaks")
    if named:
        end_name = next(i for i, (control, raw) in enumerate(items) if control and raw == b"\x0d")
        body = b"".join(raw for _, raw in items[end_name + 1:]).lstrip(b"\n")
        require(body.count(b"\n") < 3, "ADV named dialogue exceeds three body lines")
    return {"max_width": max(widths, default=0), "limit": limit, "named": named}
