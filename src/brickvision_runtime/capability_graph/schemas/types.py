"""Frozen dataclass mirrors of the 13 ``<bv>.capability_graph.*`` Delta tables.

These are the **typed write targets** the indexer's ``persist`` task
(``brickvision_runtime/capability_graph/persist.py`` — C.1 BULK) populates,
and the read-side dataclasses ``retrieve.py`` returns to its callers.

Every mirror is ``frozen=True, slots=True`` so equality is structural and
forwarded into Delta is byte-stable: ``hash(row)`` over the whole tuple is
also used as the audit-row's content hash where the indexer needs to
verify a row hasn't been mutated post-write (per
``docs/23-databricks-capability-graph.md`` §23.4.2 schema invariants).

The DDL strings live alongside in ``corpus.py``, ``taxonomy.py``,
``operational.py``; this module only defines Python types — never SQL.

NOTE: column names match the DDL exactly (snake_case). ``snapshot_id`` is
the first field on every per-snapshot row (per the §23.4.2 invariant
"All tables carry ``snapshot_id`` as their first column"). For closed
partner-stable tables (``source_authority``, ``docs_section_aliases``,
``embedding_cache``), the field is replaced by ``schema_version``
(int) so reads can pin against an older alias-table version at replay
time per §23.1.6 / §23.2.7.
"""

from __future__ import annotations

import dataclasses


# ----- Tier B / corpus.py mirrors ------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class CorpusSnapshotRow:
    """One row in ``<BV_CATALOG>.<BV_SCHEMA>.corpus_snapshots`` per refresh."""

    snapshot_id: str
    corpus_hash: str
    refresh_plan_id: str
    planned_at_ms: int
    partial_sources: tuple[str, ...]
    signed_by: str
    signature: str | None  # null until promote_snapshot
    promoted_at_ms: int | None
    deactivated_at_ms: int | None
    task_durations_ms_json: str  # per-task ms; deterministic JSON


@dataclasses.dataclass(frozen=True, slots=True)
class ActiveSnapshotRow:
    """The single-row pointer ``<BV_CATALOG>.<BV_SCHEMA>.active_snapshot_id``."""

    singleton_key: str  # always "singleton"; enforced by promotion logic
    snapshot_id: str
    promoted_at_ms: int
    promoted_by: str  # SP application_id


@dataclasses.dataclass(frozen=True, slots=True)
class SourceAuthorityRow:
    """One row in ``<BV_CATALOG>.<BV_SCHEMA>.source_authority`` (closed table)."""

    schema_version: int
    source_kind: str  # sdk | openapi | docs | blog | labs | hand_authored
    authority_weight: float
    description: str
    enacted_at_ms: int


# ----- Tier B / taxonomy.py mirrors ----------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class TopOrderRow:
    snapshot_id: str
    top_order_id: str  # e.g., "to:data-engineering-design"
    title: str
    description: str
    meta_skill_count: int
    extension_count: int
    hand_authored_exemplar_count: int


@dataclasses.dataclass(frozen=True, slots=True)
class MetaSkillRow:
    snapshot_id: str
    meta_skill_id: str  # e.g., "meta:delta-lake"
    top_order_id: str
    title: str
    description: str
    pattern_tags: tuple[str, ...]
    source_kinds: tuple[str, ...]  # which sources contributed
    authority: str  # high | medium | low
    last_indexed_at_ms: int


