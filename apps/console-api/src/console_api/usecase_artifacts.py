"""Usecase artifact SQL and build-plan persistence."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from . import databricks_sql

logger = logging.getLogger(__name__)

def _schema_profile_quality_view_name(schema_ref: str) -> str:
    slug = "".join(ch if ch.isalnum() else "_" for ch in schema_ref.lower())
    digest = hashlib.sha256(schema_ref.encode("utf-8")).hexdigest()[:8]
    return f"bv_schema_profile_quality_{slug[:64]}_{digest}".strip("_")


def _schema_profile_quality_sql(
    *, schema_ref: str, table_subjects: list[str], view_name: str,
) -> str:
    if not table_subjects:
        raise ValueError(f"schema suggestion has no table subjects: {schema_ref}")
    target = databricks_sql.qualified_uc_name(view_name)
    claims = databricks_sql.qualified_uc_name("workspace_claims_current")
    schema_literal = databricks_sql.sql_string_literal(schema_ref)
    subject_literals = ", ".join(
        databricks_sql.sql_string_literal(subject)
        for subject in sorted(set(table_subjects))
    )
    return f"""CREATE OR REPLACE VIEW {target} AS
WITH claims AS (
  SELECT subject, predicate, value_json, observed_at_ms
  FROM {claims}
  WHERE subject IN ({subject_literals})
    AND predicate IN (
      'ROW_COUNT',
      'HAS_COLUMN',
      'NULL_COUNT',
      'DISTINCT_COUNT',
      'GRAIN_CHECK'
    )
)
SELECT
  {schema_literal} AS schema_ref,
  regexp_replace(subject, '^table:', '') AS table_ref,
  CAST(MAX(CASE
    WHEN predicate = 'ROW_COUNT' THEN get_json_object(value_json, '$.row_count')
  END) AS BIGINT) AS row_count,
  COUNT_IF(predicate = 'HAS_COLUMN') AS column_claims,
  COUNT_IF(predicate = 'NULL_COUNT') AS null_count_claims,
  COUNT_IF(predicate = 'DISTINCT_COUNT') AS distinct_count_claims,
  MAX(CASE WHEN predicate = 'GRAIN_CHECK' THEN value_json END) AS grain_check_json,
  MAX(observed_at_ms) AS evidence_observed_at_ms
FROM claims
GROUP BY subject
"""


def load_latest_build_plan_statuses(
    suggestion_ids: list[str],
) -> dict[str, dict[str, Any]]:
    if not suggestion_ids:
        return {}
    suggestion_literals = ", ".join(
        databricks_sql.sql_string_literal(suggestion_id)
        for suggestion_id in sorted(set(suggestion_ids))
    )
    try:
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT
              suggestion_id,
              status,
              artifact_kind,
              artifact_name,
              target_subject,
              target_table_ref,
              execution_result_json,
              updated_at_ms
            FROM (
              SELECT
                *,
                ROW_NUMBER() OVER (
                  PARTITION BY suggestion_id
                  ORDER BY updated_at_ms DESC
                ) AS rn
              FROM {databricks_sql.qualified_uc_name("workspace_build_plans")}
              WHERE suggestion_id IN ({suggestion_literals})
            )
            WHERE rn = 1
            """
        )
    except Exception:
        logger.info("Workspace build plan status overlay unavailable.", exc_info=True)
        return {}

    statuses: dict[str, dict[str, Any]] = {}
    for row in rows:
        if len(row) < 8:
            continue
        suggestion_id = str(row[0])
        statuses[suggestion_id] = {
            "suggestion_id": suggestion_id,
            "status": str(row[1]),
            "artifact_kind": str(row[2]) if row[2] is not None else "",
            "artifact_name": str(row[3]) if row[3] is not None else "",
            "target_subject": str(row[4]) if row[4] is not None else "",
            "target_ref": str(row[5]) if row[5] is not None else "",
            "execution_result": _decode_json(row[6]),
            "updated_at_ms": int(row[7]) if row[7] is not None else None,
        }
    return statuses


