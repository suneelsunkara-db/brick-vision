"""Canonical ReasonCode enum + Question typed-failure record (v0.7.7 MVI).

Every failure path is a typed reason code. Adding a new code requires
adding the matching default-action row to ``_DEFAULT_ACTIONS``.

v0.7.7 narrowed the surface to the load-bearing capability-graph
indexer + install pre-flight + discipline codes. The build-pipeline +
workspace-KG + visual-builder reason codes were retired alongside their
producers.
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from enum import Enum


@dataclasses.dataclass(frozen=True)
class Question:
    """Typed failure record (v0.7.7 collapsed surface)."""

    question_id: str
    subject: str
    text: str
    suggested_next_action: str
    raised_by: str
    reason_code: str
    metadata: dict
    raised_at_ms: int

    @classmethod
    def open(
        cls,
        *,
        subject: str,
        text: str,
        suggested_next_action: str,
        raised_by: str,
        reason_code: str,
        metadata: dict | None = None,
    ) -> "Question":
        return cls(
            question_id=f"q_{uuid.uuid4().hex[:12]}",
            subject=subject,
            text=text,
            suggested_next_action=suggested_next_action,
            raised_by=raised_by,
            reason_code=reason_code,
            metadata=dict(metadata or {}),
            raised_at_ms=int(time.time() * 1000),
        )


class ReasonCode(str, Enum):
    # Cross-cutting (model routing + budget + workspace).
    MODEL_ROLE_NOT_RESOLVED = "MODEL_ROLE_NOT_RESOLVED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    WORKSPACE_CATALOG_BINDING_MISSING = "WORKSPACE_CATALOG_BINDING_MISSING"
    WRITE_TARGET_CATALOG_NOT_BOUND_RW = "WRITE_TARGET_CATALOG_NOT_BOUND_RW"

    # Install pre-flights (docs/19-local-development.md §15.5).
    PYTHON_VERSION_TOO_OLD = "PYTHON_VERSION_TOO_OLD"
    DATABRICKS_SDK_VERSION_TOO_OLD = "DATABRICKS_SDK_VERSION_TOO_OLD"
    SERVERLESS_ENV_VERSION_INCOMPATIBLE = "SERVERLESS_ENV_VERSION_INCOMPATIBLE"
    VS_OUT_OF_BAND_PROVISIONING_REQUIRED = "VS_OUT_OF_BAND_PROVISIONING_REQUIRED"
    VS_RESOURCE_SCHEMA_MISMATCH = "VS_RESOURCE_SCHEMA_MISMATCH"
    INDEXER_SP_NOT_PROVISIONED = "INDEXER_SP_NOT_PROVISIONED"
    INDEXER_BUDGET_NAMESPACE_NOT_ISOLATED = "INDEXER_BUDGET_NAMESPACE_NOT_ISOLATED"
    UC_SCHEMA_CAPABILITY_GRAPH_GRANTS_INVALID = "UC_SCHEMA_CAPABILITY_GRAPH_GRANTS_INVALID"
    VS_ENDPOINT_GRANTS_MIXED = "VS_ENDPOINT_GRANTS_MIXED"

    # Capability-graph indexer (docs/23-databricks-capability-graph.md).
    # Source adapters (sdk / openapi / docs / blog / labs).
    CAPABILITY_GRAPH_SDK_PARSE_FAILED = "CAPABILITY_GRAPH_SDK_PARSE_FAILED"
    CAPABILITY_GRAPH_OPENAPI_FETCH_FAILED = "CAPABILITY_GRAPH_OPENAPI_FETCH_FAILED"
    CAPABILITY_GRAPH_OPENAPI_SDK_LINK_MISSING = "CAPABILITY_GRAPH_OPENAPI_SDK_LINK_MISSING"
    CAPABILITY_GRAPH_DOCS_FETCH_FAILED = "CAPABILITY_GRAPH_DOCS_FETCH_FAILED"
    CAPABILITY_GRAPH_DOCS_PARSE_FAILED = "CAPABILITY_GRAPH_DOCS_PARSE_FAILED"
    CAPABILITY_GRAPH_DOCS_CORPUS_PARTIAL = "CAPABILITY_GRAPH_DOCS_CORPUS_PARTIAL"
    CAPABILITY_GRAPH_BLOG_FETCH_FAILED = "CAPABILITY_GRAPH_BLOG_FETCH_FAILED"
    CAPABILITY_GRAPH_BLOG_PARSE_FAILED = "CAPABILITY_GRAPH_BLOG_PARSE_FAILED"
    CAPABILITY_GRAPH_BLOG_FILTER_REJECTED_HIGH_VOLUME = (
        "CAPABILITY_GRAPH_BLOG_FILTER_REJECTED_HIGH_VOLUME"
    )
    CAPABILITY_GRAPH_LABS_FETCH_FAILED = "CAPABILITY_GRAPH_LABS_FETCH_FAILED"
    CAPABILITY_GRAPH_LABS_PARSE_FAILED = "CAPABILITY_GRAPH_LABS_PARSE_FAILED"
    CAPABILITY_GRAPH_LABS_PIP_INSTALL_FAILED = "CAPABILITY_GRAPH_LABS_PIP_INSTALL_FAILED"
    CAPABILITY_GRAPH_LABS_MODULE_UNKNOWN = "CAPABILITY_GRAPH_LABS_MODULE_UNKNOWN"
    # Extraction + classification.
    CAPABILITY_GRAPH_EXTRACTION_RATE_FAILED = "CAPABILITY_GRAPH_EXTRACTION_RATE_FAILED"
    CAPABILITY_GRAPH_EFFECT_CLASS_UNKNOWN = "CAPABILITY_GRAPH_EFFECT_CLASS_UNKNOWN"
    CAPABILITY_GRAPH_ORPHANED_META_SKILL = "CAPABILITY_GRAPH_ORPHANED_META_SKILL"
    BLOG_META_SKILL_INFERENCE_FAILED = "BLOG_META_SKILL_INFERENCE_FAILED"
    DOCS_SECTION_ALIAS_MISSING = "DOCS_SECTION_ALIAS_MISSING"
    MENTION_PRECISION_BELOW_FLOOR = "MENTION_PRECISION_BELOW_FLOOR"
    # Embedding + persist.
    CAPABILITY_GRAPH_EMBEDDING_SHARD_FAILED = "CAPABILITY_GRAPH_EMBEDDING_SHARD_FAILED"
    CAPABILITY_GRAPH_EMBEDDING_BUDGET_EXCEEDED = "CAPABILITY_GRAPH_EMBEDDING_BUDGET_EXCEEDED"
    CAPABILITY_GRAPH_EMBEDDING_TOKEN_CAP_EXCEEDED = "CAPABILITY_GRAPH_EMBEDDING_TOKEN_CAP_EXCEEDED"
    CAPABILITY_GRAPH_EMBEDDING_ENDPOINT_ERROR = "CAPABILITY_GRAPH_EMBEDDING_ENDPOINT_ERROR"
    CAPABILITY_GRAPH_PERSIST_FAILED = "CAPABILITY_GRAPH_PERSIST_FAILED"
    CAPABILITY_GRAPH_PERSIST_WRITE_FAILED = "CAPABILITY_GRAPH_PERSIST_WRITE_FAILED"
    # Vector Search sync.
    CAPABILITY_GRAPH_VS_SYNC_FAILED = "CAPABILITY_GRAPH_VS_SYNC_FAILED"
    CAPABILITY_GRAPH_VS_UPSERT_FAILED = "CAPABILITY_GRAPH_VS_UPSERT_FAILED"
    CAPABILITY_GRAPH_VS_INDEX_ENDPOINT_DOWN = "CAPABILITY_GRAPH_VS_INDEX_ENDPOINT_DOWN"
    # Smoke + promote.
    CAPABILITY_GRAPH_SMOKE_FAILED = "CAPABILITY_GRAPH_SMOKE_FAILED"
    CAPABILITY_GRAPH_SMOKE_BASELINE_EMPTY = "CAPABILITY_GRAPH_SMOKE_BASELINE_EMPTY"
    CAPABILITY_GRAPH_SMOKE_REGRESSION = "CAPABILITY_GRAPH_SMOKE_REGRESSION"
    CAPABILITY_GRAPH_PROMOTION_FAILED = "CAPABILITY_GRAPH_PROMOTION_FAILED"
    CAPABILITY_GRAPH_PROMOTE_GATE_FAILED = "CAPABILITY_GRAPH_PROMOTE_GATE_FAILED"
    CAPABILITY_GRAPH_PROMOTE_ALREADY_PROMOTED = "CAPABILITY_GRAPH_PROMOTE_ALREADY_PROMOTED"
    CAPABILITY_GRAPH_PROMOTE_WRITE_FAILED = "CAPABILITY_GRAPH_PROMOTE_WRITE_FAILED"
    CAPABILITY_GRAPH_PROMOTION_HITL_REQUIRED = "CAPABILITY_GRAPH_PROMOTION_HITL_REQUIRED"
    # Rollback + retention.
    CAPABILITY_GRAPH_MANUAL_ROLLBACK = "CAPABILITY_GRAPH_MANUAL_ROLLBACK"
    CAPABILITY_GRAPH_ROLLBACK_RATE_LIMITED = "CAPABILITY_GRAPH_ROLLBACK_RATE_LIMITED"
    CAPABILITY_GRAPH_SNAPSHOT_OUT_OF_RETENTION = "CAPABILITY_GRAPH_SNAPSHOT_OUT_OF_RETENTION"
    CAPABILITY_GRAPH_SNAPSHOT_STALE = "CAPABILITY_GRAPH_SNAPSHOT_STALE"
    CAPABILITY_GRAPH_RETENTION_DEACTIVATE_FAILED = "CAPABILITY_GRAPH_RETENTION_DEACTIVATE_FAILED"
    CAPABILITY_GRAPH_RETENTION_CACHE_GC_FAILED = "CAPABILITY_GRAPH_RETENTION_CACHE_GC_FAILED"
    # Cross-cutting indexer.
    CAPABILITY_GRAPH_TOKEN_CAP_EXCEEDED = "CAPABILITY_GRAPH_TOKEN_CAP_EXCEEDED"
    CAPABILITY_GRAPH_TELEMETRY_FAILED = "CAPABILITY_GRAPH_TELEMETRY_FAILED"
    CAPABILITY_WORKSPACE_MISMATCH = "CAPABILITY_WORKSPACE_MISMATCH"
    HAND_AUTHORED_SKILL_MISSING_EXEMPLAR_OF = "HAND_AUTHORED_SKILL_MISSING_EXEMPLAR_OF"
    HAND_AUTHORED_SKILL_ANCHOR_NOT_SOURCE_GROUNDED = (
        "HAND_AUTHORED_SKILL_ANCHOR_NOT_SOURCE_GROUNDED"
    )

    # Knowledge UI retrieval.
    KG_SEARCH_HYDRATION_TRUNCATED = "KG_SEARCH_HYDRATION_TRUNCATED"

    # Discipline (production-only code; rule 15).
    MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE = "MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE"
    PROTOCOL_HAS_ONLY_MOCK_SUBCLASSES = "PROTOCOL_HAS_ONLY_MOCK_SUBCLASSES"


def question_from_failure(
    *,
    reason: ReasonCode,
    subject: str,
    raised_by: str,
    details: dict | None = None,
    suggested_next_action: str | None = None,
) -> "Question":
    """Build a typed Question from a failure reason code."""

    return Question.open(
        subject=subject,
        text=f"{reason.value}: see suggested_next_action.",
        suggested_next_action=suggested_next_action or _default_action(reason),
        raised_by=raised_by,
        reason_code=reason.value,
        metadata=details or {},
    )


_DEFAULT_ACTIONS: dict[ReasonCode, str] = {
    ReasonCode.MODEL_ROLE_NOT_RESOLVED: (
        "set LLM_GENERAL_TASKS or LLM_EMBEDDING_TASKS in .env"
    ),
    ReasonCode.BUDGET_EXCEEDED: (
        "increase the budget cap in <BV_CATALOG>.<BV_SCHEMA>.budget_namespaces"
        " or split the indexer refresh into smaller passes"
    ),
    ReasonCode.WORKSPACE_CATALOG_BINDING_MISSING: (
        "bind the BrickVision catalog READ_WRITE to the executing workspace via UC"
        " Workspace-Catalog Bindings"
    ),
    ReasonCode.WRITE_TARGET_CATALOG_NOT_BOUND_RW: (
        "bind target catalog to the executing workspace via UC Workspace-Catalog Bindings"
    ),
    ReasonCode.PYTHON_VERSION_TOO_OLD: (
        "upgrade local Python to >=3.11 (required by serverless Jobs runtime)"
    ),
    ReasonCode.DATABRICKS_SDK_VERSION_TOO_OLD: (
        "pip install --upgrade 'databricks-sdk>=0.68'"
    ),
    ReasonCode.SERVERLESS_ENV_VERSION_INCOMPATIBLE: (
        "set serverless_env_version=2 in databricks.yml; v1 environments are deprecated"
    ),
    ReasonCode.VS_OUT_OF_BAND_PROVISIONING_REQUIRED: (
        "create the Vector Search endpoint via 'databricks vector-search endpoints"
        " create' before running 'brickvision install'"
    ),
    ReasonCode.VS_RESOURCE_SCHEMA_MISMATCH: (
        "drop and re-create the VS index with the schema declared in"
        " brickvision_runtime.capability_graph.schemas.vs_index_specs"
    ),
    ReasonCode.INDEXER_SP_NOT_PROVISIONED: (
        "create the bv_indexer_sp service principal (distinct from bv_app_sp);"
        " CLI: `databricks service-principals create --display-name bv_indexer_sp`"
    ),
    ReasonCode.INDEXER_BUDGET_NAMESPACE_NOT_ISOLATED: (
        "split <BV_CATALOG>.<BV_SCHEMA>.budget_namespaces into 'app' and 'indexer' rows"
        " with non-overlapping ledger tables"
    ),
    ReasonCode.UC_SCHEMA_CAPABILITY_GRAPH_GRANTS_INVALID: (
        "ensure capability-graph schema OWNER is bv_indexer_sp and bv_app_sp has"
        " SELECT-only (no MODIFY/CREATE) on the schema"
    ),
    ReasonCode.VS_ENDPOINT_GRANTS_MIXED: (
        "grant bv_indexer_sp WRITE on the 3 capability-graph VS indexes and"
        " bv_app_sp READ-only on the same"
    ),
    ReasonCode.CAPABILITY_GRAPH_SDK_PARSE_FAILED: (
        "downgrade databricks-sdk to a known-good version OR file an issue against"
        " databricks-sdk-py upstream and extend the BV adapter's grammar coverage"
    ),
    ReasonCode.CAPABILITY_GRAPH_OPENAPI_FETCH_FAILED: (
        "soft-fail; inspect rate-limit / network egress; next refresh re-attempts"
    ),
    ReasonCode.CAPABILITY_GRAPH_OPENAPI_SDK_LINK_MISSING: (
        "either add the missing __databricks_path__ class-attr to the SDK service"
        " class OR accept the link gap (the operation is OpenAPI-only)"
    ),
    ReasonCode.CAPABILITY_GRAPH_DOCS_FETCH_FAILED: (
        "soft-fail; inspect docs.databricks.com / learn.microsoft.com sitemap"
        " availability; next refresh re-attempts"
    ),
    ReasonCode.CAPABILITY_GRAPH_DOCS_PARSE_FAILED: (
        "inspect the failing docs URL in the Question's evidence span; if it's"
        " a transient render issue, the next nightly refresh retries"
    ),
    ReasonCode.CAPABILITY_GRAPH_DOCS_CORPUS_PARTIAL: (
        "inspect upstream sitemap shape change; the affected corpus is excluded from"
        " the snapshot until the next successful refresh"
    ),
    ReasonCode.CAPABILITY_GRAPH_BLOG_FETCH_FAILED: (
        "rate-limit drift on databricks.com/blog; per-URL retried 3x then skipped"
    ),
    ReasonCode.CAPABILITY_GRAPH_BLOG_PARSE_FAILED: (
        "the blog post HTML structure changed; inspect the failing URL"
    ),
    ReasonCode.CAPABILITY_GRAPH_BLOG_FILTER_REJECTED_HIGH_VOLUME: (
        "the allowlist + LLM-scorer filter dropped >80% of crawled posts;"
        " review the default allowlist for drift"
    ),
    ReasonCode.CAPABILITY_GRAPH_LABS_FETCH_FAILED: (
        "soft-fail; inspect PyPI rate-limit / network egress for databricks-labs-*"
    ),
    ReasonCode.CAPABILITY_GRAPH_LABS_PARSE_FAILED: (
        "the labs module structure changed; inspect the failing module"
    ),
    ReasonCode.CAPABILITY_GRAPH_LABS_PIP_INSTALL_FAILED: (
        "soft-fail; inspect PyPI rate-limit / network egress"
    ),
    ReasonCode.CAPABILITY_GRAPH_LABS_MODULE_UNKNOWN: (
        "Lakebridge added a new top-level module not in the mapping table;"
        " review and add a row to the table in the lead doc"
    ),
    ReasonCode.CAPABILITY_GRAPH_EXTRACTION_RATE_FAILED: (
        "inspect sample failures; consider an extractor prompt update;"
        " failed extraction must be re-run before next promotion"
    ),
    ReasonCode.CAPABILITY_GRAPH_EFFECT_CLASS_UNKNOWN: (
        "review the unclassified method name in the Question's evidence span;"
        " add the verb stem to _READ_VERBS or _WRITE_VERBS in sdk_adapter.py if it generalizes"
    ),
    ReasonCode.CAPABILITY_GRAPH_ORPHANED_META_SKILL: (
        "inspect <BV_CATALOG>.<BV_SCHEMA>.docs_section_aliases for missing alias rows"
    ),
    ReasonCode.BLOG_META_SKILL_INFERENCE_FAILED: (
        "the LLM scorer produced an inferred meta-skill that didn't match any"
        " existing meta_skill node; either add the meta-skill or refine the prompt"
    ),
    ReasonCode.DOCS_SECTION_ALIAS_MISSING: (
        "a new docs section root exists in the sitemap but isn't in the"
        " <BV_CATALOG>.<BV_SCHEMA>.docs_section_aliases table; add a row + bump schema_version"
    ),
    ReasonCode.MENTION_PRECISION_BELOW_FLOOR: (
        "kg_extractor returned a malformed row; the chunk emits no edges and surfaces"
        " a Question for human review"
    ),
    ReasonCode.CAPABILITY_GRAPH_EMBEDDING_SHARD_FAILED: (
        "the failed shard re-tries up to 3x with linear backoff;"
        " set BV_INDEXER_REFUSE_PARTIAL_EMBEDDING=true to make it a hard-fail"
    ),
    ReasonCode.CAPABILITY_GRAPH_EMBEDDING_BUDGET_EXCEEDED: (
        "increase BV_INDEXER_DAILY_TOKEN_CAP or split the refresh into incremental passes"
    ),
    ReasonCode.CAPABILITY_GRAPH_EMBEDDING_TOKEN_CAP_EXCEEDED: (
        "the per-shard token cap was exceeded; split the shard or raise the cap"
    ),
    ReasonCode.CAPABILITY_GRAPH_EMBEDDING_ENDPOINT_ERROR: (
        "the FMA embedding endpoint returned an error; inspect endpoint state"
    ),
    ReasonCode.CAPABILITY_GRAPH_PERSIST_FAILED: (
        "inspect UC quota + bv_indexer_sp grants on the capability-graph schema"
    ),
    ReasonCode.CAPABILITY_GRAPH_PERSIST_WRITE_FAILED: (
        "the Statement Execution write returned a partial result; inspect the failing table"
    ),
    ReasonCode.CAPABILITY_GRAPH_VS_SYNC_FAILED: (
        "inspect Mosaic AI Vector Search endpoint state"
    ),
    ReasonCode.CAPABILITY_GRAPH_VS_UPSERT_FAILED: (
        "the VS index upsert returned an error; inspect endpoint state"
    ),
    ReasonCode.CAPABILITY_GRAPH_VS_INDEX_ENDPOINT_DOWN: (
        "the VS endpoint is offline; the indexer falls back to direct SQL queries until next refresh"
    ),
    ReasonCode.CAPABILITY_GRAPH_SMOKE_FAILED: (
        "the smoke baseline run failed; inspect the per-query diff in the Knowledge UI Refresh history tab"
    ),
    ReasonCode.CAPABILITY_GRAPH_SMOKE_BASELINE_EMPTY: (
        "the smoke baseline gold set is empty; populate eval/gold/capability_graph.py before promote"
    ),
    ReasonCode.CAPABILITY_GRAPH_SMOKE_REGRESSION: (
        "inspect per-query diff in Knowledge UI; new snapshot is REJECTED until top-1"
        " hit-rate >= 0.95 of locked v1 baseline"
    ),
    ReasonCode.CAPABILITY_GRAPH_PROMOTION_FAILED: (
        "inspect <BV_CATALOG>.<BV_SCHEMA>.active_snapshot_id row state"
    ),
    ReasonCode.CAPABILITY_GRAPH_PROMOTE_GATE_FAILED: (
        "the promote gate scorer rejected the snapshot; inspect the smoke + schema integrity scorer outputs"
    ),
    ReasonCode.CAPABILITY_GRAPH_PROMOTE_ALREADY_PROMOTED: (
        "this snapshot is already the active snapshot; promote is a no-op"
    ),
    ReasonCode.CAPABILITY_GRAPH_PROMOTE_WRITE_FAILED: (
        "the active_snapshot_id row update returned an error; inspect Lakebase row state"
    ),
    ReasonCode.CAPABILITY_GRAPH_PROMOTION_HITL_REQUIRED: (
        "smoke regressed; promotion blocked. To force-promote, run"
        " 'brickvision indexer rollback --to <id> --force' (audited)"
    ),
    ReasonCode.CAPABILITY_GRAPH_MANUAL_ROLLBACK: (
        "INFO-level — operator-initiated rollback recorded; verify"
        " <BV_CATALOG>.<BV_SCHEMA>.indexer_audit row"
    ),
    ReasonCode.CAPABILITY_GRAPH_ROLLBACK_RATE_LIMITED: (
        "rollback refused; wait until the printed next-allowed-time;"
        " tunable via BV_INDEXER_ROLLBACK_RATE_LIMIT_SEC"
    ),
    ReasonCode.CAPABILITY_GRAPH_SNAPSHOT_OUT_OF_RETENTION: (
        "rollback refused; the named snapshot is older than"
        " BV_INDEXER_SNAPSHOT_RETENTION_DAYS (default 30)"
    ),
    ReasonCode.CAPABILITY_GRAPH_SNAPSHOT_STALE: (
        "either (a) wait for the next nightly refresh, (b) trigger"
        " 'brickvision indexer refresh', or (c) widen"
        " BV_INDEXER_FRESHNESS_TOLERANCE_DAYS"
    ),
    ReasonCode.CAPABILITY_GRAPH_RETENTION_DEACTIVATE_FAILED: (
        "the retention pass failed to deactivate an out-of-window snapshot; inspect Lakebase rows"
    ),
    ReasonCode.CAPABILITY_GRAPH_RETENTION_CACHE_GC_FAILED: (
        "the embedding cache GC pass failed; non-fatal — next refresh retries"
    ),
    ReasonCode.CAPABILITY_GRAPH_TOKEN_CAP_EXCEEDED: (
        "re-tune BV_INDEXER_DAILY_TOKEN_CAP or split the refresh into incremental + full passes"
    ),
    ReasonCode.CAPABILITY_GRAPH_TELEMETRY_FAILED: (
        "the post-promote telemetry write failed; the snapshot is still active (telemetry is non-gating)"
    ),
    ReasonCode.CAPABILITY_WORKSPACE_MISMATCH: (
        "edit the skill spec to use the non-deprecated alternative; common case is a"
        " deprecated SDK method present in docs but missing in the partner's installed SDK"
    ),
    ReasonCode.HAND_AUTHORED_SKILL_MISSING_EXEMPLAR_OF: (
        "add 'exemplar_of: meta:<m>/ext:<e>' to SKILL.yaml referencing a row in"
        " <bv>.capability_graph.{meta_skills,extensions}"
    ),
    ReasonCode.HAND_AUTHORED_SKILL_ANCHOR_NOT_SOURCE_GROUNDED: (
        "re-anchor the skill to an extension with indexed SDK/OpenAPI/docs/labs"
        " provenance, or mark it as an explicit pending capability gap"
    ),
    ReasonCode.KG_SEARCH_HYDRATION_TRUNCATED: (
        "the kg_search hydration step truncated results; widen the seed ceiling or"
        " accept the hop-2 cap"
    ),
    ReasonCode.MOCK_OR_FAKE_IN_PRODUCTION_PACKAGE: (
        "a class named Fake*/Mock*/Stub*/Dummy* is defined in src/brickvision*/."
        " Move it to tests/fixtures/ and gate test-only behavior through BV_FAKE_LLM"
        " env-gates or monkeypatch"
    ),
    ReasonCode.PROTOCOL_HAS_ONLY_MOCK_SUBCLASSES: (
        "a typing.Protocol class in production code has zero concrete subclasses or"
        " every concrete subclass is a Fake*/Mock*/Stub*/Dummy*. Retire the Protocol;"
        " ship the real wrapper"
    ),
}


def _default_action(reason: ReasonCode) -> str:
    return _DEFAULT_ACTIONS.get(
        reason,
        f"see docs/23-databricks-capability-graph.md for {reason.value}",
    )


__all__ = ["Question", "ReasonCode", "question_from_failure"]
