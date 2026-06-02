"""N173 BULK — joint dual-substrate PPR contract tests.

These tests pin the C.1 BULK-specific contracts that the N187 self-bootstrap
suite (``tests/unit/test_self_bootstrap_capability_graph.py``) does NOT
exercise:

1. **Authority arbitration** — capability-graph seeds are weighted by
   per-row ``authority_weight`` (sourced from
   ``<BV_CATALOG>.<BV_SCHEMA>.source_authority`` per
   ``docs/23-databricks-capability-graph.md`` §23.1); a low-authority
   capability seed ranks below a high-authority capability seed when
   lexical relevance is held constant.
2. **Predicate-conflict mismatch detection** — when both substrates assert
   the same ``(subject, predicate)`` with different ``value_json``,
   ``mismatch_subjects`` populates and ``CAPABILITY_WORKSPACE_MISMATCH``
   appears in ``reason_codes`` per ``docs/14-context-engineering.md``
   §11.5.1.A + ``docs/05-build-pipeline.md`` §7.6.
3. **Complementary facts are NOT mismatches** — same subject, different
   predicates → no mismatch (the joint PPR walk merges complementary
   facts).
4. **Substrate attribution** — refs whose subject is unique to one
   substrate are attributed to that substrate; shared subjects go to the
   substrate with the higher authority-weighted lex score (workspace
   wins ties since workspace facts are factual about the partner's
   environment).
5. **Empty workspace + populated capability** — the inverse of the SHELL
   passthrough: ``workspace_refs`` empty, ``capability_refs`` populated.
6. **Malformed authority_weight falls back to default** — ``None`` /
   ``str`` / ``bool`` rows quietly use ``_DEFAULT_CAPABILITY_AUTHORITY``
   so a single malformed indexer row can never silently shift
   arbitration.

Together with the 9 N187 tests these 12 tests fully pin the N173 BULK.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC = REPO_ROOT / "src"
if str(_SRC) not in sys.path:  # pragma: no cover — defensive
    sys.path.insert(0, str(_SRC))


from brickvision_runtime.capability_graph import retrieve as retrieve_mod
from brickvision_runtime.capability_graph.retrieve import (
    CapabilityGraphSnapshot,
    DualSubstrateResult,
    _DEFAULT_CAPABILITY_AUTHORITY,
    _capability_authority_index,
    _detect_mismatches,
    kg_search_dual_substrate,
)
from brickvision_runtime.failures import ReasonCode
from brickvision_runtime.kg.retriever import KGSearchDefaults


_QUERY = "create a delta table in unity catalog"
_DEFAULTS = KGSearchDefaults()


def _pinned_snapshot() -> CapabilityGraphSnapshot:
    """A non-None ``CapabilityGraphSnapshot`` so the BULK branch fires."""

    return CapabilityGraphSnapshot(
        snapshot_id="snap-n173-bulk-test",
        promoted_at_ms=1_700_000_000_000,
        is_active=True,
        sources_complete=("sdk", "openapi", "docs", "labs", "blog"),
        sources_partial=(),
    )


# ---------------------------------------------------------------------------
# Authority arbitration.
# ---------------------------------------------------------------------------


def test_authority_arbitration_ranks_high_authority_capability_above_low(
    monkeypatch,
) -> None:
    """High-authority capability seeds (``authority_weight=1.0``) MUST
    rank above otherwise-equivalent low-authority seeds
    (``authority_weight=0.5``) — this is the ``source_authority``
    arbitration §11.5.1.A pins."""

    monkeypatch.setattr(retrieve_mod, "_active_snapshot", _pinned_snapshot)

    capability_claims = [
        {
            "claim_id": "cap-high",
            "subject": "meta:delta-lake/ext:create-table-high",
            "predicate": "create_table",
            "value_json": "delta",
            "metadata_json": "create a delta table in unity catalog: HIGH",
            "authority_weight": 1.0,  # SDK
        },
        {
            "claim_id": "cap-low",
            "subject": "meta:delta-lake/ext:create-table-low",
            "predicate": "create_table",
            "value_json": "delta",
            "metadata_json": "create a delta table in unity catalog: LOW",
            "authority_weight": 0.5,  # blog
        },
    ]
    workspace_claims = [
        {
            "claim_id": "ws-c1",
            "subject": "uc.catalog.bv_bronze",
            "predicate": "exists",
            "value_json": "true",
            "metadata_json": "workspace catalog",
        },
    ]

    result = kg_search_dual_substrate(
        query=_QUERY,
        workspace_claims=workspace_claims,
        workspace_edges=[],
        capability_substrate_claims=capability_claims,
        capability_substrate_edges=[],
        k=10,
    )
    assert isinstance(result, DualSubstrateResult)

    cap_refs = list(result.capability_refs)
    high_idx = next(i for i, r in enumerate(cap_refs) if r.claim_id == "cap-high")
    low_idx = next(i for i, r in enumerate(cap_refs) if r.claim_id == "cap-low")
    assert high_idx < low_idx, (
        "authority arbitration broken: low-authority capability seed "
        "ranked above high-authority seed at otherwise-identical lexical "
        f"relevance (cap_refs={[r.claim_id for r in cap_refs]})"
    )


def test_capability_authority_index_skips_malformed_rows() -> None:
    """``_capability_authority_index`` MUST skip rows with non-numeric
    ``authority_weight`` (None / str / bool / missing) so a malformed
    indexer row can never silently shift arbitration. Skipped rows
    fall back to ``_DEFAULT_CAPABILITY_AUTHORITY`` at the call site."""

    rows = [
        {"claim_id": "ok", "authority_weight": 0.85},
        {"claim_id": "ok-int", "authority_weight": 1},
        {"claim_id": "missing"},
        {"claim_id": "none-val", "authority_weight": None},
        {"claim_id": "str-val", "authority_weight": "0.85"},
        {"claim_id": "bool-val", "authority_weight": True},  # bool is int but rejected
    ]
    idx = _capability_authority_index(rows)
    assert idx == {"ok": 0.85, "ok-int": 1.0}


# ---------------------------------------------------------------------------
# Mismatch detection.
# ---------------------------------------------------------------------------


def test_mismatch_when_shared_subject_predicate_disagrees(monkeypatch) -> None:
    """Shared subject + same predicate + different ``value_json`` →
    ``CAPABILITY_WORKSPACE_MISMATCH`` lands in ``reason_codes`` and
    the subject lands in ``mismatch_subjects``."""

    monkeypatch.setattr(retrieve_mod, "_active_snapshot", _pinned_snapshot)

    capability_claims = [
        {
            "claim_id": "cap-x",
            "subject": "uc.catalog.shared",
            "predicate": "owner",
            "value_json": "data-platform",
            "metadata_json": "capability docs say data-platform owns it",
            "authority_weight": 0.85,
        },
    ]
    workspace_claims = [
        {
            "claim_id": "ws-x",
            "subject": "uc.catalog.shared",
            "predicate": "owner",
            "value_json": "legacy-team",
            "metadata_json": "workspace says legacy-team owns it",
        },
    ]

    result = kg_search_dual_substrate(
        query="who owns uc.catalog.shared",
        workspace_claims=workspace_claims,
        workspace_edges=[],
        capability_substrate_claims=capability_claims,
        capability_substrate_edges=[],
        k=10,
    )

    assert "uc.catalog.shared" in result.mismatch_subjects
    assert (
        ReasonCode.CAPABILITY_WORKSPACE_MISMATCH.value in result.reason_codes
    ), (
        f"expected CAPABILITY_WORKSPACE_MISMATCH in reason_codes; got "
        f"{result.reason_codes}"
    )


def test_no_mismatch_when_shared_subject_predicates_complementary(
    monkeypatch,
) -> None:
    """Shared subject but different predicates → NOT a mismatch.
    Capability says ``EXISTS=true``; workspace says ``OWNED_BY=team-x``.
    These are complementary facts that the joint PPR walk merges; the
    pipeline does NOT pause for HITL triage."""

    monkeypatch.setattr(retrieve_mod, "_active_snapshot", _pinned_snapshot)

    capability_claims = [
        {
            "claim_id": "cap-x",
            "subject": "uc.catalog.shared",
            "predicate": "exists",
            "value_json": "true",
            "metadata_json": "capability says it exists",
            "authority_weight": 0.85,
        },
    ]
    workspace_claims = [
        {
            "claim_id": "ws-x",
            "subject": "uc.catalog.shared",
            "predicate": "owned_by",
            "value_json": "team-x",
            "metadata_json": "workspace says team-x owns it",
        },
    ]

    result = kg_search_dual_substrate(
        query="who owns uc.catalog.shared",
        workspace_claims=workspace_claims,
        workspace_edges=[],
        capability_substrate_claims=capability_claims,
        capability_substrate_edges=[],
        k=10,
    )

    assert result.mismatch_subjects == ()
    assert (
        ReasonCode.CAPABILITY_WORKSPACE_MISMATCH.value
        not in result.reason_codes
    )


def test_no_mismatch_when_subjects_disjoint(monkeypatch) -> None:
    """Disjoint subjects (workspace and capability speak about different
    entities) → no mismatch even if their predicates and values
    accidentally collide."""

    monkeypatch.setattr(retrieve_mod, "_active_snapshot", _pinned_snapshot)

    capability_claims = [
        {
            "claim_id": "cap-x",
            "subject": "meta:delta-lake/ext:create-table",
            "predicate": "kind",
            "value_json": "delta",
            "metadata_json": "create a delta table in unity catalog",
            "authority_weight": 1.0,
        },
    ]
    workspace_claims = [
        {
            "claim_id": "ws-x",
            "subject": "uc.catalog.bv_bronze",
            "predicate": "kind",
            "value_json": "delta",
            "metadata_json": "delta in unity catalog",
        },
    ]

    result = kg_search_dual_substrate(
        query=_QUERY,
        workspace_claims=workspace_claims,
        workspace_edges=[],
        capability_substrate_claims=capability_claims,
        capability_substrate_edges=[],
        k=10,
    )

    assert result.mismatch_subjects == ()
    assert (
        ReasonCode.CAPABILITY_WORKSPACE_MISMATCH.value
        not in result.reason_codes
    )


def test_detect_mismatches_helper_returns_sorted_unique_subjects() -> None:
    """``_detect_mismatches`` returns a sorted, deduplicated tuple."""

    cap_claims = [
        {"claim_id": "c1", "subject": "z", "predicate": "p", "value_json": "1"},
        {"claim_id": "c2", "subject": "a", "predicate": "p", "value_json": "1"},
        # second predicate on `a` also disagreeing — should still produce
        # only one entry for `a`
        {"claim_id": "c3", "subject": "a", "predicate": "q", "value_json": "1"},
    ]
    ws_claims = [
        {"claim_id": "w1", "subject": "z", "predicate": "p", "value_json": "2"},
        {"claim_id": "w2", "subject": "a", "predicate": "p", "value_json": "2"},
        {"claim_id": "w3", "subject": "a", "predicate": "q", "value_json": "2"},
    ]
    out = _detect_mismatches(
        capability_claims=cap_claims,
        workspace_claims=ws_claims,
        shared_subjects={"a", "z"},
    )
    assert out == ("a", "z")


# ---------------------------------------------------------------------------
# Substrate attribution for shared subjects.
# ---------------------------------------------------------------------------


def test_shared_subject_with_higher_capability_authority_attributes_to_capability(
    monkeypatch,
) -> None:
    """When a subject appears in both substrates, attribution goes to
    the substrate whose authority-weighted lex score is higher. With
    capability ``authority_weight=1.0`` on a strong lexical match and
    workspace lexical relevance = 0 for the same subject, capability
    wins."""

    monkeypatch.setattr(retrieve_mod, "_active_snapshot", _pinned_snapshot)

    capability_claims = [
        {
            "claim_id": "cap-x",
            "subject": "uc.catalog.shared",
            "predicate": "kind",
            "value_json": "managed",
            "metadata_json": "create a delta table in unity catalog",
            "authority_weight": 1.0,
        },
    ]
    # Workspace mentions the subject but with no overlap to the query
    # tokens so its lexical score is 0; thus only capability surfaces it.
    workspace_claims = [
        {
            "claim_id": "ws-x",
            "subject": "uc.catalog.shared",
            "predicate": "owner",
            "value_json": "team-x",
            "metadata_json": "qqq aaa nothing",
        },
    ]

    result = kg_search_dual_substrate(
        query=_QUERY,
        workspace_claims=workspace_claims,
        workspace_edges=[],
        capability_substrate_claims=capability_claims,
        capability_substrate_edges=[],
        k=10,
    )

    cap_subjects = {r.subject for r in result.capability_refs}
    ws_subjects = {r.subject for r in result.workspace_refs}
    assert "uc.catalog.shared" in cap_subjects, (
        "shared subject with high cap authority should attribute to "
        "capability_refs"
    )
    # NOT in workspace_refs (workspace lex score is 0 → not eligible).
    assert "uc.catalog.shared" not in ws_subjects


# ---------------------------------------------------------------------------
# Workspace-empty + capability-populated.
# ---------------------------------------------------------------------------


def test_empty_workspace_with_populated_capability(monkeypatch) -> None:
    """Inverse of the SHELL passthrough: workspace_claims empty,
    capability populated → workspace_refs empty, capability_refs
    populated, no mismatches."""

    monkeypatch.setattr(retrieve_mod, "_active_snapshot", _pinned_snapshot)

    capability_claims = [
        {
            "claim_id": "cap-1",
            "subject": "meta:delta-lake/ext:create-table",
            "predicate": "create_table",
            "value_json": "delta",
            "metadata_json": "create a delta table in unity catalog",
            "authority_weight": 1.0,
        },
    ]

    result = kg_search_dual_substrate(
        query=_QUERY,
        workspace_claims=[],
        workspace_edges=[],
        capability_substrate_claims=capability_claims,
        capability_substrate_edges=[],
        k=10,
    )

    assert result.workspace_refs == ()
    assert len(result.capability_refs) >= 1
    assert result.capability_refs[0].subject == (
        "meta:delta-lake/ext:create-table"
    )
    assert result.mismatch_subjects == ()
    assert result.snapshot_id == "snap-n173-bulk-test"


# ---------------------------------------------------------------------------
# Default authority weight fallback (full-pipeline view).
# ---------------------------------------------------------------------------


def test_capability_seed_without_authority_weight_uses_default(
    monkeypatch,
) -> None:
    """When a capability claim row is missing ``authority_weight`` (e.g.,
    a synthetic test fixture or a row from a legacy snapshot), the
    function MUST apply ``_DEFAULT_CAPABILITY_AUTHORITY`` instead of
    crashing or silently dropping the seed."""

    monkeypatch.setattr(retrieve_mod, "_active_snapshot", _pinned_snapshot)

    capability_claims = [
        {
            "claim_id": "cap-no-weight",
            "subject": "meta:delta-lake/ext:create-table",
            "predicate": "create_table",
            "value_json": "delta",
            "metadata_json": "create a delta table in unity catalog",
            # ← no authority_weight
        },
    ]

    result = kg_search_dual_substrate(
        query=_QUERY,
        workspace_claims=[],
        workspace_edges=[],
        capability_substrate_claims=capability_claims,
        capability_substrate_edges=[],
        k=10,
    )

    assert isinstance(_DEFAULT_CAPABILITY_AUTHORITY, float)
    assert 0.0 < _DEFAULT_CAPABILITY_AUTHORITY <= 1.0
    cap_subjects = {r.subject for r in result.capability_refs}
    assert "meta:delta-lake/ext:create-table" in cap_subjects, (
        "row missing authority_weight was dropped instead of falling "
        "back to _DEFAULT_CAPABILITY_AUTHORITY"
    )


# ---------------------------------------------------------------------------
# Snapshot wiring.
# ---------------------------------------------------------------------------


def test_snapshot_id_carried_through_when_bulk_branch_fires(monkeypatch) -> None:
    """Replay-pin: when the BULK branch fires the active snapshot's
    ``snapshot_id`` MUST be carried through to ``DualSubstrateResult``
    so a replay can re-resolve Stage A retrieval against exactly the
    graph state used at the historical run (the 6th replay pin per
    ``docs/16-identity-audit-replay.md``)."""

    monkeypatch.setattr(retrieve_mod, "_active_snapshot", _pinned_snapshot)

    result = kg_search_dual_substrate(
        query=_QUERY,
        workspace_claims=[
            {
                "claim_id": "ws-1",
                "subject": "uc.catalog.bv_bronze",
                "predicate": "exists",
                "value_json": "true",
                "metadata_json": "workspace catalog",
            },
        ],
        workspace_edges=[],
        capability_substrate_claims=[
            {
                "claim_id": "cap-1",
                "subject": "meta:delta-lake/ext:create-table",
                "predicate": "create_table",
                "value_json": "delta",
                "metadata_json": "delta create table",
                "authority_weight": 1.0,
            },
        ],
        capability_substrate_edges=[],
        k=10,
    )
    assert result.snapshot_id == "snap-n173-bulk-test"


def test_snapshot_id_is_none_in_passthrough_branch() -> None:
    """The C.1 SHELL passthrough branch (no active snapshot) MUST
    return ``snapshot_id=None`` even when ``capability_substrate_claims``
    is non-empty — because without an active snapshot the BULK branch
    can't know which snapshot the caller is referencing."""

    result = kg_search_dual_substrate(
        query=_QUERY,
        workspace_claims=[
            {
                "claim_id": "ws-1",
                "subject": "uc.catalog.bv_bronze",
                "predicate": "exists",
                "value_json": "true",
                "metadata_json": "workspace catalog",
            },
        ],
        workspace_edges=[],
        capability_substrate_claims=[
            {
                "claim_id": "cap-1",
                "subject": "meta:delta-lake/ext:create-table",
                "predicate": "create_table",
                "value_json": "delta",
                "metadata_json": "delta create table",
                "authority_weight": 1.0,
            },
        ],
        capability_substrate_edges=[],
        k=10,
    )
    assert result.snapshot_id is None
    assert result.capability_refs == ()
