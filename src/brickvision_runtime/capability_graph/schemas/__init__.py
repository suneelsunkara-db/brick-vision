"""v0.7.7 — Delta DDL for the 13 capability-graph Tier B tables.

Per ``docs/23-databricks-capability-graph.md`` §23.4.2, the capability
graph's structured substrate consists of 13 Delta tables grouped into
3 thematic submodules so the SQL stays close to the docs section it
implements:

  * :mod:`.corpus`       — 3 tables: snapshot lifecycle backbone
                           (``corpus_snapshots``, ``active_snapshot_id``,
                           ``source_authority``).
  * :mod:`.taxonomy`     — 5 tables: the 3-level graph + edges + provenance
                           (``top_orders``, ``meta_skills``, ``extensions``,
                           ``entity_edges``, ``source_provenance``).
  * :mod:`.operational`  — 5 tables: indexer support
                           (``docs_section_aliases``, ``embedding_cache``,
                           ``smoke_baseline``, ``refresh_plan``,
                           ``corpus_health``).

All 13 tables live under ``<BV_CATALOG>.<BV_SCHEMA>.<table>`` (single
flat namespace per v0.7.7 schema consolidation).

Plus :mod:`.types` — frozen dataclass mirrors used by the indexer's
``persist`` task and ``retrieve.py``'s read-side serialization.

Creation order
==============

The order in :data:`ALL_DDL` is the canonical creation order; the
install pre-flight ``pre_flight.uc_schema_capability_graph_ownership``
runs them idempotently at install time. Order rationale:

  1. ``source_authority`` first (a closed-set lookup; depended on by
     the indexer's authority arbitration even on the very first
     snapshot).
  2. ``docs_section_aliases`` (closed-set lookup; depended on by the
     ``graph_builder`` task).
  3. ``smoke_baseline`` (closed at v0.7.7 ship; depended on by the
     ``smoke_test`` task).
  4. ``corpus_snapshots`` then ``active_snapshot_id`` (snapshot ledger;
     readers JOIN against ``active_snapshot_id``).
  5. ``refresh_plan`` (per-refresh plan; FK from ``corpus_snapshots``).
  6. The 5 taxonomy tables (``top_orders`` → ``meta_skills`` →
     ``extensions`` → ``entity_edges`` → ``source_provenance``).
  7. ``embedding_cache`` (cross-snapshot, content-hash keyed; can be
     created any time but kept last because it's the largest by row count
     at steady state).
  8. ``corpus_health`` (rolling SLO data; created last because it's
     written only by post-promote telemetry).

Render contract
===============

DDL strings carry both ``${BV_CATALOG}`` and ``${BV_SCHEMA}`` placeholders.
Use :func:`render` to substitute the partner's catalog + schema name
(e.g., ``brickvision_dev`` + ``brickvision``) before issuing the CREATE
statement against the workspace.

Alias-table version pinning
===========================

Three of the 13 tables (``source_authority``, ``docs_section_aliases``)
carry a ``schema_version INT`` column instead of ``snapshot_id``. They
are partner-stable across snapshots and only bump on a PRD migration.
Replay against an older snapshot pins to the ``schema_version`` that
was active at the historical snapshot's ``promoted_at_ms`` —
arithmetically: ``WHERE schema_version = (SELECT MAX(schema_version)
FROM <table> WHERE enacted_at_ms <= <historical-promoted-at>)``.
"""

from __future__ import annotations

from .corpus import (
    ACTIVE_SNAPSHOT_ID_DDL,
    CORPUS_SNAPSHOTS_DDL,
    SOURCE_AUTHORITY_DDL,
)
from .operational import (
    CORPUS_HEALTH_DDL,
    DOCS_SECTION_ALIASES_DDL,
    EMBEDDING_CACHE_DDL,
    REFRESH_PLAN_DDL,
    SMOKE_BASELINE_DDL,
)
from .taxonomy import (
    ENTITY_EDGES_DDL,
    EXTENSIONS_DDL,
    META_SKILLS_DDL,
    SOURCE_PROVENANCE_DDL,
    TOP_ORDERS_DDL,
)
from .types import (
    ActiveSnapshotRow,
    CorpusHealthRow,
    CorpusSnapshotRow,
    DocsSectionAliasRow,
    EmbeddingCacheRow,
    EntityEdgeRow,
    ExtensionRow,
    MetaSkillRow,
    RefreshPlanRow,
    SmokeBaselineRow,
    SourceAuthorityRow,
    SourceProvenanceRow,
    TopOrderRow,
)

