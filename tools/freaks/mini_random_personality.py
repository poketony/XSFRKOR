"""Today's personality: one draw per result, with a matching native radar."""

from mini_korean_runtime import Assembly, immediate as ins, jump, register as rr
from unpack_disc import require


RESULTS = 45
RADAR_CALL = 0x13d0d8
SELECTOR_CALL = 0x13d130
GAME_RANDOM_STATE = 0x1c7400
SEED = 0x6d2b79f5
PAIR_BASE = (0, 7, 13, 18, 22, 25, 27, 28)


def templates():
    result = []
    for identifier in range(1, RESULTS + 1):
        if identifier <= 8:
            row = [10] * 8
            row[identifier - 1] = 100
        elif identifier <= 36:
            row = [15] * 8
            a, b = next((a, b) for a in range(1, 8) for b in range(a + 1, 9)
                        if PAIR_BASE[a] + b == identifier)
            row[a - 1] = row[b - 1] = 50
        elif identifier <= 44:
            row = [20] * 8
            row[identifier - 37] = 32
        else:
            row = [20] * 8
        result.append(tuple(row))
    return tuple(result)


TEMPLATES = templates()


def _shift(rd, rt, amount, operation=0):
    return rt << 16 | rd << 11 | amount << 6 | operation


def _random(state):
    a = Assembly()
    a.address(3, state)
    a.emit(ins(35, 8, 3, 0))
    a.branch(5, 8, 0, "advance")
    a.emit(0)
    a.address(9, GAME_RANDOM_STATE)
    a.emit(ins(35, 8, 9, 0))
    a.address(9, SEED)
    a.emit(rr(38, 8, 8, 9))
    a.branch(5, 8, 0, "advance")
    a.emit(0)
    a.address(8, SEED)
    a.label("advance")
    # Private xorshift32 state: never advance the puzzle's own random stream.
    a.emit(_shift(9, 8, 13), rr(38, 8, 8, 9),
           _shift(9, 8, 17, 2), rr(38, 8, 8, 9),
           _shift(9, 8, 5), rr(38, 8, 8, 9),
           ins(43, 8, 3, 0), rr(8, 0, 31), ins(12, 2, 8, 0xffff))
    return a.finish()


def _radar(table, random_entry):
    a = Assembly()
    saved = (31, 16, 17, 18, 19)
    a.emit(ins(9, 29, 29, -0x70))
    for i, reg in enumerate(saved):
        a.emit(ins(63, reg, 29, i * 8))
    a.emit(rr(33, 16, 5))
    a.label("draw")
    a.emit(jump(random_entry, True), 0, ins(13, 8, 0, 65520), rr(43, 9, 2, 8))
    a.branch(4, 9, 0, "draw")
    a.emit(0, ins(9, 8, 0, RESULTS), rr(27, 0, 2, 8), rr(16, 17, 0))
    a.emit(ins(9, 2, 17, 1), ins(40, 2, 16, 0x10))
    a.address(18, table)
    a.emit(_shift(8, 17, 3), rr(33, 18, 18, 8), ins(9, 19, 0, 2))
    a.emit(ins(9, 8, 0, 44))
    a.branch(5, 17, 8, "init")
    a.emit(0, ins(9, 19, 0, 1))
    a.label("init")
    a.emit(ins(43, 0, 29, 0x30), ins(43, 0, 29, 0x54), ins(43, 0, 29, 0x58),
           rr(33, 17, 0))
    a.label("axis")
    a.emit(jump(random_entry, True), 0,
           _shift(8, 19, 1), ins(9, 8, 8, 1), rr(27, 0, 2, 8), rr(16, 9, 0),
           rr(35, 9, 9, 19), ins(36, 10, 18, 0), rr(33, 10, 10, 9),
           _shift(8, 17, 2), rr(33, 8, 29, 8), ins(43, 10, 8, 0x34),
           ins(9, 18, 18, 1), ins(9, 17, 17, 1), ins(11, 8, 17, 8))
    a.branch(5, 8, 0, "axis")
    a.emit(0, ins(9, 4, 29, 0x30), jump(0x13cd88, True), rr(33, 5, 16))
    a.emit(ins(36, 2, 16, 0x10))
    for i, reg in enumerate(saved):
        a.emit(ins(55, reg, 29, i * 8))
    a.emit(rr(8, 0, 31), ins(9, 29, 29, 0x70))
    return a.finish()


def install(image):
    """Only the result constructor changes; scores/happiness and play stay intact."""
    require(image.words(RADAR_CALL, 2) == (jump(0x13cd88, True), rr(45, 4, 18)),
            "MINI result radar call differs")
    require(image.words(SELECTOR_CALL, 2) == (jump(0x13c900, True), ins(9, 21, 21, 1)),
            "MINI result selector call differs")
    state = image.append(bytes(4))
    table = image.append(bytes(value for row in TEMPLATES for value in row))
    random_entry = image.append(_random(state))
    radar_entry = image.append(_radar(table, random_entry))
    image.patch(RADAR_CALL, [jump(radar_entry, True)])
    image.patch(SELECTOR_CALL, [ins(36, 2, 16, 0x10)])
    return {"enabled": True, "results": RESULTS, "distribution": "uniform result ID; rejection at 65520",
            "random_state": state, "templates": table, "random_entry": random_entry,
            "radar_entry": radar_entry, "radar_call": RADAR_CALL, "selector_call": SELECTOR_CALL,
            "draw_scope": "once per player at result construction; no reroll on display updates",
            "graph": "native radar from matching template, jitter +/-2 (+/-1 for balanced)",
            "game_random_state_written": False, "word_scores_modified": False,
            "happiness_and_compatibility_modified": False}
