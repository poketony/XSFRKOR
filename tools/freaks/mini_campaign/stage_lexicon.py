"""Stage-local dictionary policy and registration audits, not path proofs.

``lore_words`` is authoring metadata containing reviewed DB readings, not a
solution whitelist. The build must strip it before calling ``stage_bytes``.
``word_exclusions`` is an explicit stage-local ordinary-word omission list.
Candidates still come from the existing STD/KRdict/JPN and reviewed DB policy.
"""

from itertools import combinations

from mini_lexicon_source import search_key


def _keys(words):
    return {search_key(word) for word in words}


def eligible_words(candidates, letters, db_keys, allowed_lore, stage_index, *, ordinary_only=False, goal_targets=(), ordinary_keys=(), excluded_words=()):
    """Return sorted character-set matches with long DB keys local to a stage.

    Readings/assignments use ``corpus.search_key`` (NFC, no whitespace).
    Every assignment must exist in both candidates and the reviewed DB keys.
    DB keys of at most three syllables need no assignment. DB membership wins
    over a matching standard headword for longer keys. No stock multiplicity,
    geometry, move order, goals, or route is inferred here.

    ``ordinary_only`` retains non-DB keys, source-attested ``ordinary_keys``
    homographs and required exact-word ``goal_targets``. All must already pass
    the normal stage policy. Pass ordinary source keys before subtracting DB
    keys; this changes eligibility, not the canonical DB dictionary definition.
    One-tile targets do not authorize DB stepping stones.
    ``excluded_words`` explicitly removes ordinary candidates for a curated
    stage bank. Unknown keys and reviewed DB exclusions are errors.
    Stage 34 bypasses normalization, validation and all filters: its result is
    exactly the prior sorted character-set projection, including its DB bank.
    """
    letters = set(letters)
    if stage_index == 34:
        return sorted(word for word in set(candidates) if set(word) <= letters)
    candidates, db_keys, allowed = _keys(candidates), _keys(db_keys), _keys(allowed_lore)
    missing = allowed - candidates
    if missing:
        raise ValueError(f"Stage {stage_index} lore keys are not candidates: {', '.join(sorted(missing))}")
    unknown = allowed - db_keys
    if unknown:
        raise ValueError(f"Stage {stage_index} lore keys are not reviewed DB keys: {', '.join(sorted(unknown))}")
    excluded = _keys(excluded_words)
    missing = excluded - candidates
    if missing:
        raise ValueError(f"Stage {stage_index} excluded keys are not candidates: {', '.join(sorted(missing))}")
    if excluded & db_keys:
        raise ValueError(f"Stage {stage_index} cannot exclude reviewed DB keys: {', '.join(sorted(excluded & db_keys))}")
    words = {word for word in candidates if set(word) <= letters
             and word not in excluded and (word not in db_keys or len(word) <= 3 or word in allowed)}
    if not ordinary_only:
        return sorted(words)
    required = {word for word in _keys(goal_targets) if len(word) > 1}
    missing = required - words
    if missing:
        raise ValueError(f"Stage {stage_index} fallback goals are not stage-eligible: {', '.join(sorted(missing))}")
    ordinary = _keys(ordinary_keys)
    return sorted(word for word in words if word not in db_keys or word in ordinary or word in required)


def audit_frozen_agws(frozen, profile, bank_keys, *, serialized_sha256=None, runtime_sha256=None,
                      source_txt_sha256=None):
    """Compare authoring data/digests with the current FROZEN-AGWS.json object.

    Hash arguments are computed by the caller using the snapshot producer's
    exact encodings. In particular ``runtime_sha256`` is not just the word-bank
    hash. Missing digests are explicitly unchecked, and ``complete`` is false;
    no ELF addresses or alternate serialization are guessed here.
    """
    actual = {"profile": profile, "bank_keys": sorted(bank_keys),
              "serialized_sha256": serialized_sha256, "runtime_sha256": runtime_sha256,
              "source_txt_sha256": source_txt_sha256}
    if frozen["profile"]["index"] != 34 or profile["index"] != 34:
        raise ValueError("The frozen A.G.W.S. audit is only for stage 34")
    unchecked = [key for key, value in actual.items() if value is None]
    errors = [f"Stage 34 frozen {key} differs" for key, value in actual.items()
              if value is not None and value != frozen[key]]
    return {"errors": errors, "unchecked": unchecked, "complete": not errors and not unchecked}


