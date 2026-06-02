"""Phase 8 / N67-N72 end-to-end acceptance tests.

These tests bind the three Phase-8 scripts under ``scripts/`` to
pytest so the nightly CI tier can fail the v0.6 gate on any
regression. The actual logic lives in the scripts; this file is a
thin wrapper so engineers can also run ``pytest -k self_bootstrap``
and get the same answer as ``python3 scripts/self_bootstrap_demo.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import self_bootstrap_demo  # noqa: E402
import run_question_paths  # noqa: E402
import independence_test  # noqa: E402


# ---------------------------------------------------------------------------
# N67 + N68 + N69 — round-trip + IR convergence + 95% line overlap.
# ---------------------------------------------------------------------------


def test_self_bootstrap_demo_passes_on_canonical_corpus() -> None:
    """The canonical Layer 0 corpus must round-trip cleanly."""

    summary = self_bootstrap_demo.run_demo()
    assert summary.overall_passed, summary.to_json()
    assert summary.aggregate_overlap >= self_bootstrap_demo.LINE_OVERLAP_FLOOR
    for skill in summary.per_skill:
        assert skill.ir_converged, skill
        assert skill.transpile_idempotent, skill
        assert skill.line_overlap >= self_bootstrap_demo.LINE_OVERLAP_FLOOR, skill


def test_self_bootstrap_demo_flags_missing_skill() -> None:
    """A missing canonical skill must be reported as a typed reason
    code, not a silent pass."""

    summary = self_bootstrap_demo.run_demo(skill_names=("does.not.exist",))
    assert not summary.overall_passed
    assert "SELF_BOOTSTRAP_CANONICAL_SKILL_MISSING" in summary.reason_codes


# ---------------------------------------------------------------------------
# N70 — independence test: harness runs without ``brickvision``.
# ---------------------------------------------------------------------------


def test_independence_test_passes() -> None:
    """The forked child must be able to import every runtime module
    while ``brickvision`` (the build-time pkg) is blocked."""

    result = independence_test.run_independence_test()
    assert result["ok"], result["findings"]


# ---------------------------------------------------------------------------
# N71 — three v0.6 question paths.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expected_path",
    [
        "profile_workspace",
        "why_is_table_slow",
        "onboard_team_minimally",
    ],
)
def test_question_path_emits_typed_artifacts(expected_path: str) -> None:
    summary = run_question_paths.run_question_paths()
    matches = [p for p in summary.paths if p.name == expected_path]
    assert matches, f"missing question path: {expected_path}"
    path = matches[0]
    assert path.reason_codes == (), path
    assert path.audit_rows_signed >= 1
    assert path.claims_emitted >= 1


def test_question_paths_summary_overall_passes() -> None:
    summary = run_question_paths.run_question_paths()
    assert summary.overall_passed, summary.to_json()
    assert summary.reason_codes == ()