def _persist_build_plan(
    *,
    plan: dict[str, Any],
    user_id: str,
    execution_status: str,
    execution_result: dict[str, Any] | None,
) -> None:
    try:
        _ensure_build_plan_table()
        now_ms = int(time.time() * 1000)
        artifact = dict(plan.get("artifact") or {})
        target = dict(plan.get("target") or {})
        target_ref = str(target.get("schema_ref") or target.get("table_ref") or "")
        artifact_name = str(artifact.get("name") or artifact.get("filename") or "")
        evidence_json = json.dumps(plan.get("evidence", {}), sort_keys=True)
        execution_json = json.dumps(execution_result or {}, sort_keys=True)
        statement = f"""
        INSERT INTO {databricks_sql.qualified_uc_name("workspace_build_plans")} (
          plan_id,
          suggestion_id,
          active_snapshot_id,
          target_subject,
          target_table_ref,
          artifact_kind,
          artifact_name,
          artifact_sql,
          status,
          evidence_json,
          execution_result_json,
          created_by,
          created_at_ms,
          updated_at_ms
        )
        VALUES (
          {databricks_sql.sql_string_literal(str(plan.get("plan_id", "")))},
          {databricks_sql.sql_string_literal(str(plan.get("suggestion_id", "")))},
          {databricks_sql.sql_string_literal(str(plan.get("active_snapshot_id", "")))},
          {databricks_sql.sql_string_literal(str(target.get("subject", "")))},
          {databricks_sql.sql_string_literal(target_ref)},
          {databricks_sql.sql_string_literal(str(artifact.get("kind", "")))},
          {databricks_sql.sql_string_literal(artifact_name)},
          {databricks_sql.sql_string_literal(str(artifact.get("sql", "")))},
          {databricks_sql.sql_string_literal(execution_status)},
          {databricks_sql.sql_string_literal(evidence_json)},
          {databricks_sql.sql_string_literal(execution_json)},
          {databricks_sql.sql_string_literal(user_id)},
          {now_ms},
          {now_ms}
        )
        """
        databricks_sql.execute_sql_statement(statement)
    except Exception:
        logger.exception("Failed to persist workspace build plan.")


def _ensure_build_plan_table() -> None:
    databricks_sql.execute_sql_statement(
        f"""
        CREATE TABLE IF NOT EXISTS {databricks_sql.qualified_uc_name("workspace_build_plans")} (
          plan_id STRING NOT NULL,
          suggestion_id STRING NOT NULL,
          active_snapshot_id STRING NOT NULL,
          target_subject STRING NOT NULL,
          target_table_ref STRING NOT NULL,
          artifact_kind STRING NOT NULL,
          artifact_name STRING NOT NULL,
          artifact_sql STRING NOT NULL,
          status STRING NOT NULL,
          evidence_json STRING,
          execution_result_json STRING,
          created_by STRING,
          created_at_ms BIGINT NOT NULL,
          updated_at_ms BIGINT NOT NULL
        )
        USING DELTA
        TBLPROPERTIES ('brickvision.role' = 'workspace_build_plans')
        """
    )


def _execute_build_view(statement: str, *, view_name: str) -> dict[str, Any]:
    try:
        databricks_sql.execute_sql_statement(statement)
        return {
            "executed": True,
            "object_type": "VIEW",
            "object_name": databricks_sql.qualified_uc_name(view_name),
            "message": "Created or replaced non-destructive schema quality view.",
        }
    except Exception as exc:
        logger.exception("Workspace build artifact execution failed.")
        return {
            "executed": False,
            "object_type": "VIEW",
            "object_name": databricks_sql.qualified_uc_name(view_name),
            "error_kind": type(exc).__name__,
            "message": str(exc),
        }


def _build_plan_id(*, suggestion_id: str, snapshot_id: str, artifact_sql: str) -> str:
    raw = json.dumps(
        {
            "suggestion_id": suggestion_id,
            "snapshot_id": snapshot_id,
            "artifact_sql": artifact_sql,
            "created_at_ms": int(time.time() * 1000),
        },
        sort_keys=True,
    )
    return "wbp_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]




def _decode_json(value: Any) -> Any:  # noqa: ANN401
    if value is None:
        return None
    try:
        return json.loads(str(value))
    except Exception:
        return value
