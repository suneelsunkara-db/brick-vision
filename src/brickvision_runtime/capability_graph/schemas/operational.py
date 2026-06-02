"""Tier B Delta DDL: operational tables (§23.4.2 — 5 of 13 tables).

Operational support for the indexer DAG:
  - ``docs_section_aliases`` — closed-set docs-section ↔ meta-skill mapping
                                from §23.2.7. ~50 rows × schema_version. Adds
                                a new docs-section root requires a PRD bump
                                of ``schema_version``.
  - ``embedding_cache``      — content-hash keyed embedding cache. ~90% hit
                                rate on incremental refreshes. Pruned by the
                                snapshot-retention Job to ~10k entries via
                                ``last_used_at_ms``.
  - ``smoke_baseline``       — the 5-row hit-rate baseline locked at v0.7.7
                                ship. Read by the ``smoke_test`` task to
                                determine PASS/FAIL/REGRESSION (§23.3.3).
  - ``refresh_plan``         — one row per refresh attempt: planned source
                                list, sdk_version, daily-budget snapshot,
                                triggered_by, result_status. Joined to
                                ``corpus_snapshots`` by ``refresh_plan_id``.
  - ``corpus_health``        — rolling SLO data; one row per refresh ×
                                source. The Knowledge UI's Corpus tab + bet
                                criterion 13's PASS/FAIL/AT-RISK reads
                                ride on this.

The first three tables are partner-stable across snapshots (they have a
``schema_version`` not a ``snapshot_id``). Replay against an older
snapshot pins to the ``schema_version`` that was active at that snapshot's
``promoted_at_ms`` per the §23.1.6 / §23.2.7 schema-version arithmetic.
"""

from __future__ import annotations


DOCS_SECTION_ALIASES_DDL = """
CREATE TABLE IF NOT EXISTS ${BV_CATALOG}.${BV_SCHEMA}.docs_section_aliases (
  schema_version INT NOT NULL,
  docs_section_root STRING NOT NULL,
  meta_skill_id STRING NOT NULL,
  enacted_at_ms BIGINT NOT NULL
)
USING DELTA
TBLPROPERTIES (
  'capability_graph.role' = 'docs_alias',
  'capability_graph.closed_set' = 'true'
);
"""

EMBEDDING_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS ${BV_CATALOG}.${BV_SCHEMA}.embedding_cache (
  content_hash STRING NOT NULL,
  embedding_endpoint STRING NOT NULL,
  embedding_dim INT NOT NULL,
  embedding ARRAY<DOUBLE> NOT NULL,
  emitted_at_ms BIGINT NOT NULL,
  last_used_at_ms BIGINT NOT NULL
)
USING DELTA
TBLPROPERTIES (
  'capability_graph.role' = 'embedding_cache',
  'delta.autoOptimize.optimizeWrite' = 'true'
);
"""

SMOKE_BASELINE_DDL = """
CREATE TABLE IF NOT EXISTS ${BV_CATALOG}.${BV_SCHEMA}.smoke_baseline (
  query_id STRING NOT NULL,
  query_text STRING NOT NULL,
  expected_top_1_extension_id STRING NOT NULL,
  baseline_hit_rate DOUBLE NOT NULL,
  locked_at_ms BIGINT NOT NULL,
  locked_at_corpus_hash STRING NOT NULL
)
USING DELTA
TBLPROPERTIES ('capability_graph.role' = 'smoke_baseline');
"""

REFRESH_PLAN_DDL = """
CREATE TABLE IF NOT EXISTS ${BV_CATALOG}.${BV_SCHEMA}.refresh_plan (
  refresh_plan_id STRING NOT NULL,
  planned_at_ms BIGINT NOT NULL,
  planned_sources ARRAY<STRING>,
  partial_sources ARRAY<STRING>,
  sdk_version STRING,
  daily_token_cap BIGINT,
  daily_embedding_budget_usd DOUBLE,
  freshness_tolerance_days INT,
  triggered_by STRING NOT NULL,
  result_status STRING NOT NULL,
  result_snapshot_id STRING,
  duration_ms BIGINT,
  embedding_cost_usd DOUBLE
)
USING DELTA
TBLPROPERTIES ('capability_graph.role' = 'refresh_ledger');
"""

CORPUS_HEALTH_DDL = """
CREATE TABLE IF NOT EXISTS ${BV_CATALOG}.${BV_SCHEMA}.corpus_health (
  recorded_at_ms BIGINT NOT NULL,
  source_kind STRING NOT NULL,
  last_refresh_at_ms BIGINT NOT NULL,
  last_refresh_duration_ms BIGINT,
  last_refresh_status STRING,
  last_corpus_hash STRING,
  entity_count BIGINT NOT NULL,
  coverage_pct DOUBLE,
  smoke_hit_rate DOUBLE,
  embedding_cost_usd_30d DOUBLE NOT NULL,
  partial_sources_30d ARRAY<STRING>
)
USING DELTA
TBLPROPERTIES ('capability_graph.role' = 'health_metrics');
"""


__all__ = [
    "CORPUS_HEALTH_DDL",
    "DOCS_SECTION_ALIASES_DDL",
    "EMBEDDING_CACHE_DDL",
    "REFRESH_PLAN_DDL",
    "SMOKE_BASELINE_DDL",
]