# Canonical creation order (see module docstring rationale).
# Keys are bare table names — every table lives under
# ``<BV_CATALOG>.<BV_SCHEMA>.<table>`` (single flat schema per v0.7.7
# consolidation; no more per-domain schema sub-namespacing).
ALL_DDL: dict[str, str] = {
    "source_authority": SOURCE_AUTHORITY_DDL,
    "docs_section_aliases": DOCS_SECTION_ALIASES_DDL,
    "smoke_baseline": SMOKE_BASELINE_DDL,
    "corpus_snapshots": CORPUS_SNAPSHOTS_DDL,
    "active_snapshot_id": ACTIVE_SNAPSHOT_ID_DDL,
    "refresh_plan": REFRESH_PLAN_DDL,
    "top_orders": TOP_ORDERS_DDL,
    "meta_skills": META_SKILLS_DDL,
    "extensions": EXTENSIONS_DDL,
    "entity_edges": ENTITY_EDGES_DDL,
    "source_provenance": SOURCE_PROVENANCE_DDL,
    "embedding_cache": EMBEDDING_CACHE_DDL,
    "corpus_health": CORPUS_HEALTH_DDL,
}

# Tables keyed by ``snapshot_id`` (vs. ``schema_version``) — the indexer's
# persist + retention tasks read this set to know which tables to
# snapshot-prune on the 30-day Tier A retention sweep (§23.4.4).
SNAPSHOT_KEYED_TABLES: tuple[str, ...] = (
    "corpus_snapshots",
    "top_orders",
    "meta_skills",
    "extensions",
    "entity_edges",
    "source_provenance",
)

# Closed partner-stable tables (versioned by ``schema_version``).
SCHEMA_VERSIONED_TABLES: tuple[str, ...] = (
    "source_authority",
    "docs_section_aliases",
)


def render(ddl: str, catalog: str, schema: str = "brickvision") -> str:
    """Substitute ``${BV_CATALOG}`` + ``${BV_SCHEMA}`` placeholders.

    Mirrors :func:`brickvision_runtime.kg.schemas.render` so the install
    pre-flight runner can use a single helper for both KG and capability-
    graph DDL.
    """

    return ddl.replace("${BV_CATALOG}", catalog).replace("${BV_SCHEMA}", schema)


__all__ = [
    "ACTIVE_SNAPSHOT_ID_DDL",
    "ALL_DDL",
    "ActiveSnapshotRow",
    "CORPUS_HEALTH_DDL",
    "CORPUS_SNAPSHOTS_DDL",
    "CorpusHealthRow",
    "CorpusSnapshotRow",
    "DOCS_SECTION_ALIASES_DDL",
    "DocsSectionAliasRow",
    "EMBEDDING_CACHE_DDL",
    "ENTITY_EDGES_DDL",
    "EXTENSIONS_DDL",
    "EmbeddingCacheRow",
    "EntityEdgeRow",
    "ExtensionRow",
    "META_SKILLS_DDL",
    "MetaSkillRow",
    "REFRESH_PLAN_DDL",
    "RefreshPlanRow",
    "SCHEMA_VERSIONED_TABLES",
    "SMOKE_BASELINE_DDL",
    "SNAPSHOT_KEYED_TABLES",
    "SOURCE_AUTHORITY_DDL",
    "SOURCE_PROVENANCE_DDL",
    "SmokeBaselineRow",
    "SourceAuthorityRow",
    "SourceProvenanceRow",
    "TOP_ORDERS_DDL",
    "TopOrderRow",
    "render",
]
