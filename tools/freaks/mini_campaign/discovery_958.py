"""Expand MINI's separate play/history bitmaps without moving legacy save fields."""

from mini_korean_runtime import Assembly, immediate as ins, jump, register as rr
from unpack_disc import require


LEGACY_BYTES = 0x30d5
PREFIX = 0x247030
SAVE = 0x1c7458
SAVE_BYTES = 0x3400
SCORE_PAD = SAVE + 0x32e8
TAIL_BYTES = 128
CAPACITY_BYTES = LEGACY_BYTES + TAIL_BYTES
MAGIC = (0x4458534b, 0x31545349, 0x00010080)
SIZE_SITES = (0x161508, 0x1688a4, 0x17d43c, 0x17e190)


def move(rd, rs):
    return rr(33, rd, rs)


def save(a, regs, size):
    a.emit(ins(9, 29, 29, -size))
    for i, r in enumerate(regs):
        a.emit(ins(63, r, 29, i * 8))


def restore(a, regs, size):
    for i, r in enumerate(regs):
        a.emit(ins(55, r, 29, i * 8))
    a.emit(rr(8, 0, 31), ins(9, 29, 29, size))


def copy(a, dst, src, count, label):
    a.address(24, dst)
    a.address(25, src)
    a.emit(ins(9, 8, 0, count))
    a.label(label)
    a.emit(ins(36, 9, 25, 0), ins(9, 25, 25, 1), ins(9, 8, 8, -1),
           ins(40, 9, 24, 0))
    a.branch(5, 8, 0, label)
    a.emit(ins(9, 24, 24, 1))


def clear(a, dst, count, label):
    a.address(24, dst)
    a.emit(ins(9, 8, 0, count))
    a.label(label)
    a.emit(ins(40, 0, 24, 0), ins(9, 8, 8, -1))
    a.branch(5, 8, 0, label)
    a.emit(ins(9, 24, 24, 1))


def wrap(image, at, body, *, before=False):
    # Only straight-line prologues are relocated; original function bodies,
    # stack frames, checksum and memory-card IO continue to execute unchanged.
    words = image.words(at, 2)
    require(all(w >> 26 not in (1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23) for w in words),
            f"Discovery wrapper cannot relocate a branch at {at:#x}")
    original = image.append(b"".join(w.to_bytes(4, "little") for w in (*words, jump(at + 8), 0)))
    a = Assembly()
    save(a, [31], 16)
    if before:
        body(a)
    a.emit(jump(original, True), 0)
    if not before:
        body(a)
    restore(a, [31], 16)
    return image.hook(at, a.finish())


