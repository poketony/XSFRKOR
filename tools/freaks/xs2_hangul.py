"""Independent copy of XS2's approved KS X 1001 Hangul-to-kanji table."""

from functools import lru_cache
import hashlib
import json
from build_paths import DATA
from unpack_disc import require


TABLE = DATA / "XENOSAGA KOR-JPN.json"
TABLE_HASH = "1f461ef9eee4b58a856ef214e80f38ffa84c24865bddd7a494662685f00ab007"


@lru_cache(maxsize=1)
def rows():
    data = TABLE.read_bytes()
    require(hashlib.sha256(data).hexdigest() == TABLE_HASH, "Approved Hangul table changed")
    pairs = json.loads(data.decode("utf-8-sig"))["replace-table"]
    codes = [bytes((a, b)) for a in range(0xb0, 0xc9) for b in range(0xa1, 0xff)]
    require(len(pairs) == 2350 and list(pairs) == [c.decode("euc_kr") for c in codes],
            "Hangul table is not the 2350-character EUC-KR order")
    require([j.encode("euc_jp") for j in pairs.values()] == codes, "Hangul/kanji byte positions differ")
    return tuple(pairs.items())


def mapping(characters, encoding):
    table = {k: j.encode(encoding) for k, j in rows()}
    chars = set(characters)
    require(chars <= table.keys(), "Only the fixed EUC-KR 2350 Hangul syllables are supported")
    return {k: table[k] for k, _ in rows() if k in chars}
