"""Best-effort evaluation event persistence for BrickVision workflows."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from . import databricks_sql


def emit_evaluation_event(
    *,
    event_kind: str,
    workflow: str,
    status: str,
    subject_id: str,
    user_id: str,
    metrics: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    expectations: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    reason_codes: list[str] | None = None,
    mlflow_run_id: str = "",
    mlflow_trace_id: str = "",
    mlflow_dataset_name: str = "",
) -> dict[str, Any]:
    """Persist one normalized evaluation event.

    The writer is intentionally fail-open. Evaluation history is critical for
    staging/prod quality, but it must not break the user's foreground action.
    """

    now_ms = int(time.time() * 1000)
    event = {
        "event_id": _event_id(
            event_kind=event_kind,
            workflow=workflow,
            subject_id=subject_id,
            user_id=user_id,
            created_at_ms=now_ms,
        ),
        "event_kind": event_kind,
        "workflow": workflow,
        "status": status,
        "subject_id": subject_id,
        "user_id": user_id,
        "mlflow_run_id": mlflow_run_id,
        "mlflow_trace_id": mlflow_trace_id,
        "mlflow_dataset_name": mlflow_dataset_name,
        "metrics": metrics or {},
        "inputs": inputs or {},
        "outputs": outputs or {},
        "expectations": expectations or {},
        "evidence": evidence or [],
        "reason_codes": reason_codes or [],
        "created_at_ms": now_ms,
    }
    try:
        _persist_event(event)
    except Exception:
        event["persisted"] = False
    else:
        event["persisted"] = True
    return event


def list_recent_evaluation_events(*, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent normalized evaluation events for the Evaluation page."""

    _ensure_evaluation_events_table()
    safe_limit = max(1, min(int(limit), 100))
    rows = databricks_sql.query_sql_statement_rows(
        f"""
        SELECT
          event_id,
          event_kind,
          workflow,
          status,
          subject_id,
          user_id,
          mlflow_run_id,
          mlflow_trace_id,
          mlflow_dataset_name,
          metrics_json,
          reason_codes_json,
          created_at_ms
        FROM {databricks_sql.qualified_uc_name("evaluation_events")}
        ORDER BY created_at_ms DESC
        LIMIT {safe_limit}
        """
    )
    return [
        {
            "event_id": str(row[0]),
            "event_kind": str(row[1]),
            "workflow": str(row[2]),
            "status": str(row[3]),
            "subject_id": str(row[4]),
            "user_id": str(row[5] or ""),
            "mlflow_run_id": str(row[6] or ""),
            "mlflow_trace_id": str(row[7] or ""),
            "mlflow_dataset_name": str(row[8] or ""),
            "metrics": _decode_json_object(row[9]),
            "reason_codes": _decode_json_list(row[10]),
            "created_at_ms": int(row[11] or 0),
        }
        for row in rows
    ]


def _persist_event(event: dict[str, Any]) -> None:
    _ensure_evaluation_events_table()
    databricks_sql.execute_sql_statement(
        f"""
        INSERT INTO {databricks_sql.qualified_uc_name("evaluation_events")} (
          event_id,
          event_kind,
          workflow,
          status,
          subject_id,
          user_id,
          mlflow_run_id,
          mlflow_trace_id,
          mlflow_dataset_name,
          metrics_json,
          inputs_json,
          outputs_json,
          expectations_json,
          evidence_json,
          reason_codes_json,
          created_at_ms
        )
        VALUES (
          {databricks_sql.sql_string_literal(str(event["event_id"]))},
          {databricks_sql.sql_string_literal(str(event["event_kind"]))},
          {databricks_sql.sql_string_literal(str(event["workflow"]))},
          {databricks_sql.sql_string_literal(str(event["status"]))},
          {databricks_sql.sql_string_literal(str(event["subject_id"]))},
          {databricks_sql.sql_string_literal(str(event["user_id"]))},
          {databricks_sql.sql_string_literal(str(event["mlflow_run_id"]))},
          {databricks_sql.sql_string_literal(str(event["mlflow_trace_id"]))},
          {databricks_sql.sql_string_literal(str(event["mlflow_dataset_name"]))},
          {databricks_sql.sql_string_literal(json.dumps(event["metrics"], sort_keys=True))},
          {databricks_sql.sql_string_literal(json.dumps(event["inputs"], sort_keys=True))},
          {databricks_sql.sql_string_literal(json.dumps(event["outputs"], sort_keys=True))},
          {databricks_sql.sql_string_literal(json.dumps(event["expectations"], sort_keys=True))},
          {databricks_sql.sql_string_literal(json.dumps(event["evidence"], sort_keys=True))},
          {databricks_sql.sql_string_literal(json.dumps(event["reason_codes"], sort_keys=True))},
          {int(event["created_at_ms"])}
        )
        """
    )


def _ensure_evaluation_events_table() -> None:
    databricks_sql.execute_sql_statement(
        f"""
        CREATE TABLE IF NOT EXISTS {databricks_sql.qualified_uc_name("evaluation_events")} (
          event_id STRING NOT NULL,
          event_kind STRING NOT NULL,
          workflow STRING NOT NULL,
          status STRING NOT NULL,
          subject_id STRING NOT NULL,
          user_id STRING,
          mlflow_run_id STRING,
          mlflow_trace_id STRING,
          mlflow_dataset_name STRING,
          metrics_json STRING,
          inputs_json STRING,
          outputs_json STRING,
          expectations_json STRING,
          evidence_json STRING,
          reason_codes_json STRING,
          created_at_ms BIGINT NOT NULL
        )
        USING DELTA
        TBLPROPERTIES ('brickvision.role' = 'evaluation_events')
        """
    )


def _event_id(
    *,
    event_kind: str,
    workflow: str,
    subject_id: str,
    user_id: str,
    created_at_ms: int,
) -> str:
    raw = "|".join((event_kind, workflow, subject_id, user_id, str(created_at_ms)))
    return "evalevt_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def query_hash(value: str) -> str:
    """Stable subject id for natural-language search/ask inputs."""

    normalized = " ".join(value.strip().lower().split())
    if not normalized:
        return "query_empty"
    return "query_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _decode_json_object(value: Any) -> dict[str, Any]:
    decoded = _decode_maybe_json(value)
    return decoded if isinstance(decoded, dict) else {}


def _decode_json_list(value: Any) -> list[Any]:
    decoded = _decode_maybe_json(value)
    return decoded if isinstance(decoded, list) else []


def _decode_maybe_json(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
