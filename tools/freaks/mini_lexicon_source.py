"""Project-ready dictionary source identities, spelling and candidate keys."""

import csv
import gzip
import unicodedata

from build_paths import DATA
from unpack_disc import require


LOOKUP = DATA / "mini/lexicon.tsv.gz"


def search_key(word):
    return "".join(unicodedata.normalize("NFC", word).split())


def common_rows():
    with gzip.open(LOOKUP, "rt", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        require(reader.fieldnames == ["entry_id", "sense_id", "key", "display"],
                "MINI lexicon lookup columns differ")
        rows = list(reader)
    require(rows and all(row["entry_id"] and row["sense_id"] and row["key"] and row["display"] for row in rows),
            "Missing MINI lexicon lookup fields")
    require(len({(row["entry_id"], row["sense_id"]) for row in rows}) == len(rows),
            "MINI lexicon lookup has conflicting source identities")
    return rows