def install(image, word_count):
    size = word_count // 8 + 1
    require(0 < word_count and size <= CAPACITY_BYTES,
            "Discovery history exceeds its verified save capacity; do not build an unsafe bitmap")
    if size <= LEGACY_BYTES:
        return {"word_count": word_count, "play_bytes": LEGACY_BYTES, "expanded": False}
    require(SCORE_PAD + 34 * 8 + 4 == SAVE + SAVE_BYTES - 4,
            "Reserved score words overlap the original save trailer")
    bank = image.append(bytes(CAPACITY_BYTES))
    tail = bank + LEGACY_BYTES
    snapshot = image.append(bytes(TAIL_BYTES))
    regs = [8, 9, 10, 24, 25]

    for at in SIZE_SITES:
        require(image.words(at)[0] >> 26 == 9, f"Unexpected discovery size opcode at {at:#x}")
        image.immediate(at, LEGACY_BYTES, size)

    a = Assembly()
    save(a, regs, 48)
    copy(a, bank, PREFIX, LEGACY_BYTES, "prefix")
    a.address(2, bank)
    restore(a, regs, 48)
    getter = image.hook(0x11c7d8, a.finish())

    a = Assembly()
    save(a, regs, 48)
    a.branch(4, 4, 0, "null")
    a.emit(move(2, 0))
    a.address(24, PREFIX)
    a.emit(move(25, 4), ins(9, 8, 0, LEGACY_BYTES))
    a.label("prefix")
    a.emit(ins(36, 9, 25, 0), ins(9, 25, 25, 1), ins(9, 8, 8, -1), ins(40, 9, 24, 0))
    a.branch(5, 8, 0, "prefix")
    a.emit(ins(9, 24, 24, 1))
    copy(a, bank, PREFIX, LEGACY_BYTES, "bank_prefix")
    # a0 may be bank itself. Preserve the live tail before clearing its spare bytes.
    a.address(24, tail)
    a.emit(ins(9, 25, 4, LEGACY_BYTES), ins(9, 8, 0, size - LEGACY_BYTES))
    a.label("tail")
    a.emit(ins(36, 9, 25, 0), ins(9, 25, 25, 1), ins(9, 8, 8, -1), ins(40, 9, 24, 0))
    a.branch(5, 8, 0, "tail")
    a.emit(ins(9, 24, 24, 1))
    if size < CAPACITY_BYTES:
        clear(a, bank + size, CAPACITY_BYTES - size, "spare")
    a.emit(ins(9, 2, 0, -1))
    a.label("null")
    restore(a, regs, 48)
    setter = image.hook(0x11c7e8, a.finish())

    # The word book marks the getter's buffer directly, unlike play buffers
    # which are committed by the setter. Keep the legacy prefix write-through.
    a = Assembly()
    save(a, regs + [4, 5], 64)
    a.emit(ins(9, 5, 5, -1))
    a.address(24, word_count)
    a.emit(rr(43, 8, 5, 24))
    a.branch(4, 8, 0, "invalid")
    a.emit(0, 5 << 16 | 8 << 11 | 3 << 6 | 2, ins(12, 5, 5, 7),
           rr(33, 10, 4, 8), ins(9, 9, 0, 1), rr(4, 9, 5, 9),
           ins(36, 2, 10, 0), rr(37, 2, 2, 9), ins(40, 2, 10, 0))
    a.address(24, bank)
    a.branch(5, 4, 24, "return")
    a.emit(ins(11, 9, 8, LEGACY_BYTES))
    a.branch(4, 9, 0, "return")
    a.emit(0)
    a.address(24, PREFIX)
    a.emit(rr(33, 24, 24, 8), ins(40, 2, 24, 0))
    a.branch(4, 0, 0, "return")
    a.emit(0)
    a.label("invalid")
    a.emit(move(2, 0))
    a.label("return")
    restore(a, regs + [4, 5], 64)
    marker = image.hook(0x1155c0, a.finish())

    def import_tail(a):
        save(a, regs, 48)
        clear(a, tail, TAIL_BYTES, "clear")
        a.address(24, 0x1c7420)
        a.emit(ins(35, 9, 24, 0))
        a.branch(4, 9, 0, "snapshot")
        a.emit(0)
        a.address(24, SCORE_PAD)
        for i, value in enumerate(MAGIC):
            a.address(25, value)
            a.emit(ins(35, 9, 24, i * 8))
            a.branch(5, 9, 25, "snapshot")
            a.emit(0)
        a.emit(ins(9, 24, 24, 24))
        a.address(25, tail)
        a.emit(ins(9, 8, 0, TAIL_BYTES // 4))
        a.label("read")
        for i in range(4):
            a.emit(ins(36, 9, 24, i), ins(40, 9, 25, i))
        a.emit(ins(9, 24, 24, 8), ins(9, 8, 8, -1))
        a.branch(5, 8, 0, "read")
        a.emit(ins(9, 25, 25, 4))
        a.label("snapshot")
        copy(a, snapshot, tail, TAIL_BYTES, "snapshot_copy")
        # This is a wrapper body, not a callable helper.
        for i, r in enumerate(regs):
            a.emit(ins(55, r, 29, i * 8))
        a.emit(ins(9, 29, 29, 48))

    def export_tail(a):
        save(a, regs, 48)
        a.address(24, SCORE_PAD)
        for i, value in enumerate(MAGIC):
            a.address(25, value)
            a.emit(ins(43, 25, 24, i * 8))
        a.emit(ins(9, 24, 24, 24))
        a.address(25, tail)
        a.emit(ins(9, 8, 0, TAIL_BYTES // 4))
        a.label("write")
        for i in range(4):
            a.emit(ins(36, 9, 25, i), ins(40, 9, 24, i))
        a.emit(ins(9, 24, 24, 8), ins(9, 8, 8, -1))
        a.branch(5, 8, 0, "write")
        a.emit(ins(9, 25, 25, 4))
        for i, r in enumerate(regs):
            a.emit(ins(55, r, 29, i * 8))
        a.emit(ins(9, 29, 29, 48))

    def copy_tail(dst, src):
        def body(a):
            save(a, regs, 48)
            copy(a, dst, src, TAIL_BYTES, "tail_copy")
            for i, r in enumerate(regs):
                a.emit(ins(55, r, 29, i * 8))
            a.emit(ins(9, 29, 29, 48))
        return body

    def dirty(a):
        save(a, regs, 48)
        a.branch(5, 2, 0, "done")
        a.emit(0)
        a.address(24, tail)
        a.address(25, snapshot)
        a.emit(ins(9, 8, 0, TAIL_BYTES))
        a.label("compare")
        a.emit(ins(36, 9, 24, 0), ins(36, 10, 25, 0))
        a.branch(5, 9, 10, "changed")
        a.emit(ins(9, 24, 24, 1), ins(9, 8, 8, -1))
        a.branch(5, 8, 0, "compare")
        a.emit(ins(9, 25, 25, 1))
        a.branch(4, 0, 0, "done")
        a.emit(0)
        a.label("changed")
        a.emit(ins(9, 2, 0, -1))
        a.label("done")
        for i, r in enumerate(regs):
            a.emit(ins(55, r, 29, i * 8))
        a.emit(ins(9, 29, 29, 48))

    hooks = {
        "load_or_new": wrap(image, 0x11c100, import_tail),
        "before_checksum": wrap(image, 0x1177a8, export_tail, before=True),
        "snapshot": wrap(image, 0x11c308, copy_tail(snapshot, tail)),
        "rollback": wrap(image, 0x11c230, copy_tail(tail, snapshot)),
        "dirty": wrap(image, 0x11c3d0, dirty),
    }
    return {"word_count": word_count, "legacy_bytes": LEGACY_BYTES, "play_bytes": size,
            "bank": bank, "tail": tail, "snapshot": snapshot,
            "getter": getter, "setter": setter, "marker": marker, "hooks": hooks,
            "save_bytes": SAVE_BYTES, "save_reserved_words": 35,
            "save_tail_bytes": TAIL_BYTES, "maximum_word_id": CAPACITY_BYTES * 8 - 1}
