"""Bounded Korean recordings for the original MINI attract replay player."""

import hashlib
import json
from pathlib import Path
import struct

from elf_probe import Elf
from unpack_disc import require


ROOT = Path(__file__).resolve().parent
from build_paths import DATA


RECORDINGS = DATA / "mini/DemoKorean.json"
TABLE = 0x213468
SLOTS = ((0, 358, 19), (358, 368, 25), (726, 4084, 103), (4810, 3036, 105))
ACTIVE_FRAMES = 1780
PAD_BITS = (0x2000, 0x1000, 0x800, 0x400, 0x200, 0x100, 0x80, 0x40)


def fingerprint(profile, description):
    payload = {key: profile.get(key) for key in
               ("stage", "preplaced", "inventory", "words", "goal_letters", "mode", "random_letters")}
    payload["route"] = description["route"]
    payload["goal_rule"] = description.get("goal_rule")
    payload["goal_targets"] = description.get("goal_targets")
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":")).encode("ascii")).hexdigest()


def encode(frames, lanes):
    require(lanes in (1, 2) and len(frames) <= ACTIVE_FRAMES, "Korean demo exceeds native timeout")
    samples = []
    for frame in frames:
        pads = [frame] if lanes == 1 else frame
        require(isinstance(pads, (list, tuple)) and len(pads) == lanes, "Demo pad lane count differs")
        for pad in pads:
            require(type(pad) is int and pad >= 0 and pad & ~sum(PAD_BITS) == 0,
                    "Unsupported demo pad input")
            samples.append(sum(1 << i for i, bit in enumerate(PAD_BITS) if pad & bit))
    samples.extend([0] * ((ACTIVE_FRAMES - len(frames)) * lanes))
    runs = bytearray()
    for sample in samples:
        if runs and runs[-2] == sample and runs[-1] < 255:
            runs[-1] += 1
        else:
            runs.extend((sample, 1))
    return struct.pack("<II", len(runs) + 8, 1) + runs


def plan(elf, replay, profiles, descriptions):
    original = Elf(elf)
    actual = struct.unpack_from("<12I", elf, original.offset(TABLE))
    require(actual == tuple(v for row in SLOTS for v in row), "Native demo directory changed")
    data = json.loads(RECORDINGS.read_text(encoding="utf-8"))
    require(data["version"] == 1 and data["active_frames"] == ACTIVE_FRAMES
            and hashlib.sha256(replay).hexdigest() == data["source_sha256"],
            "Original demo replay source differs")
    by_stage = {p["stage"]: p for p in profiles}
    descriptions = {p["index"]: p for p in descriptions}
    output, report = bytearray(replay), []
    for offset, capacity, stage in SLOTS:
        if stage not in by_stage:
            continue
        record = data["stages"][str(stage)]
        require(record["profile_sha256"] == fingerprint(by_stage[stage], descriptions[stage]),
                f"Korean demo {stage} profile/word bank changed; re-record and verify its native input")
        raw = bytes.fromhex(record["replay_hex"])
        require(8 < len(raw) <= capacity and struct.unpack_from("<II", raw) == (len(raw), 1)
                and len(raw) % 2 == 0 and all(raw[i] for i in range(9, len(raw), 2)),
                f"Invalid Korean demo recording: {stage}")
        lanes = 2 if stage >= 100 else 1
        require(sum(raw[9::2]) == ACTIVE_FRAMES * lanes, "Demo recording sample count differs")
        output[offset:offset + capacity] = raw.ljust(capacity, b"\0")
        report.append({"stage": stage, "offset": offset, "capacity": capacity, "bytes": len(raw),
                       "lanes": lanes, "active_frames": ACTIVE_FRAMES,
                       "moves": record["moves"], "profile_sha256": record["profile_sha256"]})
    require(len(output) == len(replay), "Demo patch resized the native replay resource")
    return bytes(output), report
