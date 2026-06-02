"""Runtime-side BrickVision model invocation attribution.

This mirrors the Console API ledger contract without importing console_api from
Databricks Jobs. Writes are best-effort: attribution must never fail indexer
work.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

_ENSURED = False


def record_model_invocation(
    *,
    feature: str,
    model_role: str,
    endpoint: str,
    request_kind: str,
    status: str,
    started_at_ms: int,
    run_id: str | None = None,
    response: Any | None = None,
    error: BaseException | None = None,
    metadata: dict[str, Any] | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
) -> None:
    """Insert one attribution row into ``model_invocation_ledger`` if possible."""

    warehouse_id = _warehouse_id()
    if not warehouse_id:
        return
    try:
        _ensure_table(warehouse_id=warehouse_id)
        usage = _extract_usage(response)
        input_tokens = input_tokens if input_tokens is not None else usage.get("input_tokens")
        output_tokens = output_tokens if output_tokens is not None else usage.get("output_tokens")
        total_tokens = total_tokens if total_tokens is not None else usage.get("total_tokens")
        if total_tokens is None and (input_tokens is not None or output_tokens is not None):
            total_tokens = int(input_tokens or 0) + int(output_tokens or 0)
        now_ms = int(time.time() * 1000)
        _execute(
            warehouse_id=warehouse_id,
            statement=f"""
            INSERT INTO {_qualified_uc_name('model_invocation_ledger')}
            (
              invocation_id,
              observed_at_ms,
              feature,
              run_id,
              user_id,
              model_role,
              endpoint,
              provider,
              request_kind,
              status,
              latency_ms,
              input_tokens,
              output_tokens,
              total_tokens,
              estimated_cost_usd,
              error_type,
              error_message,
              metadata_json
            )
            VALUES (
              {_sql_string_literal('mi_' + uuid.uuid4().hex)},
              {now_ms},
              {_sql_string_literal(feature)},
              {_nullable_string(run_id or os.environ.get('DB_RUN_ID'))},
              NULL,
              {_sql_string_literal(model_role)},
              {_sql_string_literal(endpoint)},
              {_sql_string_literal('databricks_model_serving')},
              {_sql_string_literal(request_kind)},
              {_sql_string_literal(status)},
              {max(0, now_ms - started_at_ms)},
              {_nullable_int(input_tokens)},
              {_nullable_int(output_tokens)},
              {_nullable_int(total_tokens)},
              NULL,
              {_nullable_string(type(error).__name__ if error else None)},
              {_nullable_string(str(error)[:1000] if error else None)},
              {_sql_string_literal(json.dumps(metadata or {}, sort_keys=True))}
            )
            """,
        )
    except Exception:
        return


def _ensure_table(*, warehouse_id: str) -> None:
    global _ENSURED
    if _ENSURED:
        return
    _execute(
        warehouse_id=warehouse_id,
        statement=f"""
        CREATE TABLE IF NOT EXISTS {_qualified_uc_name('model_invocation_ledger')} (
          invocation_id STRING NOT NULL,
          observed_at_ms BIGINT NOT NULL,
          feature STRING NOT NULL,
          run_id STRING,
          user_id STRING,
          model_role STRING NOT NULL,
          endpoint STRING NOT NULL,
          provider STRING NOT NULL,
          request_kind STRING NOT NULL,
          status STRING NOT NULL,
          latency_ms BIGINT,
          input_tokens BIGINT,
          output_tokens BIGINT,
          total_tokens BIGINT,
          estimated_cost_usd DOUBLE,
          error_type STRING,
          error_message STRING,
          metadata_json STRING
        )
        USING DELTA
        """,
    )
    _ENSURED = True


def _execute(*, warehouse_id: str, statement: str) -> None:
    from databricks.sdk import WorkspaceClient  # noqa: PLC0415
    from databricks.sdk.service.sql import StatementState  # noqa: PLC0415

    response = WorkspaceClient().statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        wait_timeout="50s",
    )
    state = response.status.state if response.status else None
    if state != StatementState.SUCCEEDED:
        err = response.status.error if response.status else None
        msg = err.message if err else "(no error message)"
        raise RuntimeError(f"Statement Execution returned state={state}; error={msg}")


def _warehouse_id() -> str:
    return (
        os.environ.get("DATABRICKS_WAREHOUSE_ID", "").strip()
        or os.environ.get("BV_INDEXER_WAREHOUSE_ID", "").strip()
        or os.environ.get("BV_WAREHOUSE_ID", "").strip()
    )


def _qualified_uc_name(object_name: str) -> str:
    catalog = os.environ.get("BV_CATALOG", "brickvision")
    schema = os.environ.get("BV_SCHEMA", "brickvision")
    return ".".join((_quote_identifier(catalog), _quote_identifier(schema), _quote_identifier(object_name)))


def _quote_identifier(value: str) -> str:
    if not value:
        raise ValueError("empty SQL identifier")
    return "`" + value.replace("`", "``") + "`"


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _nullable_string(value: str | None) -> str:
    if value is None or value == "":
        return "NULL"
    return _sql_string_literal(value)


def _nullable_int(value: int | None) -> str:
    return "NULL" if value is None else str(int(value))


def _extract_usage(response: Any | None) -> dict[str, int | None]:
    usage = getattr(response, "usage", None) if response is not None else None
    input_tokens = _get_usage_value(usage, "prompt_tokens", "input_tokens")
    output_tokens = _get_usage_value(usage, "completion_tokens", "output_tokens")
    total_tokens = _get_usage_value(usage, "total_tokens")
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = int(input_tokens or 0) + int(output_tokens or 0)
    return {
        "input_tokens": _coerce_int(input_tokens),
        "output_tokens": _coerce_int(output_tokens),
        "total_tokens": _coerce_int(total_tokens),
    }


def _get_usage_value(usage: Any, *names: str) -> Any:
    if usage is None:
        return None
    if isinstance(usage, dict):
        for name in names:
            if usage.get(name) is not None:
                return usage.get(name)
        return None
    for name in names:
        value = getattr(usage, name, None)
        if value is not None:
            return value
    return None


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = ["record_model_invocation"]
