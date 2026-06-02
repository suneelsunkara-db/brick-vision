"""Built-in scorers shared across skills (Phase 2 N20-N26 + Phase 3 N34).

Per `docs/17-eval-framework.md` §13.3. Each scorer returns a normalized
score in [0, 1]. The Phase-2 mechanical-skill scorers (Idempotence,
ClaimCountAssertion, ClaimShapeValidator, FreshnessBeliefMonotonicity,
QuestionEmissionOnFailure) are pure-function scorers that compare two
runs of the same skill against the same fixture.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from brickvision_runtime.eval.scorers import ScorerResult


def claim_count_assertion(
    *,
    emitted: Iterable[dict[str, Any]],
    expected_min: int,
) -> ScorerResult:
    """Pass iff the count of emitted claims meets the minimum."""
    n = sum(1 for _ in emitted)
    ok = n >= expected_min
    return ScorerResult(
        score=1.0 if ok else 0.0,
        reason_codes=() if ok else ("CLAIM_COUNT_BELOW_MIN",),
        details={"emitted_claim_count": n, "expected_min": expected_min},
    )


_REQUIRED_KEYS = ("claim_id", "subject", "predicate", "value_json", "emitted_by", "signature_hex")


def claim_shape_validator(*, emitted: Iterable[dict[str, Any]]) -> ScorerResult:
    rows = list(emitted)
    bad: list[str] = []
    for r in rows:
        for k in _REQUIRED_KEYS:
            if not r.get(k):
                bad.append(f"{r.get('claim_id', '<no_id>')}:{k}")
                break
    ok = not bad
    return ScorerResult(
        score=1.0 if ok else max(0.0, 1.0 - len(bad) / max(1, len(rows))),
        reason_codes=() if ok else ("CLAIM_SHAPE_INVALID",),
        details={"violations": bad[:10], "n_rows": len(rows)},
    )


def idempotence(
    *,
    run1_claims: Iterable[dict[str, Any]],
    run2_claims: Iterable[dict[str, Any]],
) -> ScorerResult:
    """Run-twice produces identical (subject, predicate, value_json) sets."""

    def _key(c: dict[str, Any]) -> tuple[str, str, str]:
        return (c["subject"], c["predicate"], json.dumps(json.loads(c["value_json"]), sort_keys=True))

    a = {_key(c) for c in run1_claims}
    b = {_key(c) for c in run2_claims}
    diff = a.symmetric_difference(b)
    score = 1.0 if not diff else max(0.0, 1.0 - len(diff) / max(1, len(a | b)))
    return ScorerResult(
        score=score,
        reason_codes=() if score == 1.0 else ("IDEMPOTENCE_BROKEN",),
        details={"diff_count": len(diff)},
    )


def freshness_belief_monotonicity(
    *,
    before: Iterable[dict[str, Any]],
    after: Iterable[dict[str, Any]],
) -> ScorerResult:
    """A successful refresh advances LAST_RUN_AT for the claimed source."""
    bmap = {(b["subject"], b["predicate"]): int(b["last_updated_at_ms"]) for b in before}
    regressions: list[str] = []
    for a in after:
        key = (a["subject"], a["predicate"])
        prev = bmap.get(key)
        if prev is not None and int(a["last_updated_at_ms"]) < prev:
            regressions.append(f"{key}:advanced_backwards")
    ok = not regressions
    return ScorerResult(
        score=1.0 if ok else 0.0,
        reason_codes=() if ok else ("FRESHNESS_REGRESSED",),
        details={"regressions": regressions[:10]},
    )


def question_emission_on_failure(
    *,
    sub_skill_failed: bool,
    questions_emitted: Iterable[dict[str, Any]],
) -> ScorerResult:
    """A failed sub-skill must produce at least one Question."""
    questions = list(questions_emitted)
    if not sub_skill_failed:
        return ScorerResult(score=1.0)
    if questions:
        return ScorerResult(score=1.0, details={"q_count": len(questions)})
    return ScorerResult(
        score=0.0,
        reason_codes=("SILENT_FAILURE_DETECTED",),
        details={"q_count": 0},
    )


__all__ = [
    "claim_count_assertion",
    "claim_shape_validator",
    "freshness_belief_monotonicity",
    "idempotence",
    "question_emission_on_failure",
]
