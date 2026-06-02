"""Tier B Delta DDL: the 3-level taxonomy + edges + provenance
(§23.4.2 — 5 of 13 tables).

The graph itself:
  - ``top_orders``        — the closed 7 (§23.2.2). Versioned per snapshot.
  - ``meta_skills``       — ~54 at v0.7.7 ship (§23.2.3).
  - ``extensions``        — ~750 at v0.7.7 ship (§23.2.4).
  - ``entity_edges``      — typed cross-refs for PPR walks
                            (kinds: cites, derives, deprecates, sibling,
                            mentions, tagged_with, exemplifies — §23.1).
  - ``source_provenance`` — for each entity, the source URL/file/line/
                            commit-SHA/parsed_at it was derived from.

Schema invariants (§23.4.2 — enforced by ``CapabilityGraphSchemaIntegrity()``
scorer + the indexer's persist task):

  - Every row in ``extensions`` has exactly one parent in ``meta_skills``
    (FK constraint, same ``snapshot_id``).
  - Every row in ``meta_skills`` has exactly one parent in ``top_orders``
    (FK constraint, same ``snapshot_id``).
  - Every row in ``entity_edges`` has both endpoints existing in either
    ``meta_skills`` or ``extensions`` for the same ``snapshot_id``.
  - ``extensions.effect_class`` ∈ {read, write, write·hitl, unclassified}.
  - ``extensions.authority`` ∈ {high, medium, low}.
  - ``extensions.lifecycle`` ∈ {ga, public-preview, beta, deprecated, removed}.
  - ``extensions.cloud_variance`` ∈ {invariant, aws-only, azure-only,
    gcp-only, per-cloud-overlay}.

Databricks SQL in the target local-deploy path does not accept CHECK
constraints. Delta therefore stores the structural columns only; the
indexer's ``persist`` task and schema-integrity scorer enforce enum,
prefix, and FK-style invariants before rows are promoted.
"""

from __future__ import annotations


TOP_ORDERS_DDL = """
CREATE TABLE IF NOT EXISTS ${BV_CATALOG}.${BV_SCHEMA}.top_orders (
  snapshot_id STRING NOT NULL,
  top_order_id STRING NOT NULL,
  title STRING NOT NULL,
  description STRING,
  meta_skill_count INT NOT NULL,
  extension_count INT NOT NULL,
  hand_authored_exemplar_count INT NOT NULL
)
USING DELTA
PARTITIONED BY (snapshot_id)
TBLPROPERTIES (
  'capability_graph.role' = 'taxonomy_root',
  'capability_graph.closed_set' = 'true'
);
"""

META_SKILLS_DDL = """
CREATE TABLE IF NOT EXISTS ${BV_CATALOG}.${BV_SCHEMA}.meta_skills (
  snapshot_id STRING NOT NULL,
  meta_skill_id STRING NOT NULL,
  top_order_id STRING NOT NULL,
  title STRING NOT NULL,
  description STRING,
  pattern_tags ARRAY<STRING>,
  source_kinds ARRAY<STRING>,
  authority STRING NOT NULL,
  last_indexed_at_ms BIGINT NOT NULL
)
USING DELTA
PARTITIONED BY (snapshot_id)
TBLPROPERTIES ('capability_graph.role' = 'taxonomy_meta');
"""

EXTENSIONS_DDL = """
CREATE TABLE IF NOT EXISTS ${BV_CATALOG}.${BV_SCHEMA}.extensions (
  snapshot_id STRING NOT NULL,
  extension_id STRING NOT NULL,
  meta_skill_id STRING NOT NULL,
  top_order_id STRING NOT NULL,
  title STRING NOT NULL,
  synopsis STRING,
  effect_class STRING NOT NULL,
  when_to_use STRING,
  inputs_schema_json STRING,
  outputs_schema_json STRING,
  authority STRING NOT NULL,
  cloud_variance STRING NOT NULL,
  lifecycle STRING NOT NULL,
  authoring_surface STRING,
  min_sdk_version STRING,
  deprecates STRING,
  deprecated_by STRING,
  exemplar_skill_id STRING,
  last_indexed_at_ms BIGINT NOT NULL,
  last_indexed_corpus_hash STRING NOT NULL
)
USING DELTA
PARTITIONED BY (snapshot_id)
TBLPROPERTIES ('capability_graph.role' = 'taxonomy_extension');
"""

ENTITY_EDGES_DDL = """
CREATE TABLE IF NOT EXISTS ${BV_CATALOG}.${BV_SCHEMA}.entity_edges (
  snapshot_id STRING NOT NULL,
  src_id STRING NOT NULL,
  dst_id STRING NOT NULL,
  edge_kind STRING NOT NULL,
  weight DOUBLE,
  emitted_at_ms BIGINT NOT NULL
)
USING DELTA
PARTITIONED BY (snapshot_id)
TBLPROPERTIES ('capability_graph.role' = 'graph_edges');
"""

SOURCE_PROVENANCE_DDL = """
CREATE TABLE IF NOT EXISTS ${BV_CATALOG}.${BV_SCHEMA}.source_provenance (
  snapshot_id STRING NOT NULL,
  entity_id STRING NOT NULL,
  source_kind STRING NOT NULL,
  ref STRING NOT NULL,
  content_hash STRING NOT NULL,
  parsed_at_ms BIGINT NOT NULL,
  commit_sha STRING
)
USING DELTA
PARTITIONED BY (snapshot_id)
TBLPROPERTIES ('capability_graph.role' = 'graph_provenance');
"""


__all__ = [
    "ENTITY_EDGES_DDL",
    "EXTENSIONS_DDL",
    "META_SKILLS_DDL",
    "SOURCE_PROVENANCE_DDL",
    "TOP_ORDERS_DDL",
]