@dataclasses.dataclass(frozen=True, slots=True)
class ExtensionRow:
    snapshot_id: str
    extension_id: str  # e.g., "meta:delta-lake/ext:create-table"
    meta_skill_id: str
    top_order_id: str
    title: str
    synopsis: str
    effect_class: str  # read | write | write·hitl | unclassified
    when_to_use: str
    inputs_schema_json: str  # typed schema; SDK- or docs-derived
    outputs_schema_json: str
    authority: str  # high | medium | low
    cloud_variance: str  # invariant | aws-only | azure-only | gcp-only | per-cloud-overlay
    lifecycle: str  # ga | public-preview | beta | deprecated | removed
    authoring_surface: str  # sdk | cli | terraform | dab | ui | notebook
    min_sdk_version: str | None
    deprecates: str | None  # extension_id
    deprecated_by: str | None  # extension_id
    exemplar_skill_id: str | None  # e.g., "skill:uc.catalog-introspect"
    last_indexed_at_ms: int
    last_indexed_corpus_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class EntityEdgeRow:
    snapshot_id: str
    src_id: str
    dst_id: str
    edge_kind: str  # cites | derives | deprecates | sibling | mentions | tagged_with | exemplifies
    weight: float
    emitted_at_ms: int


@dataclasses.dataclass(frozen=True, slots=True)
class SourceProvenanceRow:
    snapshot_id: str
    entity_id: str  # meta_skill_id or extension_id
    source_kind: str
    ref: str  # URL / file-path / SDK-method ref
    content_hash: str
    parsed_at_ms: int
    commit_sha: str | None  # for SDK + labs sources


# ----- Tier B / operational.py mirrors -------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class DocsSectionAliasRow:
    """The closed-set docs-section ↔ meta-skill alias (§23.2.7)."""

    schema_version: int
    docs_section_root: str  # e.g., "delta/" or "machine-learning/feature-store/"
    meta_skill_id: str  # e.g., "meta:delta-lake"
    enacted_at_ms: int


@dataclasses.dataclass(frozen=True, slots=True)
class EmbeddingCacheRow:
    """Content-hash keyed embedding cache (~90% hit on incremental refresh)."""

    content_hash: str
    embedding_endpoint: str  # e.g., LLM_EMBEDDING_TASKS
    embedding_dim: int
    embedding: tuple[float, ...]  # length == embedding_dim
    emitted_at_ms: int
    last_used_at_ms: int  # bumped on every cache hit; used by retention


@dataclasses.dataclass(frozen=True, slots=True)
class SmokeBaselineRow:
    """The locked-at-v0.7.7-ship hit-rate baseline (5 rows, §23.3.3)."""

    query_id: str
    query_text: str
    expected_top_1_extension_id: str
    baseline_hit_rate: float
    locked_at_ms: int
    locked_at_corpus_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class RefreshPlanRow:
    """One row per refresh; planned source list + budgets (§23.3.6)."""

    refresh_plan_id: str
    planned_at_ms: int
    planned_sources: tuple[str, ...]
    partial_sources: tuple[str, ...]
    sdk_version: str | None
    daily_token_cap: int
    daily_embedding_budget_usd: float
    freshness_tolerance_days: int
    triggered_by: str  # cron | manual | rollback
    result_status: str  # planning | running | success | partial | failed
    result_snapshot_id: str | None
    duration_ms: int | None
    embedding_cost_usd: float | None


@dataclasses.dataclass(frozen=True, slots=True)
class CorpusHealthRow:
    """Rolling SLO data (one row per refresh × source)."""

    recorded_at_ms: int
    source_kind: str  # sdk | openapi | docs | blog | labs
    last_refresh_at_ms: int
    last_refresh_duration_ms: int | None
    last_refresh_status: str  # success | partial | failed
    last_corpus_hash: str | None
    entity_count: int
    coverage_pct: float | None  # only for sdk: methods_indexed/methods_known
    smoke_hit_rate: float | None  # only on snapshots that ran the smoke test
    embedding_cost_usd_30d: float
    partial_sources_30d: tuple[str, ...]


__all__ = [
    "ActiveSnapshotRow",
    "CorpusHealthRow",
    "CorpusSnapshotRow",
    "DocsSectionAliasRow",
    "EmbeddingCacheRow",
    "EntityEdgeRow",
    "ExtensionRow",
    "MetaSkillRow",
    "RefreshPlanRow",
    "SmokeBaselineRow",
    "SourceAuthorityRow",
    "SourceProvenanceRow",
    "TopOrderRow",
]