def audit_banks(profiles, banks, candidates, db_keys, *, frozen_agws=None):
    """Audit actual key banks against stage metadata; return JSON-ready evidence.

    ``profiles`` are config records with index/preplaced/inventory and optional
    lore_words/goal_targets/word_exclusions. ``banks`` maps stage indices to dictionary *keys*,
    not word IDs. Include all stages in scope to detect cross-stage leakage.
    Invalid metadata, missing banks, bank omissions/extras, unavailable themes,
    and missing goal registrations are reported in ``errors``. An empty error
    list establishes registration policy only, never a reachable native path.

    Multiple explicit owners are reported rather than silently prohibited;
    short DB names can be common. Stage 34 remains a frozen-policy exception.
    ``frozen_agws`` optionally checks stage 34's exact profile and bank keys
    against FROZEN-AGWS.json. Complete byte/digest verification uses
    ``audit_frozen_agws`` separately with caller-computed hashes.
    """
    candidates, db_keys = set(candidates), _keys(db_keys)
    canonical_candidates = _keys(candidates)
    long_db = {word for word in db_keys if len(word) > 3}
    by_index = {}
    for profile in profiles:
        index = profile["index"]
        if type(index) is not int or index < 0 or index in by_index:
            raise ValueError("Duplicate or invalid stage index in lexicon audit")
        by_index[index] = profile
    banks = {index: set(words) for index, words in banks.items()}
    errors, stages, owners = [], [], {}
    for index in sorted(banks.keys() - by_index.keys()):
        errors.append(f"Stage {index} bank has no profile")
    for index, profile in sorted(by_index.items()):
        letters = set(profile["preplaced"] + profile["inventory"]) - {"?", "."}
        allowed = _keys(profile.get("lore_words", ()))
        for word in sorted(allowed & long_db):
            owners.setdefault(word, []).append(index)
        if index not in banks:
            errors.append(f"Stage {index} has no bank")
        bank = banks.get(index, set())
        # Do not normalize the frozen bank and hide a literal-key difference.
        if index != 34:
            bank = _keys(bank)
        for word in sorted(bank & long_db):
            actual = owners.setdefault(word, [])
            if index not in actual:
                actual.append(index)
        expected = None
        try:
            # Keep globally present assignments for validation even when their
            # letters are unavailable here. Stage 34 keeps literal legacy keys.
            projected = (candidates if index == 34 else
                         {word for word in canonical_candidates if set(word) <= letters}
                         | (allowed & canonical_candidates))
            expected = set(eligible_words(projected, letters, db_keys, allowed, index,
                                          excluded_words=profile.get("word_exclusions", ())))
        except ValueError as exc:
            errors.append(str(exc))
        missing = sorted(expected - bank) if expected is not None else []
        extra = sorted(bank - expected) if expected is not None else []
        leaked = sorted(bank & long_db - allowed) if index != 34 else []
        missing_lore = sorted(allowed - bank)
        goals = _keys(profile.get("goal_targets", ())) - {""}
        missing_goals = sorted(word for word in goals
                               if not (any(word in key for key in bank) if len(word) == 1 else word in bank))
        for label, words in (("missing eligible keys", missing), ("unexpected bank keys", extra),
                             ("unowned long DB keys", leaked), ("unregistered lore keys", missing_lore),
                             ("unregistered goal keys", missing_goals)):
            if words:
                errors.append(f"Stage {index} {label}: {', '.join(words)}")
        stages.append({"index": index, "frozen_policy": index == 34,
                       "registered_keys": len(bank), "ordinary_keys": len(bank - db_keys),
                       "short_db_keys": len(bank & (db_keys - long_db)),
                       "long_db_keys": sorted(bank & long_db),
                       "allowed_lore": sorted(allowed), "registered_lore": sorted(allowed & bank),
                       "excluded_keys": sorted(_keys(profile.get("word_exclusions", ()))),
                       "missing_lore": missing_lore, "missing_keys": missing,
                       "unexpected_keys": extra, "unowned_long_db_keys": leaked,
                       "goal_keys": sorted(goals), "missing_goal_keys": missing_goals,
                       "reachability_proven": None})
    pairwise = []
    for left, right in combinations(sorted(banks.keys() & by_index.keys()), 2):
        a = banks[left] if left == 34 else _keys(banks[left])
        b = banks[right] if right == 34 else _keys(banks[right])
        shared, union = a & b, a | b
        pairwise.append({"stages": [left, right], "shared_keys": len(shared),
                         "union_keys": len(union), "jaccard": len(shared) / len(union) if union else 0.0,
                         "shared_ordinary_keys": len(shared - db_keys),
                         "shared_short_db_keys": len(shared & (db_keys - long_db)),
                         "shared_long_db_keys": sorted(shared & long_db)})
    frozen_check = None
    if frozen_agws is not None:
        if 34 not in by_index or 34 not in banks:
            errors.append("Stage 34 is missing from the frozen audit")
        else:
            frozen_check = audit_frozen_agws(frozen_agws, by_index[34], banks[34])
            errors.extend(frozen_check["errors"])
    return {"errors": errors, "long_db_owners": dict(sorted(owners.items())), "frozen_agws": frozen_check,
            "stages": stages, "pairwise": pairwise,
            "scope": "Dictionary registration and character-set coverage only; "
                     "not a route, native reachability, or play-quality proof."}
