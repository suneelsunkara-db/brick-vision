"""BrickVision attribution ledger for model-serving calls.

Databricks system tables remain the source of truth for billable usage. This
ledger records BrickVision-specific context that system tables do not know:
feature, model role, user, run ID, latency, and success/failure.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from . import databricks_sql

_ENSURED = False


def record_model_invocation(
    *,
    feature: str,
    model_role: str,
    endpoint: str,
    request_kind: str,
    status: str,
    started_at_ms: int,
    user_id: str | None = None,
    run_id: str | None = None,
    response: Any | None = None,
    error: BaseException | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Best-effort ledger insert. Never blocks the user path."""

    try:
        _ensure_model_invocation_ledger()
        usage = _extract_usage(response)
        now_ms = int(time.time() * 1000)
        databricks_sql.execute_sql_statement(
            f"""
            INSERT INTO {databricks_sql.qualified_uc_name('model_invocation_ledger')}
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
              {databricks_sql.sql_string_literal('mi_' + uuid.uuid4().hex)},
              {now_ms},
              {databricks_sql.sql_string_literal(feature)},
              {_nullable_string(run_id)},
              {_nullable_string(user_id)},
              {databricks_sql.sql_string_literal(model_role)},
              {databricks_sql.sql_string_literal(endpoint)},
              {databricks_sql.sql_string_literal('databricks_model_serving')},
              {databricks_sql.sql_string_literal(request_kind)},
              {databricks_sql.sql_string_literal(status)},
              {max(0, now_ms - started_at_ms)},
              {_nullable_int(usage.get('input_tokens'))},
              {_nullable_int(usage.get('output_tokens'))},
              {_nullable_int(usage.get('total_tokens'))},
              NULL,
              {_nullable_string(type(error).__name__ if error else None)},
              {_nullable_string(str(error)[:1000] if error else None)},
              {databricks_sql.sql_string_literal(json.dumps(metadata or {}, sort_keys=True))}
            )
            """
        )
    except Exception:
        return


def _ensure_model_invocation_ledger() -> None:
    global _ENSURED
    if _ENSURED:
        return
    databricks_sql.execute_sql_statement(
        f"""
        CREATE TABLE IF NOT EXISTS {databricks_sql.qualified_uc_name('model_invocation_ledger')} (
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
        """
    )
    _ENSURED = True


def _extract_usage(response: Any | None) -> dict[str, int | None]:
    usage = getattr(response, "usage", None) if response is not None else None
    if isinstance(usage, dict):
        input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
        output_tokens = usage.get("completion_tokens") or usage.get("output_tokens")
        total_tokens = usage.get("total_tokens")
    else:
        input_tokens = (
            getattr(usage, "prompt_tokens", None)
            or getattr(usage, "input_tokens", None)
            if usage is not None
            else None
        )
        output_tokens = (
            getattr(usage, "completion_tokens", None)
            or getattr(usage, "output_tokens", None)
            if usage is not None
            else None
        )
        total_tokens = getattr(usage, "total_tokens", None) if usage is not None else None
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = int(input_tokens or 0) + int(output_tokens or 0)
    return {
        "input_tokens": _coerce_int(input_tokens),
        "output_tokens": _coerce_int(output_tokens),
        "total_tokens": _coerce_int(total_tokens),
    }


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _nullable_string(value: str | None) -> str:
    if value is None or value == "":
        return "NULL"
    return databricks_sql.sql_string_literal(value)


def _nullable_int(value: int | None) -> str:
    return "NULL" if value is None else str(int(value))


__all__ = ["record_model_invocation"]
