"""Contract tests for ``NoMockOrFakeImplementations`` (N189 track A).

Per [`docs/17-eval-framework.md`](../../docs/17-eval-framework.md) §13.3
production-package discipline + [`docs/01-overview.md`](../../docs/01-overview.md)
§0 rule 15. The gold set lives at
``tests/fixtures/discipline_rule_15/`` (12 fixtures: 6 violation +
6 clean) and is the canonical contract surface for the scorer.

The tests run the scorer against each fixture group and assert on:

1. The violation group produces ≥ 6 hard violations across both
   reason codes (``MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE`` +
   ``PROTOCOL_HAS_ONLY_MOCK_SUBCLASSES``).
2. Each individual violation file produces the kind it advertises in
   its docstring (``class``, ``function``, ``filename``, or
   ``protocol``).
3. The clean group produces 0 hard violations and ``score == 1.0``.
4. The ``soft_warn_files`` argument moves matched-file violations
   from ``details["violations"]`` into ``details["soft_warnings"]``
   without changing the score for clean files.
5. The default soft-warn allowance covers exactly the 13 v0.7.7-cascade
   files enumerated in
   ``docs/24-pending-tasks-tracker.md`` §24.3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brickvision_runtime.eval.scorers.no_mock_or_fake_implementations import (
    _V077_CASCADE_SOFT_WARN_FILES,
    no_mock_or_fake_implementations,
    scan_paths,
)
from brickvision_runtime.failures import ReasonCode

_FIXTURES_ROOT = Path(__file__).parent.parent / "fixtures" / "discipline_rule_15"
_VIOLATION_ROOT = _FIXTURES_ROOT / "violation"
_CLEAN_ROOT = _FIXTURES_ROOT / "clean"


# ---------------------------------------------------------------------------
# Sanity — fixture set is intact.
# ---------------------------------------------------------------------------


def test_violation_fixtures_count_is_six() -> None:
    files = sorted(p.name for p in _VIOLATION_ROOT.glob("v*.py"))
    assert files == [
        "v01_class_prefix_fake.py",
        "v02_class_prefix_mock.py",
        "v03_class_prefix_dummy.py",
        "v04_function_prefix_fake.py",
        "v05_filename_stub.py",
        "v06_protocol_only_mock_subclass.py",
    ]


def test_clean_fixtures_count_is_six() -> None:
    files = sorted(p.name for p in _CLEAN_ROOT.glob("c*.py"))
    assert files == [
        "c01_real_wrapper.py",
        "c02_env_gated_function.py",
        "c03_protocol_with_real_subclass.py",
        "c04_private_protocol.py",
        "c05_legitimate_filename.py",
        "c06_dry_run_branch.py",
    ]


# ---------------------------------------------------------------------------
# Violation group — every file fails as advertised.
# ---------------------------------------------------------------------------


def test_violation_group_score_is_zero_with_both_reason_codes() -> None:
    res = no_mock_or_fake_implementations(
        roots=[_VIOLATION_ROOT],
        soft_warn_files=(),
    )
    assert res.score == 0.0
    assert ReasonCode.MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE.value in res.reason_codes
    assert ReasonCode.PROTOCOL_HAS_ONLY_MOCK_SUBCLASSES.value in res.reason_codes
    violations = res.details["violations"]
    assert isinstance(violations, list)
    assert len(violations) >= 6


def test_violation_v01_class_prefix_fake_flagged() -> None:
    raw = scan_paths([_VIOLATION_ROOT / "v01_class_prefix_fake.py"])
    kinds = {v.kind for v in raw}
    names = {v.name for v in raw}
    assert "class" in kinds
    assert "FakeFMSClient" in names


def test_violation_v02_class_prefix_mock_flagged() -> None:
    raw = scan_paths([_VIOLATION_ROOT / "v02_class_prefix_mock.py"])
    kinds = {v.kind for v in raw}
    names = {v.name for v in raw}
    assert "class" in kinds
    assert "MockWorkspaceClient" in names


def test_violation_v03_class_prefix_dummy_flagged() -> None:
    raw = scan_paths([_VIOLATION_ROOT / "v03_class_prefix_dummy.py"])
    kinds = {v.kind for v in raw}
    names = {v.name for v in raw}
    assert "class" in kinds
    assert "DummyTracer" in names


def test_violation_v04_function_prefix_fake_flagged() -> None:
    raw = scan_paths([_VIOLATION_ROOT / "v04_function_prefix_fake.py"])
    kinds = {v.kind for v in raw}
    names = {v.name for v in raw}
    assert "function" in kinds
    assert "fake_runner" in names


def test_violation_v05_filename_stub_flagged() -> None:
    raw = scan_paths([_VIOLATION_ROOT / "v05_filename_stub.py"])
    kinds = {v.kind for v in raw}
    assert "filename" in kinds


def test_violation_v06_protocol_only_mock_subclass_flagged() -> None:
    # The fixture defines BOTH the Protocol and its only Fake* subclass
    # in the same file, so two violations are expected: the Fake* class
    # name AND the protocol-only-mock-subclass seam.
    raw = scan_paths([_VIOLATION_ROOT / "v06_protocol_only_mock_subclass.py"])
    kinds = {v.kind for v in raw}
    names = {v.name for v in raw}
    assert "class" in kinds
    assert "protocol" in kinds
    assert "FakeEmbeddingClient" in names
    assert "EmbeddingClient" in names


# ---------------------------------------------------------------------------
# Clean group — every file passes.
# ---------------------------------------------------------------------------


def test_clean_group_score_is_one() -> None:
    res = no_mock_or_fake_implementations(
        roots=[_CLEAN_ROOT],
        soft_warn_files=(),
    )
    assert res.score == 1.0
    assert res.reason_codes == ()
    assert res.details["violations"] == []
    assert res.details["soft_warnings"] == []


@pytest.mark.parametrize(
    "fixture",
    [
        "c01_real_wrapper.py",
        "c02_env_gated_function.py",
        "c03_protocol_with_real_subclass.py",
        "c04_private_protocol.py",
        "c05_legitimate_filename.py",
        "c06_dry_run_branch.py",
    ],
)
def test_clean_individual_files_have_no_violations(fixture: str) -> None:
    raw = scan_paths([_CLEAN_ROOT / fixture])
    assert raw == []


# ---------------------------------------------------------------------------
# Soft-warn allowance.
# ---------------------------------------------------------------------------


def test_soft_warn_files_downgrade_violation_files_to_warnings() -> None:
    soft = (_VIOLATION_ROOT / "v01_class_prefix_fake.py").as_posix()
    res = no_mock_or_fake_implementations(
        roots=[_VIOLATION_ROOT],
        soft_warn_files={soft},
    )
    soft_violation_files = {v["file"] for v in res.details["soft_warnings"]}
    hard_violation_files = {v["file"] for v in res.details["violations"]}
    assert soft in soft_violation_files
    assert soft not in hard_violation_files


def test_clean_group_unaffected_by_soft_warn_argument() -> None:
    bogus_soft = "src/brickvision_runtime/does_not_exist.py"
    res = no_mock_or_fake_implementations(
        roots=[_CLEAN_ROOT],
        soft_warn_files={bogus_soft},
    )
    assert res.score == 1.0
    assert res.details["violations"] == []
    assert res.details["soft_warnings"] == []


# ---------------------------------------------------------------------------
# Default v0.7.7-cascade soft-warn allowance.
# ---------------------------------------------------------------------------


def test_default_soft_warn_allowance_is_empty_post_n189_close() -> None:
    # N189 close (per docs/24-pending-tasks-tracker.md §24.3) — every
    # cascade item retired; the default allowance is now an empty
    # frozenset and any future violation must hard-fail CI rather
    # than land via soft-warn.
    assert _V077_CASCADE_SOFT_WARN_FILES == frozenset()


def test_default_soft_warn_allowance_excludes_all_closed_items() -> None:
    # Both sub-table A (capability_graph cascade) and sub-table B
    # (pre-existing v0.7.5/v0.7.6.x/Phase -1 mocks) are closed under
    # N189; the default allowance must NOT name any of the 12
    # historically-tracked files.
    for closed in (
        # Sub-table A — capability_graph cascade.
        "src/brickvision_runtime/capability_graph/embed.py",
        "src/brickvision_runtime/capability_graph/persist.py",
        "src/brickvision_runtime/capability_graph/vs_upsert.py",
        "src/brickvision_runtime/capability_graph/smoke.py",
        "src/brickvision_runtime/capability_graph/promote.py",
        "src/brickvision_runtime/capability_graph/retention.py",
        "src/brickvision_runtime/capability_graph/graph_builder.py",
        # Sub-table B — pre-existing.
        "src/brickvision/stages/agent_design_stub.py",
        "src/brickvision_runtime/telemetry/central_stub.py",
        "src/brickvision_runtime/context_engine/sub_agent.py",
        "src/brickvision_runtime/harness/coordinator.py",
    ):
        assert closed not in _V077_CASCADE_SOFT_WARN_FILES


# ---------------------------------------------------------------------------
# Production roots (default) — discipline rule 15 acceptance gate.
#
# N189 closed: with the soft-warn allowance now empty, the default
# scorer must report 0 hard violations across the production roots.
# Any future Fake*/Mock*/Stub*/Dummy* class, *_stub/*_fake/*_mock/
# *_dummy filename, or Protocol-with-only-mock-subclass seam will
# hard-fail CI.
# ---------------------------------------------------------------------------


def test_production_roots_pass_with_empty_soft_warn_allowance() -> None:
    res = no_mock_or_fake_implementations()
    hard = res.details["violations"]
    soft = res.details["soft_warnings"]
    assert isinstance(hard, list)
    assert isinstance(soft, list)
    if hard:
        msg = "Unexpected discipline-rule-15 hard violations:\n"
        for v in hard:
            msg += f"  {v['file']}:{v['lineno']} [{v['kind']}] {v['name']} — {v['detail']}\n"
        msg += (
            "Discipline rule 15 is in hard-fail mode after N189 close — fix the"
            " production code (env-gate via BV_FAKE_LLM/BV_DRY_RUN, move to"
            " tests/fixtures/, or rename to a domain noun) rather than adding"
            " a soft-warn entry."
        )
        pytest.fail(msg)
    assert soft == []
    assert res.score == 1.0
