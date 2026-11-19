"""Reuse MINI native checks only for byte-identical inputs and unchanged verifier code."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import unicorn

from font_tool import write_generated

from build_paths import CAMPAIGN_MODULES, TOOLS, WORK


PATH = WORK / "evidence/mini-validation-cache.json"
FORMAT = "FREAKS-MINI-VERIFIED-CHECKS-v1"
ACTIVE = ContextVar("mini_verified_checks", default=None)
LIMIT = 16


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def checksum(raw):
    return hashlib.sha256(raw).hexdigest()


def revision():
    digest = hashlib.sha256(packed((sys.version, unicorn.__version__)))
    files = sorted((*Path(__file__).parent.glob("*.py"), *CAMPAIGN_MODULES.glob("*.py")))
    for path in files:
        digest.update(path.relative_to(TOOLS).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def decode(raw, current):
    try:
        ledger = json.loads(raw)
        if ledger["format"] != FORMAT or ledger["revision"] != current or len(ledger["entries"]) > LIMIT:
            return {}
        entries = ledger["entries"]
        if not isinstance(entries, dict) or any(
                len(key) != 64 or checksum(packed(row["result"])) != row["sha256"]
                or not isinstance(row["result"], dict) for key, row in entries.items()):
            return {}
        return entries
    except (ValueError, TypeError, KeyError):
        return {}


class Checks:
    def __init__(self, current, entries, progress):
        self.current = current
        self.entries = deepcopy(entries)
        self.progress = progress
        self.hits = self.misses = 0

    def run(self, name, inputs, operation):
        digest = hashlib.sha256(packed((self.current, name)))
        for raw in inputs:
            digest.update(len(raw).to_bytes(8, "little"))
            digest.update(raw)
        key = digest.hexdigest()
        if key in self.entries:
            self.hits += 1
            self.progress(f"[MINI CHECK] {name}: reuse byte-identical verified inputs")
            return deepcopy(self.entries[key]["result"])
        self.misses += 1
        self.progress(f"[MINI CHECK] {name}: running original native functions")
        result = operation()
        if len(self.entries) >= LIMIT:
            self.entries.pop(next(iter(self.entries)))
        self.entries[key] = {"result": deepcopy(result), "sha256": checksum(packed(result))}
        return result

    def commit(self):
        if self.misses:
            write_generated(PATH, packed({"format": FORMAT, "revision": self.current, "entries": self.entries}) + b"\n")


def checked(name, inputs, operation):
    cache = ACTIVE.get()
    return operation() if cache is None else cache.run(name, inputs, operation)


@contextmanager
def reuse(enabled, progress):
    if not enabled:
        token = ACTIVE.set(None)
        try:
            yield None
        finally:
            ACTIVE.reset(token)
        return
    current = revision()
    try:
        entries = decode(PATH.read_bytes(), current)
    except OSError:
        entries = {}
    cache = Checks(current, entries, progress)
    token = ACTIVE.set(cache)
    try:
        yield cache
        try:
            cache.commit()
        except OSError as error:
            progress(f"[MINI CHECK] Cache not saved; checks remain valid: {error}")
    finally:
        ACTIVE.reset(token)
