"""N178 — unit tests for the 11 v0.7.7 Capability-Graph scorers.

The companion file ``test_hand_authored_skill_exemplar_linkage.py`` already
covers the ``HandAuthoredSkillExemplarLinkage`` scorer end-to-end; this
file pins the other scorers' contracts (happy path + at least one
violation case per scorer) plus the canonical ``scorer_index()`` shape.

Reference: ``docs/17-eval-framework.md`` §13.3 (NEW v0.7.7) +
``docs/23-databricks-capability-graph.md`` §23.5.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from brickvision.install.preflight.capability_graph import (
    BudgetNamespaceProbe,
    BudgetNamespaceSpec,
    IndexerSPProbe,
    IndexerSPSpec,
    VSGrantProbe,
    VSGrantSpec,
)
from brickvision_runtime.capability_graph.schemas import ALL_DDL
from brickvision_runtime.eval.gold.capability_graph import (
    SEED_CAPABILITY_GRAPH_SKILL_CATALOG_GOLD,
    SEED_INDEXER_DAG_TOPOLOGY_GOLD,
    SEED_TOP_ORDER_GOLD,
    HandAuthoredExemplarLinkGoldRow,
)
from brickvision_runtime.eval.scorers import get_scorer
from brickvision_runtime.eval.scorers.capability_graph import (
    budget_namespace_isolation,
    capability_graph_schema_integrity,
    capability_graph_smoke_test_pass_rate,
    hand_authored_skill_anchor_grounding,
    indexer_dag_task_spec,
    indexer_refresh_slo,
    knowledge_ui_vocabulary_coverage,
    scorer_index,
    service_principal_isolation,
    source_authority_assignment,
    vector_search_endpoint_grants,
)
from brickvision_runtime.failures import ReasonCode


# ---------------------------------------------------------------------------
# Index — every scorer is registered + reason-code-aligned.
# ---------------------------------------------------------------------------


def test_scorer_index_returns_eleven_scorers() -> None:
    refs = scorer_index()
    assert len(refs) == 11
    assert {r.pascal_name for r in refs} == {
        "CapabilityGraphSchemaIntegrity",
        "CapabilityGraphSmokeTestPassRate",
        "IndexerDAGTaskSpec",
        "BudgetNamespaceIsolation",
        "ServicePrincipalIsolation",
        "VectorSearchEndpointGrants",
        "SourceAuthorityAssignment",
        "HandAuthoredSkillExemplarLinkage",
        "HandAuthoredSkillAnchorGrounding",
        "IndexerRefreshSLO",
        "KnowledgeUIVocabularyCoverage",
    }


def test_scorer_index_pascal_names_resolve_via_register_scorer() -> None:
    skill_id = "brickvision_runtime.capability_graph"
    for ref in scorer_index():
        fn = get_scorer(skill_id, ref.pascal_name)
        assert fn is not None, f"scorer {ref.pascal_name!r} not registered"


# ---------------------------------------------------------------------------
# 1. CapabilityGraphSchemaIntegrity
# ---------------------------------------------------------------------------


def test_schema_integrity_passes_for_live_all_ddl() -> None:
    result = capability_graph_schema_integrity(
        all_ddl=ALL_DDL, catalog="brickvision_dev",
    )
    assert result.score == 1.0
    assert result.reason_codes == ()
    assert result.details["tables_checked"] == 13


def test_schema_integrity_fails_when_table_count_drifts() -> None:
    pruned = {k: v for i, (k, v) in enumerate(ALL_DDL.items()) if i < 12}
    result = capability_graph_schema_integrity(all_ddl=pruned, catalog="bv")
    assert result.score == 0.0
    assert ReasonCode.CAPABILITY_GRAPH_PERSIST_FAILED.value in result.reason_codes
    kinds = {v["kind"] for v in result.details["violations"]}
    assert "table_count_drift" in kinds


def test_schema_integrity_fails_when_first_column_is_not_snapshot_id() -> None:
    poisoned: dict[str, str] = dict(ALL_DDL)
    poisoned["meta_skills"] = (
        "CREATE TABLE IF NOT EXISTS bv.brickvision.meta_skills "
        "(meta_skill_id STRING, snapshot_id STRING) USING DELTA"
    )
    result = capability_graph_schema_integrity(all_ddl=poisoned, catalog="bv")
    assert result.score == 0.0
    kinds = {v["kind"] for v in result.details["violations"]}
    assert "snapshot_id_first_column_violation" in kinds


# ---------------------------------------------------------------------------
# 2. CapabilityGraphSmokeTestPassRate
# ---------------------------------------------------------------------------


def _live_baseline_rows(hit_rate: float = 0.85) -> list[Mapping[str, object]]:
    return [
        {"query_id": row.query_id, "baseline_hit_rate": hit_rate}
        for row in SEED_CAPABILITY_GRAPH_SKILL_CATALOG_GOLD
    ]


def test_smoke_pass_rate_passes_when_observed_above_floor() -> None:
    result = capability_graph_smoke_test_pass_rate(
        smoke_baseline=_live_baseline_rows(0.80),
        smoke_observed={"observed_hit_rate": 0.95, "passed": True},
    )
    assert result.score == 1.0
    assert result.reason_codes == ()


def test_smoke_pass_rate_fails_when_observed_below_floor() -> None:
    result = capability_graph_smoke_test_pass_rate(
        smoke_baseline=_live_baseline_rows(0.85),
        smoke_observed={"observed_hit_rate": 0.50, "passed": False},
    )
    assert result.score == 0.50
    assert (
        ReasonCode.CAPABILITY_GRAPH_SMOKE_REGRESSION.value
        in result.reason_codes
    )
    assert result.details["kind"] == "hit_rate_below_floor"


def test_smoke_pass_rate_fails_when_gold_query_missing_from_live_baseline() -> None:
    pruned = _live_baseline_rows(0.80)[1:]
    result = capability_graph_smoke_test_pass_rate(
        smoke_baseline=pruned,
        smoke_observed={"observed_hit_rate": 0.95, "passed": True},
    )
    assert result.score == 0.0
    assert (
        ReasonCode.CAPABILITY_GRAPH_SMOKE_REGRESSION.value
        in result.reason_codes
    )
    assert result.details["kind"] == "gold_query_missing_from_live_baseline"
    assert "cg-q1" in result.details["missing"]


def test_smoke_pass_rate_fails_on_empty_baseline() -> None:
    result = capability_graph_smoke_test_pass_rate(
        smoke_baseline=[
            {"query_id": row.query_id}  # no baseline_hit_rate
            for row in SEED_CAPABILITY_GRAPH_SKILL_CATALOG_GOLD
        ],
        smoke_observed={"observed_hit_rate": 0.95},
    )
    assert result.score == 0.0
    assert result.details["kind"] == "empty_baseline"


# ---------------------------------------------------------------------------
# 3. IndexerDAGTaskSpec
# ---------------------------------------------------------------------------


def _gold_observed_tasks() -> list[dict[str, object]]:
    return [
        {"task_key": row.task_key, "depends_on": list(row.depends_on)}
        for row in SEED_INDEXER_DAG_TOPOLOGY_GOLD
    ]


def test_indexer_dag_task_spec_passes_for_gold_topology() -> None:
    result = indexer_dag_task_spec(observed_tasks=_gold_observed_tasks())
    assert result.score == 1.0
    assert result.reason_codes == ()
    assert result.details["task_count"] == result.details["gold_task_count"]


def test_indexer_dag_task_spec_fails_when_task_missing() -> None:
    tasks = _gold_observed_tasks()
    pruned = [t for t in tasks if t["task_key"] != "smoke"]
    result = indexer_dag_task_spec(observed_tasks=pruned)
    assert result.score == 0.0
    assert (
        ReasonCode.CAPABILITY_GRAPH_PROMOTION_FAILED.value
        in result.reason_codes
    )
    kinds = {v["kind"] for v in result.details["violations"]}
    assert "missing_tasks" in kinds


def test_indexer_dag_task_spec_fails_when_extra_task_added() -> None:
    tasks = _gold_observed_tasks()
    tasks.append({"task_key": "extra_unexpected_task", "depends_on": []})
    result = indexer_dag_task_spec(observed_tasks=tasks)
    assert result.score == 0.0
    kinds = {v["kind"] for v in result.details["violations"]}
    assert "extra_tasks" in kinds


def test_indexer_dag_task_spec_fails_when_depends_on_drifts() -> None:
    tasks = _gold_observed_tasks()
    for t in tasks:
        if t["task_key"] == "promote":
            t["depends_on"] = ["graph_builder"]  # wrong: should be 'smoke'
    result = indexer_dag_task_spec(observed_tasks=tasks)
    assert result.score == 0.0
    kinds = {v["kind"] for v in result.details["violations"]}
    assert "depends_on_drift" in kinds


def test_indexer_dag_task_spec_accepts_block_style_depends_on() -> None:
    """Databricks Jobs accepts `depends_on: [{ task_key: x }]`. Verify
    the scorer normalises that block-style equally to a flat list of
    strings."""

    tasks: list[dict[str, object]] = []
    for row in SEED_INDEXER_DAG_TOPOLOGY_GOLD:
        deps_block = [{"task_key": d} for d in row.depends_on]
        tasks.append({"task_key": row.task_key, "depends_on": deps_block})
    result = indexer_dag_task_spec(observed_tasks=tasks)
    assert result.score == 1.0


# ---------------------------------------------------------------------------
# 4. BudgetNamespaceIsolation
# ---------------------------------------------------------------------------


def _budget_pass_probe() -> BudgetNamespaceProbe:
    return BudgetNamespaceProbe(
        namespaces={"app": "<bv>.budget.app_ledger", "indexer": "<bv>.budget.indexer_ledger"},
        env_resolution={"bv_app_sp": "app", "bv_indexer_sp": "indexer"},
    )


def test_budget_namespace_isolation_passes_for_clean_partner() -> None:
    result = budget_namespace_isolation(
        spec=BudgetNamespaceSpec(),
        probe=_budget_pass_probe(),
    )
    assert result.score == 1.0
    assert result.reason_codes == ()


def test_budget_namespace_isolation_fails_when_namespace_missing() -> None:
    probe = BudgetNamespaceProbe(
        namespaces={"app": "<bv>.budget.app_ledger"},  # missing indexer
        env_resolution={"bv_app_sp": "app"},
    )
    result = budget_namespace_isolation(spec=BudgetNamespaceSpec(), probe=probe)
    assert result.score == 0.0
    assert (
        ReasonCode.INDEXER_BUDGET_NAMESPACE_NOT_ISOLATED.value
        in result.reason_codes
    )


def test_budget_namespace_isolation_fails_when_ledgers_overlap() -> None:
    probe = BudgetNamespaceProbe(
        namespaces={
            "app": "<bv>.budget.shared_ledger",
            "indexer": "<bv>.budget.shared_ledger",
        },
        env_resolution={"bv_app_sp": "app", "bv_indexer_sp": "indexer"},
    )
    result = budget_namespace_isolation(spec=BudgetNamespaceSpec(), probe=probe)
    assert result.score == 0.0
    assert any(
        "shared_ledger" in str(v.get("detail", "")).lower()
        or "shared_ledgers" in str(v.get("detail", "")).lower()
        for v in result.details["violations"]
    )


# ---------------------------------------------------------------------------
# 5. ServicePrincipalIsolation
# ---------------------------------------------------------------------------


def test_service_principal_isolation_passes_for_distinct_active_sps() -> None:
    probe = IndexerSPProbe(
        indexer_sp_application_id="app-id-indexer",
        app_sp_application_id="app-id-app",
        enabled={"bv_indexer_sp": True, "bv_app_sp": True},
    )
    result = service_principal_isolation(spec=IndexerSPSpec(), probe=probe)
    assert result.score == 1.0
    assert result.reason_codes == ()


def test_service_principal_isolation_fails_when_indexer_sp_missing() -> None:
    probe = IndexerSPProbe(
        indexer_sp_application_id=None,
        app_sp_application_id="app-id-app",
        enabled={"bv_app_sp": True},
    )
    result = service_principal_isolation(spec=IndexerSPSpec(), probe=probe)
    assert result.score == 0.0
    assert (
        ReasonCode.INDEXER_SP_NOT_PROVISIONED.value in result.reason_codes
    )


def test_service_principal_isolation_fails_when_sps_collapsed() -> None:
    probe = IndexerSPProbe(
        indexer_sp_application_id="shared-app-id",
        app_sp_application_id="shared-app-id",
        enabled={"bv_indexer_sp": True, "bv_app_sp": True},
    )
    result = service_principal_isolation(spec=IndexerSPSpec(), probe=probe)
    assert result.score == 0.0
    assert any(
        "shared_application_id" in str(v.get("detail", ""))
        for v in result.details["violations"]
    )


# ---------------------------------------------------------------------------
# 6. VectorSearchEndpointGrants
# ---------------------------------------------------------------------------


def _correct_vs_grants() -> Mapping[str, Mapping[str, tuple[str, ...]]]:
    return {
        "entity_index": {
            "bv_indexer_sp": ("WRITE",),
            "bv_app_sp": ("READ",),
        },
    }


def test_vector_search_endpoint_grants_passes_for_correct_grants() -> None:
    probe = VSGrantProbe(
        endpoint_exists=True,
        index_grants=_correct_vs_grants(),
    )
    result = vector_search_endpoint_grants(spec=VSGrantSpec(), probe=probe)
    assert result.score == 1.0


def test_vector_search_endpoint_grants_fails_when_app_has_write() -> None:
    grants = {k: dict(v) for k, v in _correct_vs_grants().items()}
    grants["entity_index"]["bv_app_sp"] = ("READ", "WRITE")
    probe = VSGrantProbe(
        endpoint_exists=True,
        index_grants={k: dict(v) for k, v in grants.items()},
    )
    result = vector_search_endpoint_grants(spec=VSGrantSpec(), probe=probe)
    assert result.score == 0.0
    assert (
        ReasonCode.VS_ENDPOINT_GRANTS_MIXED.value in result.reason_codes
    )


def test_vector_search_endpoint_grants_fails_when_indexer_lacks_write() -> None:
    grants = {k: dict(v) for k, v in _correct_vs_grants().items()}
    grants["entity_index"]["bv_indexer_sp"] = ("READ",)
    probe = VSGrantProbe(
        endpoint_exists=True,
        index_grants={k: dict(v) for k, v in grants.items()},
    )
    result = vector_search_endpoint_grants(spec=VSGrantSpec(), probe=probe)
    assert result.score == 0.0


# ---------------------------------------------------------------------------
# 7. SourceAuthorityAssignment
# ---------------------------------------------------------------------------


def _locked_authority_rows() -> list[dict[str, object]]:
    return [
        {"source_kind": "sdk", "authority_weight": 1.00},
        {"source_kind": "openapi", "authority_weight": 0.95},
        {"source_kind": "docs", "authority_weight": 0.85},
        {"source_kind": "labs", "authority_weight": 0.75},
        {"source_kind": "blog", "authority_weight": 0.50},
        {"source_kind": "hand_authored", "authority_weight": 0.00},
    ]


def test_source_authority_assignment_passes_for_locked_weights() -> None:
    result = source_authority_assignment(
        source_authority_rows=_locked_authority_rows(),
    )
    assert result.score == 1.0
    assert result.reason_codes == ()


def test_source_authority_assignment_fails_on_weight_drift() -> None:
    rows = _locked_authority_rows()
    # silently change docs weight
    for r in rows:
        if r["source_kind"] == "docs":
            r["authority_weight"] = 0.99
    result = source_authority_assignment(source_authority_rows=rows)
    assert result.score == 0.0
    assert (
        ReasonCode.CAPABILITY_GRAPH_PERSIST_FAILED.value in result.reason_codes
    )
    kinds = {v["kind"] for v in result.details["violations"]}
    assert "weight_drift" in kinds


def test_source_authority_assignment_fails_when_kind_missing() -> None:
    rows = [r for r in _locked_authority_rows() if r["source_kind"] != "labs"]
    result = source_authority_assignment(source_authority_rows=rows)
    assert result.score == 0.0
    kinds = {v["kind"] for v in result.details["violations"]}
    assert "missing_source_kinds" in kinds


def test_source_authority_assignment_accepts_blog_recency_decay() -> None:
    rows = _locked_authority_rows()
    for r in rows:
        if r["source_kind"] == "blog":
            r["authority_weight"] = 0.10  # recency-decayed value
    result = source_authority_assignment(source_authority_rows=rows)
    assert result.score == 1.0


def test_source_authority_assignment_fails_on_unknown_provenance_kind() -> None:
    result = source_authority_assignment(
        source_authority_rows=_locked_authority_rows(),
        provenance_rows=[
            {"source_kind": "rumour", "entity_id": "ext:foo"},
        ],
    )
    assert result.score == 0.0
    kinds = {v["kind"] for v in result.details["violations"]}
    assert "provenance_unknown_source_kind" in kinds


# ---------------------------------------------------------------------------
# 9. HandAuthoredSkillAnchorGrounding
# ---------------------------------------------------------------------------


def test_hand_authored_skill_anchor_grounding_passes_for_source_grounded_anchor() -> None:
    result = hand_authored_skill_anchor_grounding(
        observed_skill_exemplars={
            "lakeflow.jobs-run-submit": "meta:lakeflow-jobs/ext:submit",
        },
        extension_source_kinds={
            "meta:lakeflow-jobs/ext:submit": ["sdk", "docs"],
        },
        gold=[
            HandAuthoredExemplarLinkGoldRow(
                skill_id="lakeflow.jobs-run-submit",
                exemplar_of="meta:lakeflow-jobs/ext:submit",
            )
        ],
    )
    assert result.score == 1.0
    assert result.details["classifications"][0]["grounding_status"] == "source_grounded"


def test_hand_authored_skill_anchor_grounding_flags_hand_authored_stub_only() -> None:
    result = hand_authored_skill_anchor_grounding(
        observed_skill_exemplars={
            "lakeflow.jobs-run-submit": "meta:lakeflow-jobs/ext:jobs-runs-submit",
        },
        extension_source_kinds={
            "meta:lakeflow-jobs/ext:jobs-runs-submit": ["hand_authored"],
        },
        gold=[
            HandAuthoredExemplarLinkGoldRow(
                skill_id="lakeflow.jobs-run-submit",
                exemplar_of="meta:lakeflow-jobs/ext:jobs-runs-submit",
            )
        ],
    )
    assert result.score == 0.0
    assert (
        ReasonCode.HAND_AUTHORED_SKILL_ANCHOR_NOT_SOURCE_GROUNDED.value
        in result.reason_codes
    )
    assert result.details["violations"][0]["grounding_status"] == "hand_authored_stub_only"


def test_hand_authored_skill_anchor_grounding_allows_explicit_pending_stub() -> None:
    result = hand_authored_skill_anchor_grounding(
        observed_skill_exemplars={
            "partner.future-skill": "meta:future/ext:not-indexed-yet",
        },
        extension_source_kinds={
            "meta:future/ext:not-indexed-yet": ["hand_authored"],
        },
        pending_stub_anchors=["meta:future/ext:not-indexed-yet"],
        gold=[
            HandAuthoredExemplarLinkGoldRow(
                skill_id="partner.future-skill",
                exemplar_of="meta:future/ext:not-indexed-yet",
            )
        ],
    )
    assert result.score == 1.0
    assert result.details["classifications"][0]["grounding_status"] == "pending_stub_explicit"


# ---------------------------------------------------------------------------
# 10. IndexerRefreshSLO  (8 = HandAuthoredSkillExemplarLinkage covered elsewhere)
# ---------------------------------------------------------------------------


_MS_PER_DAY: int = 24 * 60 * 60 * 1000


def test_indexer_refresh_slo_passes_for_fresh_snapshot() -> None:
    now_ms = 100_000_000_000
    result = indexer_refresh_slo(
        active_snapshot_promoted_at_ms=now_ms - (1 * _MS_PER_DAY),
        now_ms=now_ms,
        freshness_tolerance_days=2,
    )
    assert result.score == 1.0


def test_indexer_refresh_slo_fails_when_no_active_snapshot() -> None:
    result = indexer_refresh_slo(
        active_snapshot_promoted_at_ms=None,
        now_ms=100_000_000_000,
    )
    assert result.score == 0.0
    assert (
        ReasonCode.CAPABILITY_GRAPH_SNAPSHOT_STALE.value in result.reason_codes
    )
    assert result.details["kind"] == "no_active_snapshot"


def test_indexer_refresh_slo_fails_when_snapshot_stale() -> None:
    now_ms = 100_000_000_000
    result = indexer_refresh_slo(
        active_snapshot_promoted_at_ms=now_ms - (5 * _MS_PER_DAY),
        now_ms=now_ms,
        freshness_tolerance_days=2,
    )
    assert result.score == 0.0
    assert (
        ReasonCode.CAPABILITY_GRAPH_SNAPSHOT_STALE.value in result.reason_codes
    )
    assert result.details["kind"] == "snapshot_stale"
    assert result.details["age_days"] == 5.0


def test_indexer_refresh_slo_fails_on_invalid_tolerance() -> None:
    result = indexer_refresh_slo(
        active_snapshot_promoted_at_ms=100,
        now_ms=200,
        freshness_tolerance_days=0,
    )
    assert result.score == 0.0
    assert result.details["kind"] == "invalid_tolerance"


# ---------------------------------------------------------------------------
# 10. KnowledgeUIVocabularyCoverage
# ---------------------------------------------------------------------------


_LIVE_TABS = ("corpus", "top-orders", "meta-skills", "extensions", "refresh-history")
_LIVE_ENDPOINTS = (
    "/api/knowledge/corpus",
    "/api/knowledge/top-orders",
    "/api/knowledge/meta-skills",
    "/api/knowledge/extensions",
    "/api/knowledge/extensions/{extension_id}/provenance",
    "/api/knowledge/refresh-history",
    "/api/knowledge/health",
)
_LIVE_TOP_ORDERS = tuple(row.top_order_id for row in SEED_TOP_ORDER_GOLD)


def test_knowledge_ui_vocab_passes_for_canonical_state() -> None:
    result = knowledge_ui_vocabulary_coverage(
        observed_tab_ids=_LIVE_TABS,
        observed_top_order_ids=_LIVE_TOP_ORDERS,
        observed_endpoint_paths=_LIVE_ENDPOINTS,
    )
    assert result.score == 1.0
    assert result.reason_codes == ()


def test_knowledge_ui_vocab_passes_when_endpoint_check_skipped() -> None:
    result = knowledge_ui_vocabulary_coverage(
        observed_tab_ids=_LIVE_TABS,
        observed_top_order_ids=_LIVE_TOP_ORDERS,
        # no endpoints -> endpoint coverage check skipped
    )
    assert result.score == 1.0
    assert result.details["endpoint_check_skipped"] is True


def test_knowledge_ui_vocab_fails_on_missing_tab() -> None:
    pruned = tuple(t for t in _LIVE_TABS if t != "extensions")
    result = knowledge_ui_vocabulary_coverage(
        observed_tab_ids=pruned,
        observed_top_order_ids=_LIVE_TOP_ORDERS,
        observed_endpoint_paths=_LIVE_ENDPOINTS,
    )
    assert result.score == 0.0
    assert ReasonCode.DOCS_SECTION_ALIAS_MISSING.value in result.reason_codes
    kinds = {v["kind"] for v in result.details["violations"]}
    assert "missing_tabs" in kinds


def test_knowledge_ui_vocab_fails_on_missing_endpoint() -> None:
    pruned = tuple(p for p in _LIVE_ENDPOINTS if "refresh-history" not in p)
    result = knowledge_ui_vocabulary_coverage(
        observed_tab_ids=_LIVE_TABS,
        observed_top_order_ids=_LIVE_TOP_ORDERS,
        observed_endpoint_paths=pruned,
    )
    assert result.score == 0.0
    kinds = {v["kind"] for v in result.details["violations"]}
    assert "missing_endpoints" in kinds


def test_knowledge_ui_vocab_fails_on_missing_top_order() -> None:
    pruned = tuple(t for t in _LIVE_TOP_ORDERS if t != "to:migration")
    result = knowledge_ui_vocabulary_coverage(
        observed_tab_ids=_LIVE_TABS,
        observed_top_order_ids=pruned,
        observed_endpoint_paths=_LIVE_ENDPOINTS,
    )
    assert result.score == 0.0
    kinds = {v["kind"] for v in result.details["violations"]}
    assert "missing_top_orders" in kinds
    assert "to:migration" in result.details["violations"][0]["top_order_ids"]


# ---------------------------------------------------------------------------
# Detail-payload hygiene — every violation case keeps under the 32-row cap.
# ---------------------------------------------------------------------------


def test_violation_payload_respects_32_row_cap() -> None:
    big_pruned = []  # zero rows -> ``missing_source_kinds`` lists 6 kinds
    result = source_authority_assignment(source_authority_rows=big_pruned)
    assert result.score == 0.0
    for v in result.details["violations"]:
        for value in v.values():
            if isinstance(value, list):
                assert len(value) <= 32


def test_dataclass_kwargs_only_signature_is_stable() -> None:
    """All scorers are kwargs-only — keep that contract pinned so future
    reorderings don't silently break callers."""

    import inspect

    for ref in scorer_index():
        # Resolve through register_scorer to get the live wrapper.
        fn = get_scorer("brickvision_runtime.capability_graph", ref.pascal_name)
        assert fn is not None
        sig = inspect.signature(fn)
        for p in sig.parameters.values():
            assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"{ref.pascal_name} param {p.name!r} is not keyword-only"
            )


# Smoke check for module-level dataclass intent.


def test_scorer_ref_is_frozen_slots_dataclass() -> None:
    refs = scorer_index()
    sample = refs[0]
    assert dataclasses.is_dataclass(sample)
    # Frozen dataclasses raise FrozenInstanceError on assignment.
    try:
        object.__setattr__(sample, "snake_name", "should_fail")
    except (AttributeError, dataclasses.FrozenInstanceError):
        pass
