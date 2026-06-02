"""Capability-graph merge layer (per §23.2 of
``docs/23-databricks-capability-graph.md``).

Takes the typed outputs of all 5 source adapters, applies routing rules
that map raw entities (SDK methods, OpenAPI operations, docs chunks,
blog posts, Lakebridge callables) onto the **3-level capability
taxonomy** (Top-Order → Meta-Skill → Extension), and emits the 5
Delta-table row types that ``persist.py`` writes.

Five inputs, five outputs
=========================

Inputs (all 5 are ``T | None`` — when an adapter ran but produced no
content, or when the indexer's per-source extract task failed and the
indexer is shipping a ``CAPABILITY_GRAPH_DOCS_CORPUS_PARTIAL``
snapshot):

  * :class:`SDKAdapterResult`     (authority 1.00)
  * :class:`OpenAPIAdapterResult` (authority 0.95)
  * :class:`DocsAdapterResult`    (authority 0.85)
  * :class:`BlogAdapterResult`    (authority 0.50, recency-decayed)
  * :class:`LabsAdapterResult`    (authority 0.75)

Outputs (typed mirrors of the Delta tables defined in
``capability_graph/schemas/types.py``):

  * :class:`TopOrderRow`          (~7 rows)
  * :class:`MetaSkillRow`         (~50–60 rows)
  * :class:`ExtensionRow`         (~hundreds-to-low-thousands)
  * :class:`EntityEdgeRow`        (entity-to-entity citations)
  * :class:`SourceProvenanceRow`  (per source-contribution lineage)

Plus :attr:`CapabilityGraphBuildResult.unlinked_entities` (entities the
deterministic routing rules couldn't place; surfaced for the
``coverage_pct`` SLO) and :attr:`build_telemetry` (per-stage counters
the indexer ships in ``CorpusSnapshotRow.task_durations_ms_json``).

Deterministic vs LLM-bound
==========================

**Deterministic (this module)** — accounts for ~95% of edge volume:

  * SDK service → meta-skill: lookup table on ``service_name``.
  * OpenAPI path-prefix → meta-skill: closed prefix tree.
  * Docs section_root → meta-skill: ``docs_section_aliases`` join.
  * Lakebridge phase → meta-skill: 4 fixed mappings (assessment /
    analysis / transpile / validation).
  * SDK method effect_class → ``ExtensionRow.effect_class`` direct copy.
  * OpenAPI ``x-databricks-effect-class`` → ``ExtensionRow.effect_class``.

**LLM-bound (gated by ``enable_blog_mentions=True``)** — accounts
for the remaining ~5% (mostly blog-post → meta-skill linkage):

  * Blog chunk → meta-skill mention extraction routes through
    :func:`brickvision_runtime.kg.extractor.kg_extractor` (the
    ``kg_extractor`` symbolic role per ``docs/14-context-engineering.md``).
    Per N189 / discipline rule 15 there is no Protocol seam — the
    capability_graph indexer Job task either dispatches the real
    extractor (production), or — when ``BV_FAKE_LLM=true`` — short-
    circuits to canned outputs in
    ``tests/fixtures/kg_extractor/canned_meta_skill_mentions.json``.

When ``enable_blog_mentions=False`` (offline / partner-stub deploy):
  * Blog entities are NOT dropped — they're emitted as
    :class:`SourceProvenanceRow` rows attached to ``corpus:blog`` so
    the Knowledge UI can still render them under "Refresh history".
  * Blog entities ARE excluded from ``ExtensionRow`` and
    ``EntityEdgeRow`` (no LLM = no confident linkage = no edge).
  * The ``unlinked_entities`` tuple grows by the blog count, which
    bumps the ``coverage_pct`` SLO and surfaces in the
    ``CorpusHealthRow.coverage_pct`` field for transparency.

Authority composition rule
==========================

When N sources contribute to the same edge (e.g., SDK
``catalog_api.create_table`` + OpenAPI ``POST /api/2.1/unity-catalog/
tables`` both link to ``ext:create-table``), the
:class:`EntityEdgeRow.weight` is the **maximum contributor authority**:

  ``weight = max(s.authority * s.recency_factor for s in contributors)``

We don't average or sum because (a) higher-authority sources are
strictly more trustworthy, and (b) summing creates non-comparable
weights across edges with different cardinality of contributors.

The :class:`SourceProvenanceRow` ledger preserves *every* contribution
(not just the max-authority one) so debug / audit can reconstruct
the lineage without consulting raw adapter outputs.

Hand-authored skill exemplar linkage
====================================

Per directive 1 of the v0.7.7 design exchange ("hand-authored skill
must pickup from the Top order skills"), each ``skills/<id>/SKILL.yaml``
declares ``exemplar_of: meta:<m>/ext:<e>`` (added to all 15 in C.0).

graph_builder reads this manifest and:
  1. Stamps the matching :class:`ExtensionRow.exemplar_skill_id` field.
  2. Increments :attr:`TopOrderRow.hand_authored_exemplar_count`.
  3. Emits an :class:`EntityEdgeRow` of kind ``exemplifies`` from
     ``skill:<s>`` to ``ext:<e>`` with weight 0.00. Hand-authored
     skills are execution contracts, not capability evidence.

If the exemplar's ``meta:<m>/ext:<e>`` doesn't exist in the snapshot
(e.g., the SDK service was renamed), the broken pointer surfaces in
:attr:`CapabilityGraphBuildResult.broken_exemplar_pointers` so the
indexer's ``smoke`` task can emit
``HAND_AUTHORED_SKILL_MISSING_EXEMPLAR_OF``. We don't fail the build —
the snapshot still ships, just with the affected hand-authored skill
unlinked.

Reason codes
============

Per §23.2:
  * :data:`ReasonCode.HAND_AUTHORED_SKILL_MISSING_EXEMPLAR_OF` —
    surfaced on broken exemplar pointers (above).
  * :data:`ReasonCode.DOCS_SECTION_ALIAS_MISSING` — surfaced on
    docs entities whose ``section_root`` isn't in the
    ``docs_section_aliases`` table; these become unlinked entities.
  * :data:`ReasonCode.BLOG_META_SKILL_INFERENCE_FAILED` — surfaced
    when ``enable_blog_mentions=True`` but no confident mention is
    returned for a chunk above the threshold.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from .schemas.types import (
    EntityEdgeRow,
    ExtensionRow,
    MetaSkillRow,
    SourceProvenanceRow,
    TopOrderRow,
)
from .sources.blog_adapter import BlogAdapterResult, BlogChunkEntity
from .sources.docs_adapter import DocsAdapterResult, DocsChunkEntity
from .sources.labs_repo_adapter import LabsAdapterResult
from .sources.openapi_adapter import OpenAPIAdapterResult
from .sources.sdk_adapter import SDKAdapterResult


# ---------------------------------------------------------------------------
# Source authority weights (per §23.1; used at edge-construction time)
# ---------------------------------------------------------------------------


_SOURCE_AUTHORITY: Mapping[str, float] = {
    "sdk": 1.00,
    "openapi": 0.95,
    "docs": 0.85,
    "labs": 0.75,
    "blog": 0.50,  # base; recency-decayed at edge-emission time
    "hand_authored": 0.00,
}


_AUTHORITY_TIER_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (0.85, "high"),
    (0.50, "medium"),
    (0.0, "low"),
)
"""Bin a numeric authority into the 3-tier enum used by
``MetaSkillRow.authority`` and ``ExtensionRow.authority``. Order
matters: first match wins as we walk top-down."""


def _authority_tier(weight: float) -> str:
    """Return ``"high" | "medium" | "low"`` for a numeric weight."""

    for thresh, tier in _AUTHORITY_TIER_THRESHOLDS:
        if weight >= thresh:
            return tier
    return "low"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class HandAuthoredSkillSpec:
    """Minimal projection of a ``skills/<id>/SKILL.yaml`` we care about.

    The full YAML has more fields (description, version, etc.); the
    builder only needs the id + the ``exemplar_of`` pointer to wire
    the linkage. The ``runtime_bridge.list_skill_catalog`` route that
    reads SKILL.yaml end-to-end can pass these projections in to keep
    graph_builder hermetic from YAML parsing.
    """

    skill_id: str  # e.g., "skill:uc.catalog-introspect"
    exemplar_of: str | None  # e.g., "meta:unity-catalog-foundation/ext:introspect-catalog-tree"
    title: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class BrokenExemplarPointer:
    """A hand-authored skill whose exemplar_of points at a non-existent
    extension. Surfaced for the smoke task's
    ``HAND_AUTHORED_SKILL_MISSING_EXEMPLAR_OF`` emission."""

    skill_id: str
    exemplar_of: str
    reason: str  # e.g., "meta-skill not found" | "extension not found"


@dataclasses.dataclass(frozen=True, slots=True)
class CapabilityGraphBuildResult:
    """Aggregate output of one ``build_capability_graph`` invocation."""

    snapshot_id: str
    built_at_ms: int

    # The 5 row sets ready for persist.py
    top_orders: tuple[TopOrderRow, ...]
    meta_skills: tuple[MetaSkillRow, ...]
    extensions: tuple[ExtensionRow, ...]
    entity_edges: tuple[EntityEdgeRow, ...]
    source_provenance: tuple[SourceProvenanceRow, ...]

    # Telemetry / debugging surfaces
    unlinked_entities: tuple[str, ...]
    broken_exemplar_pointers: tuple[BrokenExemplarPointer, ...]
    build_telemetry: Mapping[str, int]


# ---------------------------------------------------------------------------
# Blog → meta-skill mention extraction (LLM hook, production-only per N189)
# ---------------------------------------------------------------------------
#
# Discipline rule 15 (per docs/01-overview.md §0 + docs/10-generation-philosophy
# .md §8.6): no Protocol seam, no test-only fakes. The ``enable_blog_mentions``
# flag controls whether build_capability_graph attempts LLM extraction at all.
# When enabled, ``_extract_meta_skill_mentions`` is called per chunk:
#   * BV_FAKE_LLM=true short-circuits to canned outputs in
#     ``tests/fixtures/kg_extractor/canned_meta_skill_mentions.json``
#     (override via BV_FAKE_LLM_KG_EXTRACTOR_FIXTURE).
#   * Otherwise the function dispatches into
#     :func:`brickvision_runtime.kg.extractor.kg_extractor`, wrapping a
#     direct Databricks Foundation Model API call. Mentions whose
#     ``kind`` does not equal ``"meta_skill"`` are dropped.


@dataclasses.dataclass(frozen=True, slots=True)
class MetaSkillMention:
    """One extracted mention of a meta-skill within a blog chunk."""

    meta_skill_id: str
    confidence: float  # 0.0–1.0; threshold ≥0.7 to emit edge
    rationale: str  # short explanation; surfaced in source_provenance


_BLOG_MENTION_CONFIDENCE_THRESHOLD: float = 0.70
"""Per §23.2.4 — confidence below this threshold yields no edge."""


_DEFAULT_KG_EXTRACTOR_FIXTURE = (
    "tests/fixtures/kg_extractor/canned_meta_skill_mentions.json"
)


def _is_fake_llm() -> bool:
    import os  # noqa: PLC0415

    return os.environ.get("BV_FAKE_LLM", "false").lower() in ("1", "true", "yes")


def _load_canned_meta_skill_mentions() -> dict[str, list[dict[str, Any]]]:
    """Load canned blog-chunk → meta-skill mentions from a fixture.

    The fixture file's shape is::

        {
          "mentions": {
            "<chunk_id>": [
              {"meta_skill_id": "...", "confidence": 0.85, "rationale": "..."},
              ...
            ],
            ...
          }
        }
    """

    import json as _json  # noqa: PLC0415
    import os as _os  # noqa: PLC0415
    from pathlib import Path as _Path  # noqa: PLC0415

    raw = _os.environ.get("BV_FAKE_LLM_KG_EXTRACTOR_FIXTURE", "").strip()
    fixture_path = _Path(raw) if raw else _Path(_DEFAULT_KG_EXTRACTOR_FIXTURE)
    if not fixture_path.exists():
        return {}
    payload = _json.loads(fixture_path.read_text(encoding="utf-8"))
    mentions = payload.get("mentions", {})
    if not isinstance(mentions, dict):
        return {}
    return {
        str(k): [m for m in v if isinstance(m, dict)]
        for k, v in mentions.items()
        if isinstance(v, list)
    }


def _extract_meta_skill_mentions(
    *,
    chunk: BlogChunkEntity,
    candidate_meta_skills: Sequence[str],
    canned: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> Sequence[MetaSkillMention]:
    """Extract meta-skill mentions for a single blog chunk.

    Production path delegates to
    :func:`brickvision_runtime.kg.extractor.kg_extractor` over a
    direct Foundation Model API call (see
    :mod:`brickvision_runtime.capability_graph.llm`). The
    capability_graph fixture short-circuit lets the indexer run end-
    to-end on a dev workstation without a configured FMA endpoint.
    """

    if canned is not None:
        rows = canned.get(chunk.chunk_id, ())
        return tuple(
            MetaSkillMention(
                meta_skill_id=str(r["meta_skill_id"]),
                confidence=float(r.get("confidence", 0.0)),
                rationale=str(r.get("rationale", "")),
            )
            for r in rows
            if isinstance(r, Mapping)
            and r.get("meta_skill_id") in set(candidate_meta_skills)
        )

    from brickvision_runtime.capability_graph.llm import (  # noqa: PLC0415
        call_kg_extractor,
    )
    from brickvision_runtime.kg.extractor import (  # noqa: PLC0415
        kg_extractor as _kg_extractor,
    )

    def _coordinator_call(request: dict[str, Any]) -> dict[str, Any]:
        request_with_candidates = {
            **request,
            "candidate_meta_skills": list(candidate_meta_skills),
        }
        return call_kg_extractor(request_with_candidates)

    result, _questions = _kg_extractor(
        document_id=f"blog:{chunk.chunk_id}",
        chunk_id=chunk.chunk_id,
        chunk_text=chunk.text,
        coordinator_call=_coordinator_call,
    )
    candidate_set = set(candidate_meta_skills)
    return tuple(
        MetaSkillMention(
            meta_skill_id=mention.subject_id,
            confidence=mention.confidence,
            rationale=mention.evidence,
        )
        for mention in result.mentions
        if mention.kind.value == "meta_skill"
        and mention.subject_id in candidate_set
    )


# ---------------------------------------------------------------------------
# Taxonomy data — the 7 top-orders (per the rebuilt taxonomy in the
# v0.7.7 design exchange "Can you rebuild Top order skills and show me?")
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _TopOrderSpec:
    top_order_id: str
    title: str
    description: str


_TOP_ORDERS: tuple[_TopOrderSpec, ...] = (
    _TopOrderSpec(
        top_order_id="to:data-engineering",
        title="Data Engineering",
        description=(
            "Build data pipelines: Delta Lake tables, Lakeflow Jobs and "
            "Declarative Pipelines, Structured Streaming, transformations."
        ),
    ),
    _TopOrderSpec(
        top_order_id="to:data-governance",
        title="Data Governance",
        description=(
            "Catalog, ACLs, audit, lineage. Unity Catalog as the policy "
            "spine across compute, files, and AI assets."
        ),
    ),
    _TopOrderSpec(
        top_order_id="to:data-ingestion",
        title="Data Ingestion",
        description=(
            "Land data into the Lakehouse: Auto Loader, Lakeflow Connect "
            "managed connectors, change-data-capture sources."
        ),
    ),
    _TopOrderSpec(
        top_order_id="to:data-modelling",
        title="Data Modelling",
        description=(
            "Schema design: dimensional / 3NF / Data Vault, naming "
            "conventions, primary-foreign keys, table partitioning, and "
            "constraints."
        ),
    ),
    _TopOrderSpec(
        top_order_id="to:machine-learning",
        title="Machine Learning",
        description=(
            "Classical ML lifecycle: experiments, feature store, model "
            "registry, model serving, MLOps."
        ),
    ),
    _TopOrderSpec(
        top_order_id="to:gen-ai",
        title="Generative AI",
        description=(
            "LLM-native workloads: foundation model APIs, vector search, "
            "agent frameworks, evaluation, prompt registry, gateway."
        ),
    ),
    _TopOrderSpec(
        top_order_id="to:migration",
        title="Migration",
        description=(
            "Move from legacy DW/lakes onto Databricks: Lakebridge "
            "transpilation, source-warehouse assessment, validation."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Meta-skill taxonomy — explicit definitions for the ~30 known
# Databricks surface areas. Unmatched SDK services get a synthesized
# meta-skill via the fallback rule (see _resolve_meta_skill_for_sdk_service).
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _MetaSkillSpec:
    meta_skill_id: str
    top_order_id: str
    title: str
    description: str
    pattern_tags: tuple[str, ...]


_META_SKILLS: tuple[_MetaSkillSpec, ...] = (
    # ---- to:data-engineering ----
    _MetaSkillSpec(
        meta_skill_id="meta:delta-lake",
        top_order_id="to:data-engineering",
        title="Delta Lake",
        description=(
            "Tables, time travel, vacuum, optimize, change data feed, "
            "deletion vectors, liquid clustering."
        ),
        pattern_tags=("delta", "table", "vacuum", "optimize"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:lakeflow-jobs",
        top_order_id="to:data-engineering",
        title="Lakeflow Jobs",
        description="Workflow orchestration, scheduling, multi-task DAGs.",
        pattern_tags=("jobs", "workflows", "schedule"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:lakeflow-declarative-pipelines",
        top_order_id="to:data-engineering",
        title="Lakeflow Declarative Pipelines",
        description="Declarative, expectation-driven streaming and batch pipelines.",
        pattern_tags=(
            "lakeflow",
            "declarative",
            "pipelines",
            "sdp",
            "spark-declarative-pipelines",
            "dlt",
            "delta-live-tables",
        ),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:databricks-sql",
        top_order_id="to:data-engineering",
        title="Databricks SQL",
        description="SQL warehouses, Statement Execution API, queries, dashboards.",
        pattern_tags=("sql", "statement-execution", "warehouse", "dbsql"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:structured-streaming",
        top_order_id="to:data-engineering",
        title="Structured Streaming",
        description="Spark structured streaming sources, sinks, watermarking.",
        pattern_tags=("streaming", "spark"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:compute",
        top_order_id="to:data-engineering",
        title="Compute Administration",
        description="Clusters, SQL warehouses, instance pools, init scripts.",
        pattern_tags=("compute", "cluster", "warehouse"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:workspace-administration",
        top_order_id="to:data-engineering",
        title="Workspace Administration",
        description="Repos, notebooks, files, secret scopes, workspace config.",
        pattern_tags=("workspace", "repos", "notebooks"),
    ),

    # ---- to:data-governance ----
    _MetaSkillSpec(
        meta_skill_id="meta:unity-catalog-foundation",
        top_order_id="to:data-governance",
        title="Unity Catalog Foundation",
        description="Catalogs, schemas, tables, views, registered models, functions.",
        pattern_tags=("catalog", "schema", "uc", "unity-catalog"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:uc-volumes",
        top_order_id="to:data-governance",
        title="UC Volumes",
        description="Managed and external volumes for unstructured data.",
        pattern_tags=("volumes", "files", "uc"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:identity-and-access",
        top_order_id="to:data-governance",
        title="Identity & Access",
        description="Users, groups, service principals, account-level IAM.",
        pattern_tags=("iam", "users", "groups", "service-principals"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:lineage-and-audit",
        top_order_id="to:data-governance",
        title="Lineage & Audit",
        description="System tables, audit logs, lineage tables.",
        pattern_tags=("audit", "lineage", "system-tables"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:row-column-security",
        top_order_id="to:data-governance",
        title="Row & Column Security",
        description="Row filters, column masks, dynamic views.",
        pattern_tags=("rls", "cls", "filters", "masks"),
    ),

    # ---- to:data-ingestion ----
    _MetaSkillSpec(
        meta_skill_id="meta:auto-loader",
        top_order_id="to:data-ingestion",
        title="Auto Loader",
        description="cloudFiles incremental ingestion from cloud object storage.",
        pattern_tags=("auto-loader", "cloudfiles", "ingestion"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:lakeflow-connect",
        top_order_id="to:data-ingestion",
        title="Lakeflow Connect",
        description="Managed connectors for Salesforce, ServiceNow, SQL Server, etc.",
        pattern_tags=("connect", "connectors", "ingestion"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:dbsql-ingestion",
        top_order_id="to:data-ingestion",
        title="DBSQL Ingestion",
        description="COPY INTO, file upload, Databricks SQL ingestion patterns.",
        pattern_tags=("copy-into", "dbsql", "ingestion"),
    ),

    # ---- to:data-modelling ----
    _MetaSkillSpec(
        meta_skill_id="meta:dimensional-modelling",
        top_order_id="to:data-modelling",
        title="Dimensional Modelling",
        description="Star schemas, slowly changing dimensions, fact-dimension joins.",
        pattern_tags=("dimensional", "star-schema", "scd"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:naming-conventions",
        top_order_id="to:data-modelling",
        title="Naming Conventions",
        description="Catalog/schema/table naming, environment prefixes.",
        pattern_tags=("naming", "conventions"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:constraints-and-keys",
        top_order_id="to:data-modelling",
        title="Constraints & Keys",
        description="Primary keys, foreign keys, CHECK constraints, NOT NULL.",
        pattern_tags=("constraints", "primary-key", "foreign-key"),
    ),

    # ---- to:machine-learning ----
    _MetaSkillSpec(
        meta_skill_id="meta:mlflow-experiments",
        top_order_id="to:machine-learning",
        title="MLflow Experiments",
        description="Run tracking, metrics, parameters, artifacts.",
        pattern_tags=("mlflow", "experiments", "runs"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:mlflow-tracking",
        top_order_id="to:machine-learning",
        title="MLflow Tracking",
        description="Tracking server APIs, autologging, deep-learning integrations.",
        pattern_tags=("mlflow", "tracking", "autolog"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:feature-store",
        top_order_id="to:machine-learning",
        title="Feature Engineering",
        description="Online feature tables, lookups, point-in-time joins.",
        pattern_tags=("feature-engineering", "feature-store", "features"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:model-registry",
        top_order_id="to:machine-learning",
        title="Model Registry",
        description="UC-backed model registry, aliases, tags, lifecycle stages.",
        pattern_tags=("model-registry", "models", "aliases"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:model-serving",
        top_order_id="to:machine-learning",
        title="Model Serving",
        description="Real-time serving endpoints, served entities, traffic config.",
        pattern_tags=("serving", "endpoints", "real-time"),
    ),

    # ---- to:gen-ai ----
    _MetaSkillSpec(
        meta_skill_id="meta:mosaic-ai-vector-search",
        top_order_id="to:gen-ai",
        title="Mosaic AI Vector Search",
        description="Vector index endpoints, sync indexes, hybrid retrieval.",
        pattern_tags=("vector-search", "mosaic", "embeddings"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:foundation-model-apis",
        top_order_id="to:gen-ai",
        title="Foundation Model APIs",
        description=(
            "Pay-per-token / provisioned-throughput / external-model "
            "endpoints, chat & completion APIs."
        ),
        pattern_tags=("fmapi", "foundation-models", "llm", "completion"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:agent-frameworks",
        top_order_id="to:gen-ai",
        title="Agent Frameworks",
        description="Mosaic AI Agent Framework, tool calling, agent evaluation.",
        pattern_tags=("agents", "tool-calling", "agent-framework"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:mosaic-ai-gateway",
        top_order_id="to:gen-ai",
        title="Mosaic AI Gateway",
        description="LLM proxy with rate limits, audit, PII detection.",
        pattern_tags=("gateway", "ai-gateway", "rate-limit"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:mlflow-prompt-registry",
        top_order_id="to:gen-ai",
        title="MLflow Prompt Registry",
        description="Prompt versioning, evaluation, governance.",
        pattern_tags=("prompts", "mlflow", "registry"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:mlflow-tracing",
        top_order_id="to:gen-ai",
        title="MLflow Tracing",
        description="GenAI observability: spans, traces, MLflow 3 tracking.",
        pattern_tags=("tracing", "mlflow3", "observability"),
    ),

    # ---- to:migration ----
    _MetaSkillSpec(
        meta_skill_id="meta:migration-assessment",
        top_order_id="to:migration",
        title="Migration Assessment (Lakebridge)",
        description="Assess source warehouse: scan tables, profile schemas, audit perms.",
        pattern_tags=("lakebridge", "assessment", "scan", "profile"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:migration-analysis",
        top_order_id="to:migration",
        title="Migration Analysis (Lakebridge)",
        description="Parse source SQL, lint dialect-specific constructs.",
        pattern_tags=("lakebridge", "analysis", "parse", "lint"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:migration-transpile",
        top_order_id="to:migration",
        title="Migration Transpile (Lakebridge)",
        description="Translate Snowflake/Synapse/Redshift SQL → Databricks SQL.",
        pattern_tags=("lakebridge", "transpile", "translate", "convert"),
    ),
    _MetaSkillSpec(
        meta_skill_id="meta:migration-validation",
        top_order_id="to:migration",
        title="Migration Validation (Lakebridge)",
        description="Reconcile source vs target row counts and aggregations.",
        pattern_tags=("lakebridge", "validation", "reconcile", "verify"),
    ),
)


_META_BY_ID: Mapping[str, _MetaSkillSpec] = {m.meta_skill_id: m for m in _META_SKILLS}
_TOP_ORDER_BY_ID: Mapping[str, _TopOrderSpec] = {t.top_order_id: t for t in _TOP_ORDERS}


# ---------------------------------------------------------------------------
# Routing rules (deterministic; per §23.2)
# ---------------------------------------------------------------------------


_SDK_SERVICE_TO_META: Mapping[str, str] = {
    # databricks/sdk/service/<file_stem>.py -> meta_skill_id
    "catalog": "meta:unity-catalog-foundation",
    "files": "meta:uc-volumes",
    "iam": "meta:identity-and-access",
    "compute": "meta:compute",
    "jobs": "meta:lakeflow-jobs",
    "pipelines": "meta:lakeflow-declarative-pipelines",
    "workspace": "meta:workspace-administration",
    "ml": "meta:mlflow-experiments",
    "vectorsearch": "meta:mosaic-ai-vector-search",
    "serving": "meta:model-serving",
    "serving_endpoints": "meta:model-serving",
    "agentframework": "meta:agent-frameworks",
    "ai_gateway": "meta:mosaic-ai-gateway",
    "settings": "meta:workspace-administration",
    "sql": "meta:compute",
}


_SDK_EXTENSION_SLUG_ALIASES: Mapping[tuple[str, str, str], str] = {
    # Keep source-grounded operations on the extension IDs that execution
    # boundary skills cite, instead of minting parallel SDK-shaped rows.
    ("jobs", "JobsAPI", "submit"): "jobs-runs-submit",
    ("sql", "StatementExecutionAPI", "execute_statement"): "statement-execution-api",
    ("catalog", "RegisteredModelsAPI", "set_alias"): "assign-production-alias",
}

_OPENAPI_EXTENSION_SLUG_ALIASES: Mapping[str, str] = {
    "jobs-runs-submit": "jobs-runs-submit",
    "jobs_21_submit": "jobs-runs-submit",
    "statement-execution-api": "statement-execution-api",
    "statementexecution_executestatement": "statement-execution-api",
    "registeredmodels_setalias": "assign-production-alias",
}


_OPENAPI_PATH_PREFIX_TO_META: tuple[tuple[str, str], ...] = (
    # (path_prefix, meta_skill_id) — first match wins. Order matters:
    # longer / more specific prefixes first.
    ("/api/2.1/unity-catalog/", "meta:unity-catalog-foundation"),
    ("/api/2.0/unity-catalog/", "meta:unity-catalog-foundation"),
    ("/api/2.0/volumes/", "meta:uc-volumes"),
    ("/api/2.0/files/", "meta:uc-volumes"),
    ("/api/2.0/permissions/", "meta:identity-and-access"),
    ("/api/2.0/iam/", "meta:identity-and-access"),
    ("/api/2.0/preview/scim/", "meta:identity-and-access"),
    ("/api/2.1/jobs/", "meta:lakeflow-jobs"),
    ("/api/2.0/jobs/", "meta:lakeflow-jobs"),
    ("/api/2.0/pipelines/", "meta:lakeflow-declarative-pipelines"),
    ("/api/2.0/clusters/", "meta:compute"),
    ("/api/2.0/sql/warehouses/", "meta:compute"),
    ("/api/2.0/instance-pools/", "meta:compute"),
    ("/api/2.0/workspace/", "meta:workspace-administration"),
    ("/api/2.0/repos/", "meta:workspace-administration"),
    ("/api/2.0/secrets/", "meta:workspace-administration"),
    ("/api/2.0/mlflow/", "meta:mlflow-experiments"),
    ("/api/2.0/feature-store/", "meta:feature-store"),
    ("/api/2.0/serving-endpoints/", "meta:model-serving"),
    ("/api/2.0/vector-search/", "meta:mosaic-ai-vector-search"),
    ("/api/2.0/ai-gateway/", "meta:mosaic-ai-gateway"),
    ("/api/2.0/system-tables/", "meta:lineage-and-audit"),
)


_DEFAULT_DOCS_SECTION_ALIASES: Mapping[str, str] = {
    # docs URL section_root -> meta_skill_id (the seed for
    # <BV_CATALOG>.<BV_SCHEMA>.docs_section_aliases). Caller can override
    # by passing docs_section_aliases= to build_capability_graph.
    "delta/": "meta:delta-lake",
    "data-engineering/": "meta:delta-lake",
    "structured-streaming/": "meta:structured-streaming",
    "ldp/": "meta:lakeflow-declarative-pipelines",
    "sdp/": "meta:lakeflow-declarative-pipelines",
    "declarative-pipelines/": "meta:lakeflow-declarative-pipelines",
    "spark-declarative-pipelines/": "meta:lakeflow-declarative-pipelines",
    "serverless-data-pipelines/": "meta:lakeflow-declarative-pipelines",
    "dlt/": "meta:lakeflow-declarative-pipelines",
    "delta-live-tables/": "meta:lakeflow-declarative-pipelines",
    "ingestion/": "meta:auto-loader",
    "auto-loader/": "meta:auto-loader",
    "lakeflow-connect/": "meta:lakeflow-connect",
    "jobs/": "meta:lakeflow-jobs",
    "workflows/": "meta:lakeflow-jobs",
    "compute/": "meta:compute",
    "clusters/": "meta:compute",
    "sql/": "meta:databricks-sql",
    "warehouses/": "meta:compute",
    "workspace/": "meta:workspace-administration",
    "repos/": "meta:workspace-administration",
    "notebooks/": "meta:workspace-administration",
    "data-governance/": "meta:unity-catalog-foundation",
    "unity-catalog/": "meta:unity-catalog-foundation",
    "data-discovery/": "meta:unity-catalog-foundation",
    "volumes/": "meta:uc-volumes",
    "files/": "meta:uc-volumes",
    "admin/": "meta:identity-and-access",
    "iam/": "meta:identity-and-access",
    "users-and-groups/": "meta:identity-and-access",
    "audit-logs/": "meta:lineage-and-audit",
    "lineage/": "meta:lineage-and-audit",
    "system-tables/": "meta:lineage-and-audit",
    "machine-learning/": "meta:mlflow-tracking",
    "mlflow/": "meta:mlflow-tracking",
    "mlflow3/": "meta:mlflow-tracing",
    "feature-engineering/": "meta:feature-store",
    "feature-store/": "meta:feature-store",
    "model-registry/": "meta:model-registry",
    "model-serving/": "meta:model-serving",
    "generative-ai/": "meta:agent-frameworks",
    "machine-learning-foundation-model-apis/": "meta:foundation-model-apis",
    "vector-search/": "meta:mosaic-ai-vector-search",
    "ai-gateway/": "meta:mosaic-ai-gateway",
    "agents/": "meta:agent-frameworks",
    "agent-framework/": "meta:agent-frameworks",
}


_DOCS_EXTENSION_ROUTE_RULES: tuple[
    tuple[str, tuple[str, ...], tuple[str, ...]],
    ...,
] = (
    (
        "meta:lakeflow-jobs/ext:pyspark-transform-task-plan",
        ("jobs/", "pyspark/", "dev-tools/"),
        ("job", "task", "pyspark", "spark", "bundle"),
    ),
    (
        "meta:lakeflow-declarative-pipelines/ext:pyspark-transform-with-expectations",
        (
            "ldp/",
            "sdp/",
            "declarative-pipelines/",
            "spark-declarative-pipelines/",
            "serverless-data-pipelines/",
            "dlt/",
            "delta-live-tables/",
            "pyspark/",
        ),
        ("pyspark", "python", "expectation", "dataframe", "pipeline"),
    ),
    (
        "meta:lakeflow-declarative-pipelines/ext:sql-transform-with-expectations",
        (
            "ldp/",
            "sdp/",
            "declarative-pipelines/",
            "spark-declarative-pipelines/",
            "serverless-data-pipelines/",
            "dlt/",
            "delta-live-tables/",
            "sql/",
        ),
        ("sql", "expectation", "etl", "pipeline", "materialized"),
    ),
    (
        "meta:delta-lake/ext:introspect-table-metadata",
        ("delta/", "tables/", "data-governance/", "sql/"),
        ("table", "metadata", "describe", "information_schema", "catalog"),
    ),
    (
        "meta:delta-lake/ext:recommend-table-layout",
        ("delta/", "optimizations/"),
        ("optimize", "liquid", "partition", "cluster", "predictive"),
    ),
    (
        "meta:agent-frameworks/ext:fetch-doc-passages-on-demand",
        ("generative-ai/", "dev-tools/"),
        ("agent", "tool", "retrieval", "docs", "mcp"),
    ),
    (
        "meta:agent-frameworks/ext:emit-workspace-claims-to-kg",
        ("data-governance/", "admin/"),
        ("system table", "catalog", "lineage", "profiling", "classification"),
    ),
    (
        "meta:lineage-and-audit/ext:introspect-table-feed-graph",
        ("data-governance/", "admin/"),
        ("lineage", "audit", "system table"),
    ),
    (
        "meta:mlflow-tracking/ext:databricks-api-plan-bind",
        ("machine-learning/", "mlflow/"),
        ("api", "mlflow", "model", "training", "serving"),
    ),
    (
        "meta:mlflow-tracking/ext:feature-label-readiness",
        ("machine-learning/", "mlflow/"),
        ("feature", "label", "automl", "training", "dataset"),
    ),
    (
        "meta:mlflow-tracking/ext:ml-model-family-select",
        ("machine-learning/", "mlflow/"),
        ("classification", "regression", "xgboost", "automl", "model"),
    ),
    (
        "meta:mlflow-tracking/ext:select-modeling-strategy",
        ("machine-learning/", "mlflow/"),
        ("classification", "regression", "forecast", "automl", "model"),
    ),
    (
        "meta:model-serving/ext:deploy-by-alias",
        ("machine-learning/",),
        ("serving", "endpoint", "deploy", "model serving"),
    ),
    (
        "meta:mlflow-tracking/ext:databricks-ml-strategy-plan",
        ("machine-learning/", "mlflow/"),
        ("mlops", "strategy", "training", "model", "experiment"),
    ),
    (
        "meta:mlflow-tracking/ext:train-with-floor-and-register",
        ("machine-learning/", "mlflow/"),
        ("train", "register", "mlflow", "experiment", "model"),
    ),
    (
        "meta:mlflow-tracking/ext:databricks-ml-training-artifact-plan",
        ("machine-learning/", "mlflow/"),
        ("artifact", "dependency", "train", "model", "mlflow"),
    ),
    (
        "meta:mlflow-tracking/ext:databricks-ml-training-backend-probe",
        ("machine-learning/", "mlflow/"),
        ("runtime", "gpu", "xgboost", "training", "ml"),
    ),
    (
        "meta:mlflow-tracking/ext:databricks-ml-training-backend-select",
        ("machine-learning/", "mlflow/"),
        ("runtime", "gpu", "xgboost", "training", "ml"),
    ),
    (
        "meta:mlflow-tracking/ext:databricks-ml-training-task-plan",
        ("jobs/", "machine-learning/", "mlflow/"),
        ("job", "task", "training", "bundle", "mlflow"),
    ),
    (
        "meta:unity-catalog-foundation/ext:bootstrap-catalog-design",
        ("data-governance/", "catalogs/"),
        ("catalog", "schema", "unity catalog", "metastore"),
    ),
    (
        "meta:unity-catalog-foundation/ext:introspect-catalog-tree",
        ("data-governance/", "catalogs/"),
        ("catalog", "schema", "table", "unity catalog"),
    ),
    (
        "meta:identity-and-access/ext:introspect-effective-grants",
        ("data-governance/", "admin/", "security/"),
        ("grant", "permission", "privilege", "access"),
    ),
    (
        "meta:identity-and-access/ext:recommend-minimum-grants",
        ("data-governance/", "admin/", "security/"),
        ("grant", "permission", "privilege", "access"),
    ),
    (
        "meta:row-column-security/ext:design-domain-taxonomy",
        ("data-governance/", "admin/", "security/"),
        ("tag", "abac", "mask", "filter", "classification"),
    ),
)


_LABS_PHASE_TO_META: Mapping[str, str] = {
    "assessment": "meta:migration-assessment",
    "analysis": "meta:migration-analysis",
    "transpile": "meta:migration-transpile",
    "validation": "meta:migration-validation",
}


# ---------------------------------------------------------------------------
# Routing helpers (deterministic resolvers per source)
# ---------------------------------------------------------------------------


def _resolve_meta_for_sdk_service(service_name: str) -> str | None:
    """SDK service name → meta_skill_id. ``None`` for unknown services."""

    return _SDK_SERVICE_TO_META.get(service_name.lower())


def _resolve_meta_for_sdk_method(method: SDKMethodEntity) -> str | None:
    """Resolve fragmented SDK modules to the closest capability meta-skill."""

    module = method.module_name.lower()
    service_class = method.service_class_name
    method_name = method.method_name.lower()

    if module == "sql" and service_class == "StatementExecutionAPI":
        return "meta:databricks-sql"
    if module == "catalog" and service_class in {
        "RegisteredModelsAPI",
        "ModelVersionsAPI",
    }:
        return "meta:model-registry"
    if module == "ml":
        if service_class == "ExperimentsAPI":
            return "meta:mlflow-experiments"
        if service_class == "ModelRegistryAPI" or "model" in method_name:
            return "meta:model-registry"
        return "meta:mlflow-tracking"

    return _resolve_meta_for_sdk_service(module)


def _extension_slug_for_sdk_method(method: SDKMethodEntity) -> str:
    alias_key = (
        method.module_name.lower(),
        method.service_class_name,
        method.method_name,
    )
    return _SDK_EXTENSION_SLUG_ALIASES.get(alias_key, method.method_name)


def _resolve_meta_for_openapi_path(path: str) -> str | None:
    """OpenAPI path → meta_skill_id via longest-prefix match."""

    if not path:
        return None
    if path.startswith("/api/2.0/sql/statements"):
        return "meta:databricks-sql"
    if path.startswith("/api/2.0/mlflow/registered-models"):
        return "meta:model-registry"
    if path.startswith("/api/2.0/mlflow/model-versions"):
        return "meta:model-registry"
    if path.startswith("/api/2.0/mlflow/experiments"):
        return "meta:mlflow-experiments"
    for prefix, meta_id in _OPENAPI_PATH_PREFIX_TO_META:
        if path.startswith(prefix):
            return meta_id
    return None


def _extension_slug_for_openapi_operation(operation_id_raw: str | None, fallback: str) -> str:
    slug = (operation_id_raw or fallback).lower().replace("_", "-").strip("-")
    return _OPENAPI_EXTENSION_SLUG_ALIASES.get(
        (operation_id_raw or fallback).lower(),
        _OPENAPI_EXTENSION_SLUG_ALIASES.get(slug, slug),
    )


def _resolve_meta_for_docs_section(
    section_root: str | None,
    aliases: Mapping[str, str],
) -> str | None:
    if not section_root:
        return None
    return aliases.get(section_root)


def _docs_extension_targets(page: DocsChunkEntity | Any) -> tuple[str, ...]:
    section_root = str(getattr(page, "section_root", "") or "")
    haystack = " ".join(
        (
            section_root,
            str(getattr(page, "title", "") or ""),
            str(getattr(page, "url", "") or ""),
        ),
    ).lower()
    targets: list[str] = []
    for extension_id, roots, tokens in _DOCS_EXTENSION_ROUTE_RULES:
        if section_root not in roots:
            continue
        if any(token in haystack for token in tokens):
            targets.append(extension_id)
    return tuple(targets)


def _resolve_meta_for_labs_phase(phase: str) -> str | None:
    return _LABS_PHASE_TO_META.get(phase)


# ---------------------------------------------------------------------------
# Extension-id mint helpers
# ---------------------------------------------------------------------------


def _mint_extension_id(meta_skill_id: str, verb_slug: str) -> str:
    """Deterministically mint an ``ext:`` id under a meta-skill.

    Format: ``meta:<m>/ext:<verb_slug>`` per §23.2.5. The ``verb_slug``
    is the lowercased + underscore-to-dash version of the source-side
    method/operation name; this gives stable ids across re-parses
    (the underlying SDK method name doesn't change between releases
    unless the API itself is renamed, which graph_builder picks up via
    a fresh ``deprecates`` edge in step 8).
    """

    slug = verb_slug.lower().replace("_", "-").strip("-")
    return f"{meta_skill_id}/ext:{slug}"


def _empty_json_schema() -> str:
    """A minimal-but-valid JSON Schema 2020-12 placeholder.

    Used for ``ExtensionRow.inputs_schema_json`` /
    ``outputs_schema_json`` until the SDK adapter's signature →
    JSONSchema converter lands (deferred to step 2b). The string is
    deterministic so re-runs produce identical content_hashes.
    """

    return json.dumps({"type": "object", "additionalProperties": True}, sort_keys=True)


# ---------------------------------------------------------------------------
# Per-source builders
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class _BuildAccumulator:
    """Mutable accumulator threaded through the per-source builders.

    Each builder appends extensions / edges / provenance rows + flags
    unlinked entities. The orchestrator consumes the accumulated state
    and computes top-order rollups at the end.
    """

    extensions_by_id: dict[str, ExtensionRow] = dataclasses.field(default_factory=dict)
    edges: list[EntityEdgeRow] = dataclasses.field(default_factory=list)
    provenance: list[SourceProvenanceRow] = dataclasses.field(default_factory=list)
    unlinked: list[str] = dataclasses.field(default_factory=list)
    contributing_sources_by_meta: dict[str, set[str]] = dataclasses.field(
        default_factory=dict
    )
    last_indexed_at_by_meta: dict[str, int] = dataclasses.field(default_factory=dict)
    extension_authority_by_id: dict[str, float] = dataclasses.field(default_factory=dict)
    """Tracks the max contributor authority per extension so subsequent
    contributions can be compared without re-scanning provenance."""

    telemetry: dict[str, int] = dataclasses.field(default_factory=dict)


def _record_meta_contribution(
    *,
    acc: _BuildAccumulator,
    meta_skill_id: str,
    source_kind: str,
    parsed_at_ms: int,
) -> None:
    """Note that ``source_kind`` contributed to ``meta_skill_id``."""

    acc.contributing_sources_by_meta.setdefault(meta_skill_id, set()).add(source_kind)
    prev = acc.last_indexed_at_by_meta.get(meta_skill_id, 0)
    if parsed_at_ms > prev:
        acc.last_indexed_at_by_meta[meta_skill_id] = parsed_at_ms


def _upsert_extension(
    *,
    acc: _BuildAccumulator,
    snapshot_id: str,
    extension_id: str,
    meta_skill_id: str,
    title: str,
    synopsis: str,
    effect_class: str,
    when_to_use: str,
    contributor_authority: float,
    cloud_variance: str,
    lifecycle: str,
    authoring_surface: str,
    min_sdk_version: str | None,
    last_indexed_at_ms: int,
    last_indexed_corpus_hash: str,
) -> ExtensionRow:
    """Insert a new ExtensionRow OR strengthen an existing one with a
    higher-authority contribution.

    The "max contributor authority wins" rule means we only override
    fields when the new contributor has STRICTLY HIGHER authority than
    the existing one. This preserves SDK-derived schemas over docs-
    derived prose, etc.
    """

    meta = _META_BY_ID[meta_skill_id]

    # Compute per-extension content hash from its textual payload.
    # This gives each extension a unique, stable key for the embedding
    # cache and VS index (the global corpus_hash is a snapshot fingerprint,
    # not suitable as a per-row primary key).
    _ext_content = f"{extension_id}\n{title}\n{synopsis}\n{when_to_use}"
    _computed_hash = hashlib.sha256(_ext_content.encode("utf-8")).hexdigest()[:16]

    new_row = ExtensionRow(
        snapshot_id=snapshot_id,
        extension_id=extension_id,
        meta_skill_id=meta_skill_id,
        top_order_id=meta.top_order_id,
        title=title,
        synopsis=synopsis,
        effect_class=effect_class,
        when_to_use=when_to_use,
        inputs_schema_json=_empty_json_schema(),
        outputs_schema_json=_empty_json_schema(),
        authority=_authority_tier(contributor_authority),
        cloud_variance=cloud_variance,
        lifecycle=lifecycle,
        authoring_surface=authoring_surface,
        min_sdk_version=min_sdk_version,
        deprecates=None,
        deprecated_by=None,
        exemplar_skill_id=None,
        last_indexed_at_ms=last_indexed_at_ms,
        last_indexed_corpus_hash=_computed_hash,
    )

    existing = acc.extensions_by_id.get(extension_id)
    if existing is None:
        acc.extensions_by_id[extension_id] = new_row
        acc.extension_authority_by_id[extension_id] = contributor_authority
        return new_row

    existing_authority = acc.extension_authority_by_id.get(extension_id, 0.0)
    if contributor_authority > existing_authority:
        acc.extensions_by_id[extension_id] = new_row
        acc.extension_authority_by_id[extension_id] = contributor_authority
        return new_row

    return existing


def _emit_edge(
    *,
    acc: _BuildAccumulator,
    snapshot_id: str,
    src_id: str,
    dst_id: str,
    edge_kind: str,
    weight: float,
    emitted_at_ms: int,
) -> None:
    acc.edges.append(
        EntityEdgeRow(
            snapshot_id=snapshot_id,
            src_id=src_id,
            dst_id=dst_id,
            edge_kind=edge_kind,
            weight=weight,
            emitted_at_ms=emitted_at_ms,
        )
    )


def _build_from_sdk(
    *,
    acc: _BuildAccumulator,
    sdk: SDKAdapterResult,
    snapshot_id: str,
    corpus_hash: str,
    sdk_version: str | None,
) -> None:
    """Walk SDK methods → ExtensionRow (one per public method).

    Step 2b refinements:

    * **Pagination dedup** — when a method has ``paginates_method_id``
      populated AND its non-iter peer is also being walked in this
      pass, skip emission for the iter variant. The iter is a
      pagination affordance, not a separate capability; emitting two
      extensions would inflate the catalog and confuse retrieval. We
      DO record the dedup in ``acc.telemetry`` so smoke tests can
      assert it ran.
    * **Deprecation cross-link** — after the first pass, walk again to
      backfill ``deprecated_by`` on extensions whose source method
      pointed at a same-class successor (the successor's extension id
      is computed from the same ``_mint_extension_id`` rule, so we
      don't need a name table). When both endpoints exist we ALSO
      emit an explicit ``deprecates`` edge so PPR walks can traverse
      the link.
    """

    authority = _SOURCE_AUTHORITY["sdk"]

    # Set of method_ids being walked this pass. Used by the
    # pagination-dedup check so we only suppress the iter variant when
    # its non-iter peer is actually contributing an extension.
    walked_method_ids: set[str] = {m.method_id for m in sdk.methods}

    # Track method_id → extension_id for methods that DID produce an
    # extension (the keys are the survivor set after pagination dedup
    # and meta-routing). Used by the second pass to resolve
    # ``deprecated_by`` cross-links.
    extension_by_method_id: dict[str, str] = {}

    sdk_methods_skipped_pagination = 0
    for method in sdk.methods:
        # SDK module_name is the first routing signal, but fragmented
        # services such as SQL and UC-backed models need service-class
        # level routing to land on the execution capability.
        meta_id = _resolve_meta_for_sdk_method(method)
        if meta_id is None:
            acc.unlinked.append(method.method_id)
            continue

        # Pagination dedup: if this method's ``_iter`` peer link points
        # at a non-iter method we're also walking, skip the iter form.
        if (
            method.paginates_method_id is not None
            and method.paginates_method_id in walked_method_ids
        ):
            sdk_methods_skipped_pagination += 1
            continue

        ext_id = _mint_extension_id(meta_id, _extension_slug_for_sdk_method(method))
        ext = _upsert_extension(
            acc=acc,
            snapshot_id=snapshot_id,
            extension_id=ext_id,
            meta_skill_id=meta_id,
            title=method.method_name.replace("_", " ").title(),
            synopsis=(method.docstring or "").strip().split("\n")[0][:280],
            effect_class=method.effect_class or "unclassified",
            when_to_use=(method.docstring or "")[:500],
            contributor_authority=authority,
            cloud_variance="invariant",
            lifecycle="deprecated" if method.deprecated_in_docstring else "ga",
            authoring_surface="sdk",
            min_sdk_version=sdk_version,
            last_indexed_at_ms=sdk.parsed_at_ms,
            last_indexed_corpus_hash=corpus_hash,
        )
        extension_by_method_id[method.method_id] = ext.extension_id
        _record_meta_contribution(
            acc=acc, meta_skill_id=meta_id,
            source_kind="sdk", parsed_at_ms=sdk.parsed_at_ms,
        )
        _emit_edge(
            acc=acc, snapshot_id=snapshot_id,
            src_id=ext.extension_id, dst_id=method.method_id,
            edge_kind="derives", weight=authority,
            emitted_at_ms=sdk.parsed_at_ms,
        )
        acc.provenance.append(
            SourceProvenanceRow(
                snapshot_id=snapshot_id,
                entity_id=ext.extension_id,
                source_kind="sdk",
                ref=(
                    f"databricks.sdk.service.{method.module_name}."
                    f"{method.service_class_name}.{method.method_name}"
                ),
                content_hash=method.content_hash,
                parsed_at_ms=sdk.parsed_at_ms,
                commit_sha=sdk_version,
            )
        )

    # ---- Second pass: resolve deprecated_by cross-links ----------------
    #
    # For each method that pointed at a successor on the same service
    # class, look up both extension ids and (a) replace the
    # deprecating extension with one that has ``deprecated_by`` set,
    # (b) emit a ``deprecates`` edge so PPR can traverse it. We mutate
    # via _BuildAccumulator.extensions_by_id (replacing the frozen row)
    # so authority semantics from _upsert_extension are preserved.
    sdk_deprecates_resolved = 0
    for method in sdk.methods:
        if method.deprecates_method_id is None:
            continue
        src_ext_id = extension_by_method_id.get(method.method_id)
        dst_ext_id = extension_by_method_id.get(method.deprecates_method_id)
        if src_ext_id is None or dst_ext_id is None:
            continue
        if src_ext_id == dst_ext_id:
            continue  # Self-link; ignore.
        existing = acc.extensions_by_id.get(src_ext_id)
        if existing is None:
            continue  # Defensive: should never happen given above.
        # Replace the row with a copy carrying deprecated_by set.
        acc.extensions_by_id[src_ext_id] = dataclasses.replace(
            existing, deprecated_by=dst_ext_id
        )
        _emit_edge(
            acc=acc, snapshot_id=snapshot_id,
            src_id=src_ext_id, dst_id=dst_ext_id,
            edge_kind="deprecates", weight=authority,
            emitted_at_ms=sdk.parsed_at_ms,
        )
        sdk_deprecates_resolved += 1

    acc.telemetry["sdk_methods_routed"] = sum(
        1 for m in sdk.methods
        if _resolve_meta_for_sdk_method(m) is not None
    )
    acc.telemetry["sdk_methods_skipped_pagination"] = sdk_methods_skipped_pagination
    acc.telemetry["sdk_deprecates_resolved"] = sdk_deprecates_resolved


def _build_from_openapi(
    *,
    acc: _BuildAccumulator,
    oapi: OpenAPIAdapterResult,
    snapshot_id: str,
    corpus_hash: str,
) -> None:
    """Walk OpenAPI operations → ExtensionRow (one per operation)."""

    authority = _SOURCE_AUTHORITY["openapi"]
    for op in oapi.operations:
        meta_id = _resolve_meta_for_openapi_path(op.path)
        if meta_id is None:
            acc.unlinked.append(op.operation_id)
            continue

        # The verb-slug for the extension id: prefer raw operationId,
        # fall back to the canonical operation_id (which already contains
        # the path-slug fallback for spec bugs).
        verb_slug = _extension_slug_for_openapi_operation(
            op.operation_id_raw,
            op.operation_id.split(":")[-1],
        )
        ext_id = _mint_extension_id(meta_id, verb_slug)
        ext = _upsert_extension(
            acc=acc,
            snapshot_id=snapshot_id,
            extension_id=ext_id,
            meta_skill_id=meta_id,
            title=op.summary or verb_slug.replace("_", " ").title(),
            synopsis=(op.summary or op.description or "").strip().split("\n")[0][:280],
            effect_class=op.effect_class or "unclassified",
            when_to_use=(op.description or op.summary or "")[:500],
            contributor_authority=authority,
            cloud_variance="invariant",
            lifecycle="deprecated" if op.deprecated else "ga",
            authoring_surface="sdk",
            min_sdk_version=None,
            last_indexed_at_ms=oapi.parsed_at_ms,
            last_indexed_corpus_hash=corpus_hash,
        )
        _record_meta_contribution(
            acc=acc, meta_skill_id=meta_id,
            source_kind="openapi", parsed_at_ms=oapi.parsed_at_ms,
        )
        _emit_edge(
            acc=acc, snapshot_id=snapshot_id,
            src_id=ext.extension_id, dst_id=op.operation_id,
            edge_kind="derives", weight=authority,
            emitted_at_ms=oapi.parsed_at_ms,
        )
        acc.provenance.append(
            SourceProvenanceRow(
                snapshot_id=snapshot_id,
                entity_id=ext.extension_id,
                source_kind="openapi",
                ref=f"{op.http_method} {op.path}",
                content_hash=op.content_hash,
                parsed_at_ms=oapi.parsed_at_ms,
                commit_sha=None,
            )
        )
    acc.telemetry["openapi_operations_routed"] = sum(
        1 for op in oapi.operations
        if _resolve_meta_for_openapi_path(op.path) is not None
    )


def _build_from_docs(
    *,
    acc: _BuildAccumulator,
    docs: DocsAdapterResult,
    snapshot_id: str,
    corpus_hash: str,
    docs_section_aliases: Mapping[str, str],
) -> None:
    """Walk docs sections → cite edges (NOT new extensions).

    Docs are ground for the LLM at retrieval time but don't define new
    extensions on their own — the SDK / OpenAPI layers do that. So
    docs contributions:
      1. Strengthen MetaSkillRow.source_kinds (records "docs" as a
         contributing source for that meta-skill).
      2. Emit ``cites`` edges from existing extensions to docs chunks
         when the chunk falls under the same meta-skill (proxied by
         the section_root → meta-skill alias join).
      3. When ``section_root`` doesn't alias-join, the page goes to
         ``unlinked_entities`` (the indexer surfaces this as
         ``DOCS_SECTION_ALIAS_MISSING`` after the run).
    """

    authority = _SOURCE_AUTHORITY["docs"]
    for page in docs.pages:
        meta_id = _resolve_meta_for_docs_section(
            page.section_root, docs_section_aliases
        )
        if meta_id is None:
            acc.unlinked.append(page.page_id)
            continue
        _record_meta_contribution(
            acc=acc, meta_skill_id=meta_id,
            source_kind="docs", parsed_at_ms=docs.parsed_at_ms,
        )
        # One cites edge per chunk; downstream retrieval ranks by
        # vector similarity, the edge just establishes containment.
        for chunk_id in page.chunk_ids:
            _emit_edge(
                acc=acc, snapshot_id=snapshot_id,
                src_id=meta_id, dst_id=chunk_id,
                edge_kind="cites", weight=authority,
                emitted_at_ms=docs.parsed_at_ms,
            )
        acc.provenance.append(
            SourceProvenanceRow(
                snapshot_id=snapshot_id,
                entity_id=meta_id,
                source_kind="docs",
                ref=page.url,
                content_hash=page.content_hash,
                parsed_at_ms=docs.parsed_at_ms,
                commit_sha=None,
            )
        )
        for extension_id in _docs_extension_targets(page):
            if extension_id not in acc.extensions_by_id:
                continue
            ext = acc.extensions_by_id[extension_id]
            _upsert_extension(
                acc=acc,
                snapshot_id=snapshot_id,
                extension_id=extension_id,
                meta_skill_id=ext.meta_skill_id,
                title=ext.title,
                synopsis=(page.title or ext.synopsis)[:280],
                effect_class=ext.effect_class,
                when_to_use=ext.when_to_use,
                contributor_authority=authority,
                cloud_variance=ext.cloud_variance,
                lifecycle=ext.lifecycle,
                authoring_surface="docs",
                min_sdk_version=ext.min_sdk_version,
                last_indexed_at_ms=docs.parsed_at_ms,
                last_indexed_corpus_hash=corpus_hash,
            )
            for chunk_id in page.chunk_ids:
                _emit_edge(
                    acc=acc, snapshot_id=snapshot_id,
                    src_id=extension_id, dst_id=chunk_id,
                    edge_kind="cites", weight=authority,
                    emitted_at_ms=docs.parsed_at_ms,
                )
            acc.provenance.append(
                SourceProvenanceRow(
                    snapshot_id=snapshot_id,
                    entity_id=extension_id,
                    source_kind="docs",
                    ref=page.url,
                    content_hash=page.content_hash,
                    parsed_at_ms=docs.parsed_at_ms,
                    commit_sha=None,
                )
            )
    acc.telemetry["docs_pages_linked"] = sum(
        1 for p in docs.pages if _resolve_meta_for_docs_section(
            p.section_root, docs_section_aliases
        ) is not None
    )
    acc.telemetry["docs_extension_pages_linked"] = sum(
        1 for p in docs.pages if _docs_extension_targets(p)
    )


def _build_from_labs(
    *,
    acc: _BuildAccumulator,
    labs: LabsAdapterResult,
    snapshot_id: str,
    corpus_hash: str,
) -> None:
    """Walk Lakebridge callables → ExtensionRow per CLI-marked callable.

    Non-CLI internals don't get extensions (too noisy); only the
    user-facing entry points become skill verbs. The phase from the
    callable maps to the migration meta-skills.
    """

    if labs.repo is None:
        return

    authority = _SOURCE_AUTHORITY["labs"]
    rev = labs.repo.repo_revision
    for c in labs.callables:
        # Emit extension only for CLI-marked entry points or
        # phase-classifiable module-level callables.
        is_emittable = c.is_cli_command or (
            not c.is_method and c.phase != "unknown"
        )
        if not is_emittable:
            continue
        meta_id = _resolve_meta_for_labs_phase(c.phase)
        if meta_id is None:
            # Map remaining "unknown" phase CLI commands to assessment
            # as a coarse default; they're still better routed than
            # dropping entirely.
            meta_id = "meta:migration-assessment"

        ext_id = _mint_extension_id(meta_id, c.name)
        ext = _upsert_extension(
            acc=acc,
            snapshot_id=snapshot_id,
            extension_id=ext_id,
            meta_skill_id=meta_id,
            title=c.name.replace("_", " ").title(),
            synopsis=(c.docstring or "").strip().split("\n")[0][:280],
            effect_class="write" if c.phase == "transpile" else "read",
            when_to_use=(c.docstring or "")[:500],
            contributor_authority=authority,
            cloud_variance="invariant",
            lifecycle="public-preview",
            authoring_surface="cli" if c.is_cli_command else "sdk",
            min_sdk_version=None,
            last_indexed_at_ms=labs.parsed_at_ms,
            last_indexed_corpus_hash=corpus_hash,
        )
        _record_meta_contribution(
            acc=acc, meta_skill_id=meta_id,
            source_kind="labs", parsed_at_ms=labs.parsed_at_ms,
        )
        _emit_edge(
            acc=acc, snapshot_id=snapshot_id,
            src_id=ext.extension_id, dst_id=c.callable_id,
            edge_kind="derives", weight=authority,
            emitted_at_ms=labs.parsed_at_ms,
        )
        acc.provenance.append(
            SourceProvenanceRow(
                snapshot_id=snapshot_id,
                entity_id=ext.extension_id,
                source_kind="labs",
                ref=f"databricks/labs/lakebridge:{c.qualname}",
                content_hash=c.content_hash,
                parsed_at_ms=labs.parsed_at_ms,
                commit_sha=rev,
            )
        )
    acc.telemetry["labs_callables_routed"] = sum(
        1 for c in labs.callables
        if c.is_cli_command or (not c.is_method and c.phase != "unknown")
    )


def _build_from_blog(
    *,
    acc: _BuildAccumulator,
    blog: BlogAdapterResult,
    snapshot_id: str,
    corpus_hash: str,
    now_ms: int,
    enable_blog_mentions: bool,
) -> None:
    """Walk blog chunks → ``mentions`` edges (when LLM enabled).

    When ``enable_blog_mentions`` is False, blog posts contribute
    only provenance rows (no edges); when True, per-chunk LLM
    extraction emits ``mentions`` edges for any meta-skill match
    above :data:`_BLOG_MENTION_CONFIDENCE_THRESHOLD`. Per N189 the
    extraction routes through :func:`_extract_meta_skill_mentions`,
    which honors ``BV_FAKE_LLM`` for offline runs.
    """

    if blog.corpus is None:
        return

    base_authority = _SOURCE_AUTHORITY["blog"]
    candidate_metas = [m.meta_skill_id for m in _META_SKILLS]

    if not enable_blog_mentions:
        # No LLM — emit provenance only, NO edges. The blog corpus is
        # still visible in the Knowledge UI's "Refresh history" tab.
        for post in blog.posts:
            acc.provenance.append(
                SourceProvenanceRow(
                    snapshot_id=snapshot_id,
                    entity_id="corpus:blog",
                    source_kind="blog",
                    ref=post.url,
                    content_hash=post.content_hash,
                    parsed_at_ms=blog.parsed_at_ms,
                    commit_sha=None,
                )
            )
            for chunk_id in post.chunk_ids:
                acc.unlinked.append(chunk_id)
        acc.telemetry["blog_chunks_unlinked_no_llm"] = sum(
            len(p.chunk_ids) for p in blog.posts
        )
        return

    # LLM path: per-chunk extraction with confidence threshold.
    from .sources.blog_adapter import compute_recency_decayed_authority

    canned = _load_canned_meta_skill_mentions() if _is_fake_llm() else None

    for post in blog.posts:
        decayed = compute_recency_decayed_authority(
            base_authority=base_authority,
            published_at_ms=post.published_at_ms,
            now_ms=now_ms,
        )
        for chunk_id in post.chunk_ids:
            chunk = next(
                (c for c in blog.chunks if c.chunk_id == chunk_id),
                None,
            )
            if chunk is None:
                continue
            mentions = _extract_meta_skill_mentions(
                chunk=chunk,
                candidate_meta_skills=candidate_metas,
                canned=canned,
            )
            confident = [m for m in mentions if m.confidence >= _BLOG_MENTION_CONFIDENCE_THRESHOLD]
            if not confident:
                acc.unlinked.append(chunk.chunk_id)
                continue
            for mention in confident:
                if mention.meta_skill_id not in _META_BY_ID:
                    # LLM hallucinated a meta_skill_id; skip.
                    continue
                _record_meta_contribution(
                    acc=acc, meta_skill_id=mention.meta_skill_id,
                    source_kind="blog", parsed_at_ms=blog.parsed_at_ms,
                )
                _emit_edge(
                    acc=acc, snapshot_id=snapshot_id,
                    src_id=mention.meta_skill_id,
                    dst_id=chunk.chunk_id,
                    edge_kind="mentions",
                    weight=decayed * mention.confidence,
                    emitted_at_ms=blog.parsed_at_ms,
                )
                acc.provenance.append(
                    SourceProvenanceRow(
                        snapshot_id=snapshot_id,
                        entity_id=mention.meta_skill_id,
                        source_kind="blog",
                        ref=post.url,
                        content_hash=chunk.content_hash,
                        parsed_at_ms=blog.parsed_at_ms,
                        commit_sha=None,
                    )
                )
    acc.telemetry["blog_chunks_linked"] = sum(
        1 for e in acc.edges if e.edge_kind == "mentions"
    )


# ---------------------------------------------------------------------------
# Hand-authored exemplar linkage
# ---------------------------------------------------------------------------


def _parse_exemplar_pointer(pointer: str) -> tuple[str, str] | None:
    """Parse ``meta:<m>/ext:<e>`` → ``(meta_id, extension_id_full)``.

    Returns ``None`` when the format is malformed (e.g., missing the
    ``/ext:`` separator). Hand-authored skills with malformed pointers
    surface in :attr:`broken_exemplar_pointers`.
    """

    parts = pointer.split("/", 1)
    if len(parts) != 2:
        return None
    meta_part, ext_part = parts
    if not meta_part.startswith("meta:") or not ext_part.startswith("ext:"):
        return None
    full_extension_id = pointer  # exactly the pointer as given
    return meta_part, full_extension_id


def _link_exemplars(
    *,
    acc: _BuildAccumulator,
    snapshot_id: str,
    hand_authored: Sequence[HandAuthoredSkillSpec],
    built_at_ms: int,
) -> list[BrokenExemplarPointer]:
    """Stamp ExtensionRow.exemplar_skill_id where pointers resolve.

    Returns the list of broken pointers (skills whose ``exemplar_of``
    didn't match an existing extension after the source-merge phase).
    """

    broken: list[BrokenExemplarPointer] = []
    contract_link_weight = _SOURCE_AUTHORITY["hand_authored"]
    for skill in hand_authored:
        if skill.exemplar_of is None:
            broken.append(
                BrokenExemplarPointer(
                    skill_id=skill.skill_id,
                    exemplar_of="",
                    reason="missing exemplar_of pointer",
                )
            )
            continue
        parsed = _parse_exemplar_pointer(skill.exemplar_of)
        if parsed is None:
            broken.append(
                BrokenExemplarPointer(
                    skill_id=skill.skill_id,
                    exemplar_of=skill.exemplar_of,
                    reason="malformed pointer (expected meta:<m>/ext:<e>)",
                )
            )
            continue
        meta_id, extension_id = parsed
        if meta_id not in _META_BY_ID:
            broken.append(
                BrokenExemplarPointer(
                    skill_id=skill.skill_id,
                    exemplar_of=skill.exemplar_of,
                    reason=f"meta-skill not found: {meta_id}",
                )
            )
            continue
        existing = acc.extensions_by_id.get(extension_id)
        if existing is None:
            # Mint a stub extension so the hand-authored skill always
            # has a target. The exemplar_skill_id stamp is the linkage
            # signal; downstream the stub may be enriched by a later
            # SDK / OpenAPI contribution.
            stub = ExtensionRow(
                snapshot_id=snapshot_id,
                extension_id=extension_id,
                meta_skill_id=meta_id,
                top_order_id=_META_BY_ID[meta_id].top_order_id,
                title=skill.title or extension_id.split(":")[-1].replace("-", " ").title(),
                synopsis=skill.title or "",
                effect_class="unclassified",
                when_to_use="(hand-authored exemplar; awaiting SDK/OpenAPI enrichment)",
                inputs_schema_json=_empty_json_schema(),
                outputs_schema_json=_empty_json_schema(),
                authority=_authority_tier(contract_link_weight),
                cloud_variance="invariant",
                lifecycle="ga",
                authoring_surface="sdk",
                min_sdk_version=None,
                deprecates=None,
                deprecated_by=None,
                exemplar_skill_id=skill.skill_id,
                last_indexed_at_ms=built_at_ms,
                last_indexed_corpus_hash="",
            )
            acc.extensions_by_id[extension_id] = stub
            acc.extension_authority_by_id[extension_id] = contract_link_weight
        else:
            stamped = dataclasses.replace(existing, exemplar_skill_id=skill.skill_id)
            acc.extensions_by_id[extension_id] = stamped

        _emit_edge(
            acc=acc, snapshot_id=snapshot_id,
            src_id=skill.skill_id, dst_id=extension_id,
            edge_kind="exemplifies", weight=contract_link_weight,
            emitted_at_ms=built_at_ms,
        )
        acc.provenance.append(
            SourceProvenanceRow(
                snapshot_id=snapshot_id,
                entity_id=extension_id,
                source_kind="hand_authored",
                ref=skill.skill_id,
                content_hash="",
                parsed_at_ms=built_at_ms,
                commit_sha=None,
            )
        )
    acc.telemetry["hand_authored_exemplars_linked"] = len(hand_authored) - len(broken)
    return broken


# ---------------------------------------------------------------------------
# Rollup pass: compute MetaSkillRow + TopOrderRow from accumulated state
# ---------------------------------------------------------------------------


def _compute_meta_skills(
    *,
    acc: _BuildAccumulator,
    snapshot_id: str,
    built_at_ms: int,
) -> tuple[MetaSkillRow, ...]:
    """Materialize a MetaSkillRow per meta-skill that received contributions."""

    rows: list[MetaSkillRow] = []
    for spec in _META_SKILLS:
        contributors = acc.contributing_sources_by_meta.get(spec.meta_skill_id, set())
        if not contributors:
            continue
        # Bucket authority by the highest contributor weight (ignoring blog
        # decay because at the meta-skill level we want "what's the best
        # source we have for this skill").
        best_authority = max(
            (_SOURCE_AUTHORITY[s] for s in contributors), default=0.0
        )
        last_indexed = acc.last_indexed_at_by_meta.get(
            spec.meta_skill_id, built_at_ms
        )
        rows.append(
            MetaSkillRow(
                snapshot_id=snapshot_id,
                meta_skill_id=spec.meta_skill_id,
                top_order_id=spec.top_order_id,
                title=spec.title,
                description=spec.description,
                pattern_tags=spec.pattern_tags,
                source_kinds=tuple(sorted(contributors)),
                authority=_authority_tier(best_authority),
                last_indexed_at_ms=last_indexed,
            )
        )
    rows.sort(key=lambda r: r.meta_skill_id)
    return tuple(rows)


def _compute_top_orders(
    *,
    snapshot_id: str,
    meta_skills: Sequence[MetaSkillRow],
    extensions: Sequence[ExtensionRow],
) -> tuple[TopOrderRow, ...]:
    """Roll up meta-skill + extension counts per top-order."""

    meta_count_by_to: dict[str, int] = {}
    for ms in meta_skills:
        meta_count_by_to[ms.top_order_id] = meta_count_by_to.get(ms.top_order_id, 0) + 1

    ext_count_by_to: dict[str, int] = {}
    exemplar_count_by_to: dict[str, int] = {}
    for ext in extensions:
        ext_count_by_to[ext.top_order_id] = ext_count_by_to.get(ext.top_order_id, 0) + 1
        if ext.exemplar_skill_id is not None:
            exemplar_count_by_to[ext.top_order_id] = (
                exemplar_count_by_to.get(ext.top_order_id, 0) + 1
            )

    rows: list[TopOrderRow] = []
    for spec in _TOP_ORDERS:
        rows.append(
            TopOrderRow(
                snapshot_id=snapshot_id,
                top_order_id=spec.top_order_id,
                title=spec.title,
                description=spec.description,
                meta_skill_count=meta_count_by_to.get(spec.top_order_id, 0),
                extension_count=ext_count_by_to.get(spec.top_order_id, 0),
                hand_authored_exemplar_count=exemplar_count_by_to.get(
                    spec.top_order_id, 0
                ),
            )
        )
    return tuple(rows)


def _dedupe_edges(edges: Sequence[EntityEdgeRow]) -> tuple[EntityEdgeRow, ...]:
    by_key: dict[tuple[str, str, str, str], EntityEdgeRow] = {}
    for edge in edges:
        key = (edge.snapshot_id, edge.src_id, edge.dst_id, edge.edge_kind)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = edge
            continue
        by_key[key] = dataclasses.replace(
            existing,
            weight=max(existing.weight, edge.weight),
            emitted_at_ms=max(existing.emitted_at_ms, edge.emitted_at_ms),
        )
    return tuple(
        sorted(
            by_key.values(),
            key=lambda e: (e.snapshot_id, e.src_id, e.dst_id, e.edge_kind),
        ),
    )


def _dedupe_provenance(
    provenance: Sequence[SourceProvenanceRow],
) -> tuple[SourceProvenanceRow, ...]:
    by_key: dict[tuple[str, str, str, str, str], SourceProvenanceRow] = {}
    for row in provenance:
        key = (
            row.snapshot_id,
            row.entity_id,
            row.source_kind,
            row.ref,
            row.content_hash,
        )
        existing = by_key.get(key)
        if existing is None or row.parsed_at_ms > existing.parsed_at_ms:
            by_key[key] = row
    return tuple(
        sorted(
            by_key.values(),
            key=lambda p: (
                p.snapshot_id,
                p.entity_id,
                p.source_kind,
                p.ref,
                p.content_hash,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def build_capability_graph(
    *,
    sdk_result: SDKAdapterResult | None,
    openapi_result: OpenAPIAdapterResult | None,
    docs_result: DocsAdapterResult | None,
    blog_result: BlogAdapterResult | None,
    labs_result: LabsAdapterResult | None,
    hand_authored_skills: Sequence[HandAuthoredSkillSpec] = (),
    snapshot_id: str,
    built_at_ms: int,
    now_ms: int,
    corpus_hash: str = "",
    docs_section_aliases: Mapping[str, str] | None = None,
    enable_blog_mentions: bool = False,
) -> CapabilityGraphBuildResult:
    """Merge all 5 source adapter outputs into the capability graph.

    Order of contribution matters because higher-authority sources get
    to override lower-authority ones via :func:`_upsert_extension`'s
    "max contributor authority wins" rule. We process in
    descending-authority order:

      1. SDK              (authority 1.00) — defines the canonical extensions.
      2. Hand-authored    (authority 0.00) — stamps exemplar pointers only.
      3. OpenAPI          (authority 0.95) — strengthens with API-side schemas.
      4. Docs             (authority 0.85) — emits ``cites`` edges to chunks.
      5. Labs (Lakebridge)(authority 0.75) — adds Migration-only extensions.
      6. Blog             (authority 0.50, decayed) — LLM-bound mentions.

    Step 2 (hand-authored) is interleaved AFTER SDK so the exemplar
    stub can be filled in by the SDK contribution where they overlap.

    All 5 inputs are independently optional. When all are ``None``,
    the result is a valid (but empty) graph: 7 zero-count TopOrderRows
    and zero of everything else. This keeps the persist/promote
    pipeline robust to indexer failures on individual sources.
    """

    aliases = docs_section_aliases or _DEFAULT_DOCS_SECTION_ALIASES
    acc = _BuildAccumulator()

    if sdk_result is not None:
        _build_from_sdk(
            acc=acc, sdk=sdk_result, snapshot_id=snapshot_id,
            corpus_hash=corpus_hash, sdk_version=sdk_result.sdk_version,
        )

    broken_pointers = _link_exemplars(
        acc=acc, snapshot_id=snapshot_id,
        hand_authored=hand_authored_skills, built_at_ms=built_at_ms,
    )

    if openapi_result is not None:
        _build_from_openapi(
            acc=acc, oapi=openapi_result, snapshot_id=snapshot_id,
            corpus_hash=corpus_hash,
        )

    if docs_result is not None:
        _build_from_docs(
            acc=acc, docs=docs_result, snapshot_id=snapshot_id,
            corpus_hash=corpus_hash, docs_section_aliases=aliases,
        )

    if labs_result is not None:
        _build_from_labs(
            acc=acc, labs=labs_result, snapshot_id=snapshot_id,
            corpus_hash=corpus_hash,
        )

    if blog_result is not None:
        _build_from_blog(
            acc=acc, blog=blog_result, snapshot_id=snapshot_id,
            corpus_hash=corpus_hash, now_ms=now_ms,
            enable_blog_mentions=enable_blog_mentions,
        )

    extensions = tuple(
        sorted(acc.extensions_by_id.values(), key=lambda e: e.extension_id)
    )
    meta_skills = _compute_meta_skills(
        acc=acc, snapshot_id=snapshot_id, built_at_ms=built_at_ms
    )
    top_orders = _compute_top_orders(
        snapshot_id=snapshot_id,
        meta_skills=meta_skills,
        extensions=extensions,
    )
    entity_edges = _dedupe_edges(acc.edges)
    source_provenance = _dedupe_provenance(acc.provenance)

    acc.telemetry["meta_skills_emitted"] = len(meta_skills)
    acc.telemetry["extensions_emitted"] = len(extensions)
    acc.telemetry["edges_emitted"] = len(entity_edges)
    acc.telemetry["edges_deduped"] = len(acc.edges) - len(entity_edges)
    acc.telemetry["provenance_rows_emitted"] = len(source_provenance)
    acc.telemetry["provenance_rows_deduped"] = (
        len(acc.provenance) - len(source_provenance)
    )
    acc.telemetry["unlinked_entities"] = len(acc.unlinked)
    acc.telemetry["broken_exemplar_pointers"] = len(broken_pointers)

    return CapabilityGraphBuildResult(
        snapshot_id=snapshot_id,
        built_at_ms=built_at_ms,
        top_orders=top_orders,
        meta_skills=meta_skills,
        extensions=extensions,
        entity_edges=entity_edges,
        source_provenance=source_provenance,
        unlinked_entities=tuple(acc.unlinked),
        broken_exemplar_pointers=tuple(broken_pointers),
        build_telemetry=dict(acc.telemetry),
    )


__all__ = [
    "BrokenExemplarPointer",
    "CapabilityGraphBuildResult",
    "HandAuthoredSkillSpec",
    "MetaSkillMention",
    "build_capability_graph",
]
