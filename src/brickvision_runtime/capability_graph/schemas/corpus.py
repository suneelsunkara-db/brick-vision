"""Tier B Delta DDL: corpus-level tables (§23.4.2 — 3 of 13 tables).

Snapshot lifecycle backbone:
  - ``corpus_snapshots``      — append-only ledger of every refresh attempt.
  - ``active_snapshot_id``    — single-row pointer that readers JOIN against
                                so only the active snapshot's rows are visible.
  - ``source_authority``      — the closed-set source-authority weights from
                                §23.1.6 (sdk=1.0, openapi=0.95, docs=0.85,
                                docs/azure-microsoft-learn=0.85, blog=0.50,
                                labs=0.75, hand_authored=0.00).

Atomicity contract (§23.4.4): promotion is a single-row Delta MERGE INTO
``active_snapshot_id``. Until promotion succeeds, readers see the previous
snapshot. ``corpus_snapshots`` retains the previous-snapshot row with
``deactivated_at_ms`` set; readers join against ``active_snapshot_id`` and
never see deactivated rows by default.

The SQL strings here use ``${BV_CATALOG}`` + ``${BV_SCHEMA}`` placeholders;
install renders them via ``schemas.render(ddl, catalog, schema)`` before
issuing the CREATE. All 3 tables live under
``<BV_CATALOG>.<BV_SCHEMA>.<table>`` (single flat schema per v0.7.7
consolidation).
"""

from __future__ import annotations


CORPUS_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS ${BV_CATALOG}.${BV_SCHEMA}.corpus_snapshots (
  snapshot_id STRING NOT NULL,
  corpus_hash STRING NOT NULL,
  refresh_plan_id STRING NOT NULL,
  planned_at_ms BIGINT NOT NULL,
  partial_sources ARRAY<STRING>,
  signed_by STRING NOT NULL,
  signature STRING,
  promoted_at_ms BIGINT,
  deactivated_at_ms BIGINT,
  task_durations_ms_json STRING
)
USING DELTA
PARTITIONED BY (snapshot_id)
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'true',
  'delta.appendOnly' = 'false',
  'capability_graph.role' = 'snapshot_ledger'
);
"""

ACTIVE_SNAPSHOT_ID_DDL = """
CREATE TABLE IF NOT EXISTS ${BV_CATALOG}.${BV_SCHEMA}.active_snapshot_id (
  singleton_key STRING NOT NULL,
  snapshot_id STRING NOT NULL,
  promoted_at_ms BIGINT NOT NULL,
  promoted_by STRING NOT NULL
)
USING DELTA
TBLPROPERTIES (
  'capability_graph.role' = 'active_pointer',
  'capability_graph.singleton' = 'true'
);
"""

SOURCE_AUTHORITY_DDL = """
CREATE TABLE IF NOT EXISTS ${BV_CATALOG}.${BV_SCHEMA}.source_authority (
  schema_version INT NOT NULL,
  source_kind STRING NOT NULL,
  authority_weight DOUBLE NOT NULL,
  description STRING,
  enacted_at_ms BIGINT NOT NULL
)
USING DELTA
TBLPROPERTIES (
  'capability_graph.role' = 'authority_weights',
  'capability_graph.closed_set' = 'true'
);
"""


__all__ = [
    "ACTIVE_SNAPSHOT_ID_DDL",
    "CORPUS_SNAPSHOTS_DDL",
    "SOURCE_AUTHORITY_DDL",
]
