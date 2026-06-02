"""Workspace KG Delta DDL.

Workspace observations are stored as two Delta tables in the same flat
``<BV_CATALOG>.<BV_SCHEMA>`` namespace as the capability graph:

* ``workspace_claims`` is append-only audit history.
* ``workspace_claims_current`` is the Lakebase read source with one row
  per stable claim id.
"""

from __future__ import annotations


WORKSPACE_CLAIMS_DDL = """
CREATE TABLE IF NOT EXISTS ${BV_CATALOG}.${BV_SCHEMA}.workspace_claims (
  claim_id STRING NOT NULL,
  workspace_profile_id STRING NOT NULL,
  workspace_id STRING,
  subject STRING NOT NULL,
  subject_kind STRING NOT NULL,
  predicate STRING NOT NULL,
  object_ref STRING,
  value_json STRING NOT NULL,
  metadata_json STRING,
  source_skill_id STRING NOT NULL,
  source_tool_id STRING,
  confidence DOUBLE NOT NULL,
  observed_at_ms BIGINT NOT NULL,
  emitted_at_ms BIGINT NOT NULL,
  config_hash STRING,
  run_id STRING
)
USING DELTA
PARTITIONED BY (workspace_profile_id)
TBLPROPERTIES (
  'brickvision.role' = 'workspace_kg_claims',
  'delta.appendOnly' = 'true'
);
"""


WORKSPACE_CLAIMS_CURRENT_DDL = """
CREATE TABLE IF NOT EXISTS ${BV_CATALOG}.${BV_SCHEMA}.workspace_claims_current (
  claim_id STRING NOT NULL,
  workspace_profile_id STRING NOT NULL,
  workspace_id STRING,
  subject STRING NOT NULL,
  subject_kind STRING NOT NULL,
  predicate STRING NOT NULL,
  object_ref STRING,
  value_json STRING NOT NULL,
  metadata_json STRING,
  source_skill_id STRING NOT NULL,
  source_tool_id STRING,
  confidence DOUBLE NOT NULL,
  observed_at_ms BIGINT NOT NULL,
  emitted_at_ms BIGINT NOT NULL,
  config_hash STRING,
  run_id STRING
)
USING DELTA
PARTITIONED BY (workspace_profile_id)
TBLPROPERTIES (
  'brickvision.role' = 'workspace_kg_claims_current'
);
"""


ALL_DDL: dict[str, str] = {
    "workspace_claims": WORKSPACE_CLAIMS_DDL,
    "workspace_claims_current": WORKSPACE_CLAIMS_CURRENT_DDL,
}


def render(ddl: str, catalog: str, schema: str = "brickvision") -> str:
    """Substitute ``${BV_CATALOG}`` + ``${BV_SCHEMA}`` placeholders."""

    return ddl.replace("${BV_CATALOG}", catalog).replace("${BV_SCHEMA}", schema)


__all__ = [
    "ALL_DDL",
    "WORKSPACE_CLAIMS_CURRENT_DDL",
    "WORKSPACE_CLAIMS_DDL",
    "render",
]
