"""BrickVision platform observability read model.

This is intentionally platform-scoped, not usecase-scoped. It reports the
current state of BrickVision infrastructure, jobs, model configuration, and
usage instrumentation readiness.
"""

from __future__ import annotations

import os
from typing import Any

from . import databricks_sql
from .capability_graph_service import (
    get_capability_graph_health,
    get_capability_graph_refresh_history,
)


OBSERVABILITY_TABLES = (
    "budget_namespaces",
    "budget_ledger_app",
    "budget_ledger_indexer",
    "model_invocation_ledger",
    "refresh_plan",
    "corpus_health",
    "workspace_usecase_tool_proofs",
)


def get_observability_overview(*, user_id: str) -> dict[str, Any]:
    """Return current platform observability facts and instrumentation gaps."""

    health = _safe_call(lambda: get_capability_graph_health(user_id=user_id), {})
    refresh_history = _safe_call(
        lambda: get_capability_graph_refresh_history(user_id=user_id, limit=10),
        [],
    )
    table_state = _table_states(OBSERVABILITY_TABLES)
    model_config = _model_config()
    databricks_system = _databricks_system_usage()
    proof_counts = _proof_counts(table_state["workspace_usecase_tool_proofs"]["exists"])
    budget_namespaces = _budget_namespaces(table_state["budget_namespaces"]["exists"])

    model_ledger_exists = table_state["model_invocation_ledger"]["exists"]
    app_ledger_exists = table_state["budget_ledger_app"]["exists"]
    indexer_ledger_exists = table_state["budget_ledger_indexer"]["exists"]
    system_llm_usage_ready = databricks_system["model_serving"]["status"] == "ready"
    system_billing_ready = databricks_system["billing"]["status"] == "ready"

    return {
        "status": "ready",
        "scope": "brickvision_platform",
        "summary": {
            "active_snapshot_id": health.get("active_snapshot_id"),
            "indexer_state": health.get("indexer_state", "unknown"),
            "freshness_days": health.get("freshness_days"),
            "is_stale": bool(health.get("is_stale")),
            "refresh_run_count": len(refresh_history),
            "llm_usage_instrumented": system_llm_usage_ready or model_ledger_exists,
            "budget_usage_instrumented": system_billing_ready or (app_ledger_exists and indexer_ledger_exists),
        },
        "infra": {
            "capability_graph": health,
            "lakebase": {
                "status": "configured" if os.environ.get("BV_LAKEBASE_PROJECT_ID") else "not_configured",
                "project_id": os.environ.get("BV_LAKEBASE_PROJECT_ID", ""),
                "branch": os.environ.get("BV_LAKEBASE_BRANCH", ""),
                "database": os.environ.get("BV_LAKEBASE_DATABASE", ""),
            },
            "vector_search": {
                "endpoint": os.environ.get("BV_VS_ENDPOINT", ""),
                "index_name": _entity_index_name(),
            },
            "sql_warehouse": {
                "warehouse_id": databricks_sql.resolve_warehouse_id(),
            },
        },
        "jobs": {
            "refresh_history": refresh_history,
            "latest_refresh": refresh_history[0] if refresh_history else None,
        },
        "models": {
            "configured": model_config,
            "observed_usage": _model_usage_summary(
                model_ledger_exists,
                databricks_system["model_serving"],
            ),
            "attribution": _model_attribution_summary(model_ledger_exists),
            "gaps": _model_observability_gaps(
                model_ledger_exists,
                system_llm_usage_ready,
            ),
        },
        "databricks_system": databricks_system,
        "usage": {
            "budget_namespaces": budget_namespaces,
            "proof_counts": proof_counts,
            "tables": table_state,
            "gaps": _usage_gaps(table_state, system_billing_ready),
        },
        "next_action": (
            "Add BrickVision correlation IDs to enrich Databricks system usage."
            if system_llm_usage_ready and not model_ledger_exists
            else "Enable Databricks billing/model-serving system tables or add model_invocation_ledger."
            if not system_llm_usage_ready and not model_ledger_exists
            else "Review token usage by namespace and endpoint."
        ),
    }


def get_model_serving_usage_detail(*, days: int = 7) -> dict[str, Any]:
    """Return filtered model-serving usage for configured BrickVision endpoints."""

    days = _bounded_days(days)
    endpoints = _configured_endpoints()
    if not endpoints:
        return {
            "status": "unavailable",
            "source": "system.billing.usage",
            "message": "No configured model-serving endpoints found.",
            "days": days,
            "rows": [],
        }
    try:
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT
              COALESCE(usage_metadata.endpoint_name, 'unknown') AS endpoint_name,
              usage_type,
              sku_name,
              COUNT(*) AS record_count,
              CAST(COALESCE(SUM(usage_quantity), 0) AS DOUBLE) AS usage_quantity,
              CAST(MIN(usage_date) AS STRING) AS first_usage_date,
              CAST(MAX(usage_date) AS STRING) AS last_usage_date
            FROM system.billing.usage
            WHERE usage_date >= current_date() - INTERVAL {days} DAYS
              AND usage_metadata.endpoint_name IN ({_sql_string_list(endpoints)})
            GROUP BY usage_metadata.endpoint_name, usage_type, sku_name
            ORDER BY usage_quantity DESC
            LIMIT 50
            """
        )
    except Exception as exc:
        return {
            "status": "unavailable",
            "source": "system.billing.usage",
            "message": str(exc),
            "days": days,
            "rows": [],
        }

    return {
        "status": "ready",
        "source": "system.billing.usage",
        "days": days,
        "endpoint_count": len({str(row[0]) for row in rows}),
        "record_count": sum(int(row[3]) for row in rows),
        "token_usage_quantity": sum(
            float(row[4]) for row in rows if str(row[1]).upper() == "TOKEN"
        ),
        "usage_quantity": sum(float(row[4]) for row in rows),
        "rows": [
            {
                "endpoint_name": str(row[0]),
                "usage_type": str(row[1]) if row[1] is not None else "",
                "sku_name": str(row[2]) if row[2] is not None else "",
                "record_count": int(row[3]),
                "usage_quantity": float(row[4]),
                "first_usage_date": str(row[5]) if row[5] is not None else "",
                "last_usage_date": str(row[6]) if row[6] is not None else "",
            }
            for row in rows
        ],
    }


def get_lakeflow_jobs_detail(*, days: int = 7) -> dict[str, Any]:
    """Return recent BrickVision Lakeflow job runs from Databricks system tables."""

    days = _bounded_days(days)
    job_names = _brickvision_job_names()
    try:
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT
              j.name,
              r.job_id,
              r.run_id,
              COALESCE(r.result_state, 'RUNNING_OR_UNKNOWN') AS result_state,
              CAST(r.period_start_time AS STRING) AS start_time,
              CAST(r.period_end_time AS STRING) AS end_time,
              COALESCE(r.run_duration_seconds, 0) AS run_duration_seconds,
              COALESCE(r.termination_code, '') AS termination_code
            FROM system.lakeflow.job_run_timeline r
            INNER JOIN system.lakeflow.jobs j
              ON r.workspace_id = j.workspace_id
             AND r.job_id = j.job_id
            WHERE r.period_start_time >= current_timestamp() - INTERVAL {days} DAYS
              AND j.name IN ({_sql_string_list(job_names)})
              AND j.delete_time IS NULL
            ORDER BY r.period_start_time DESC
            LIMIT 50
            """
        )
    except Exception as exc:
        return {
            "status": "unavailable",
            "source": "system.lakeflow.job_run_timeline",
            "message": str(exc),
            "days": days,
            "rows": [],
        }

    return {
        "status": "ready",
        "source": "system.lakeflow.job_run_timeline",
        "days": days,
        "job_names": job_names,
        "run_count": len(rows),
        "failure_count": sum(
            1 for row in rows if str(row[3]).upper() in {"ERROR", "FAILED", "TIMED_OUT"}
        ),
        "rows": [
            {
                "job_name": str(row[0]),
                "job_id": str(row[1]),
                "run_id": str(row[2]),
                "result_state": str(row[3]),
                "start_time": str(row[4]) if row[4] is not None else "",
                "end_time": str(row[5]) if row[5] is not None else "",
                "run_duration_seconds": int(row[6]),
                "termination_code": str(row[7]) if row[7] is not None else "",
            }
            for row in rows
        ],
    }


def get_sql_queries_detail(*, hours: int = 24) -> dict[str, Any]:
    """Return recent SQL warehouse query summary for BrickVision's warehouse."""

    hours = max(1, min(int(hours), 168))
    warehouse_id = databricks_sql.resolve_warehouse_id()
    try:
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT
              statement_id,
              COALESCE(execution_status, 'UNKNOWN') AS execution_status,
              COALESCE(statement_type, '') AS statement_type,
              CAST(start_time AS STRING) AS start_time,
              CAST(end_time AS STRING) AS end_time,
              COALESCE(total_duration_ms, 0) AS total_duration_ms,
              COALESCE(executed_as, '') AS executed_as,
              COALESCE(from_result_cache, false) AS from_result_cache
            FROM system.query.history
            WHERE start_time >= current_timestamp() - INTERVAL {hours} HOURS
              AND compute.warehouse_id = {databricks_sql.sql_string_literal(warehouse_id)}
            ORDER BY start_time DESC
            LIMIT 50
            """
        )
    except Exception as exc:
        return {
            "status": "unavailable",
            "source": "system.query.history",
            "message": str(exc),
            "hours": hours,
            "warehouse_id": warehouse_id,
            "rows": [],
        }

    durations = [int(row[5]) for row in rows]
    return {
        "status": "ready",
        "source": "system.query.history",
        "hours": hours,
        "warehouse_id": warehouse_id,
        "query_count": len(rows),
        "avg_duration_ms": int(sum(durations) / len(durations)) if durations else 0,
        "failure_count": sum(
            1 for row in rows if str(row[1]).upper() not in {"FINISHED", "SUCCEEDED"}
        ),
        "rows": [
            {
                "statement_id": str(row[0]),
                "execution_status": str(row[1]),
                "statement_type": str(row[2]),
                "start_time": str(row[3]) if row[3] is not None else "",
                "end_time": str(row[4]) if row[4] is not None else "",
                "total_duration_ms": int(row[5]),
                "executed_as": str(row[6]) if row[6] is not None else "",
                "from_result_cache": bool(row[7]),
            }
            for row in rows
        ],
    }


def _safe_call(fn: Any, fallback: Any) -> Any:
    try:
        return fn()
    except Exception:
        return fallback


def _qualified_table(name: str) -> str:
    return databricks_sql.qualified_uc_name(name)


def _qualified_schema() -> str:
    catalog = os.environ.get("BV_CATALOG", "brickvision")
    schema = os.environ.get("BV_SCHEMA", "brickvision")
    return ".".join(
        (
            databricks_sql.quote_identifier(catalog),
            databricks_sql.quote_identifier(schema),
        )
    )


def _table_states(names: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    try:
        rows = databricks_sql.query_sql_statement_rows(
            f"SHOW TABLES IN {_qualified_schema()}"
        )
    except Exception as exc:
        return {
            name: {
                "exists": False,
                "row_count": None,
                "status": "unknown",
                "message": f"Could not inspect table: {type(exc).__name__}",
            }
            for name in names
        }

    available = {
        str(value).lower()
        for row in rows
        for value in row
        if isinstance(value, str)
    }

    states: dict[str, dict[str, Any]] = {}
    for name in names:
        exists = name.lower() in available
        row_count: int | None = None
        if exists:
            try:
                count_rows = databricks_sql.query_sql_statement_rows(
                    f"SELECT COUNT(*) FROM {_qualified_table(name)}"
                )
                row_count = int(count_rows[0][0]) if count_rows else 0
            except Exception:
                row_count = None
        states[name] = {
            "exists": exists,
            "row_count": row_count,
            "status": "ready" if exists else "missing",
            "message": "Available" if exists else "Not instrumented yet",
        }
    return states


def _model_config() -> list[dict[str, str]]:
    configured: list[dict[str, str]] = []
    env_to_role = {
        "LLM_GENERAL_TASKS": "general_tasks",
        "LLM_EMBEDDING_TASKS": "embedding_tasks",
    }
    defaults = {
        "LLM_GENERAL_TASKS": "databricks-qwen3-next-80b-a3b-instruct",
        "LLM_EMBEDDING_TASKS": "databricks-qwen3-embedding-0-6b",
    }
    for env_name, role in env_to_role.items():
        endpoint = os.environ.get(env_name, defaults.get(env_name, "")).strip()
        configured.append(
            {
                "role": role,
                "env_var": env_name,
                "endpoint": endpoint,
                "status": "configured" if endpoint else "not_configured",
            }
        )
    return configured


def _model_usage_summary(
    model_ledger_exists: bool,
    system_model_serving: dict[str, Any],
) -> dict[str, Any]:
    if system_model_serving.get("status") == "ready":
        return {
            "status": "ready",
            "source": "system.billing.usage",
            "call_count": system_model_serving.get("record_count"),
            "input_tokens": None,
            "output_tokens": None,
            "token_usage_quantity": system_model_serving.get("token_usage_quantity"),
            "usage_quantity": system_model_serving.get("usage_quantity"),
            "endpoint_count": system_model_serving.get("endpoint_count"),
            "estimated_cost_usd": None,
            "message": (
                "Databricks billing system table reports model-serving TOKEN "
                "usage, but it does not split prompt/completion tokens."
            ),
        }
    if not model_ledger_exists:
        return {
            "status": "not_available",
            "source": "none",
            "call_count": None,
            "input_tokens": None,
            "output_tokens": None,
            "token_usage_quantity": None,
            "usage_quantity": None,
            "endpoint_count": None,
            "estimated_cost_usd": None,
        }
    try:
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT
              COUNT(*),
              COALESCE(SUM(input_tokens), 0),
              COALESCE(SUM(output_tokens), 0),
              COALESCE(SUM(estimated_cost_usd), 0)
            FROM {_qualified_table('model_invocation_ledger')}
            """
        )
        row = rows[0] if rows else [0, 0, 0, 0]
        return {
            "status": "ready",
            "source": "model_invocation_ledger",
            "call_count": int(row[0]),
            "input_tokens": int(row[1]),
            "output_tokens": int(row[2]),
            "token_usage_quantity": int(row[1]) + int(row[2]),
            "usage_quantity": None,
            "endpoint_count": None,
            "estimated_cost_usd": float(row[3]),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _model_observability_gaps(
    model_ledger_exists: bool,
    system_llm_usage_ready: bool,
) -> list[str]:
    gaps: list[str] = []
    if not system_llm_usage_ready:
        gaps.append("Databricks system.billing.usage model-serving rows are unavailable")
    if not model_ledger_exists:
        gaps.append("model_invocation_ledger table is missing for BrickVision attribution")
        gaps.append("FMS chat calls do not persist endpoint role, latency, or failure reason")
        gaps.append("Embedding calls expose budget estimates in-process but not durable per-call telemetry")
    if not gaps:
        return []
    return gaps


def _model_attribution_summary(model_ledger_exists: bool) -> dict[str, Any]:
    if not model_ledger_exists:
        return {
            "status": "missing",
            "source": "model_invocation_ledger",
            "rows": [],
        }
    try:
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT
              feature,
              model_role,
              endpoint,
              status,
              COUNT(*) AS invocation_count,
              CAST(COALESCE(AVG(latency_ms), 0) AS BIGINT) AS avg_latency_ms,
              CAST(COALESCE(SUM(total_tokens), 0) AS BIGINT) AS total_tokens
            FROM {databricks_sql.qualified_uc_name('model_invocation_ledger')}
            WHERE observed_at_ms >= (CAST(unix_millis(current_timestamp()) AS BIGINT) - 604800000)
            GROUP BY feature, model_role, endpoint, status
            ORDER BY invocation_count DESC
            LIMIT 20
            """
        )
    except Exception as exc:
        return {
            "status": "unavailable",
            "source": "model_invocation_ledger",
            "message": str(exc),
            "rows": [],
        }
    return {
        "status": "ready",
        "source": "model_invocation_ledger",
        "rows": [
            {
                "feature": str(row[0]),
                "model_role": str(row[1]),
                "endpoint": str(row[2]),
                "status": str(row[3]),
                "invocation_count": int(row[4]),
                "avg_latency_ms": int(row[5]),
                "total_tokens": int(row[6]),
            }
            for row in rows
        ],
    }


def _usage_gaps(
    table_state: dict[str, dict[str, Any]],
    system_billing_ready: bool,
) -> list[str]:
    gaps: list[str] = []
    if not system_billing_ready:
        gaps.append("system.billing.usage is unavailable")
    if not table_state["budget_ledger_app"]["exists"]:
        gaps.append("budget_ledger_app is missing for BrickVision app attribution")
    if not table_state["budget_ledger_indexer"]["exists"]:
        gaps.append("budget_ledger_indexer is missing for BrickVision indexer attribution")
    if not table_state["model_invocation_ledger"]["exists"]:
        gaps.append("model_invocation_ledger is missing for prompt/agent attribution")
    return gaps


def _databricks_system_usage() -> dict[str, Any]:
    return {
        "billing": _system_billing_summary(),
        "model_serving": _system_model_serving_usage(),
        "jobs": _system_jobs_summary(),
        "queries": _system_query_summary(),
        "audit": _system_audit_summary(),
        "lookback_days": 30,
    }


def _system_billing_summary() -> dict[str, Any]:
    available = _system_table_available("system.billing", "usage")
    if not available["available"]:
        return {"status": "unavailable", "message": available["message"], "rows": []}
    return {
        "status": "ready",
        "source": "system.billing.usage",
        "message": "Billing system table is available for DBU, token, and feature usage.",
        "rows": _system_feature_rows(),
    }


def _system_model_serving_usage() -> dict[str, Any]:
    available = _system_table_available("system.billing", "usage")
    endpoints = _configured_endpoints()
    if not available["available"]:
        return {
            "status": "unavailable",
            "source": "system.billing.usage",
            "message": available["message"],
            "rows": [],
        }
    return {
        "status": "ready",
        "source": "system.billing.usage",
        "record_count": None,
        "usage_quantity": None,
        "token_usage_quantity": None,
        "endpoint_count": len(endpoints),
        "message": (
            "Model-serving usage is available through billing records. "
            "The overview avoids account-wide aggregates; use a filtered detail query "
            "for per-endpoint token quantities."
        ),
        "rows": [
            {
                "endpoint_name": endpoint,
                "usage_type": "TOKEN/COMPUTE_TIME",
                "sku_name": "system.billing.usage",
                "record_count": None,
                "usage_quantity": None,
            }
            for endpoint in endpoints
        ],
    }


def _system_jobs_summary() -> dict[str, Any]:
    available = _system_table_available("system.lakeflow", "job_run_timeline")
    if not available["available"]:
        return {"status": "unavailable", "message": available["message"], "rows": []}
    return {
        "status": "ready",
        "source": "system.lakeflow.job_run_timeline",
        "message": "Lakeflow job run system table is available.",
        "rows": [],
    }


def _system_query_summary() -> dict[str, Any]:
    available = _system_table_available("system.query", "history")
    if not available["available"]:
        return {"status": "unavailable", "message": available["message"], "rows": []}
    return {
        "status": "ready",
        "source": "system.query.history",
        "lookback_days": 1,
        "message": "SQL query history system table is available.",
        "rows": [],
    }


def _system_audit_summary() -> dict[str, Any]:
    available = _system_table_available("system.access", "audit")
    if not available["available"]:
        return {"status": "unavailable", "message": available["message"], "rows": []}
    return {
        "status": "ready",
        "source": "system.access.audit",
        "lookback_days": 1,
        "message": "Audit system table is available.",
        "rows": [],
    }


def _system_table_available(schema: str, table: str) -> dict[str, Any]:
    try:
        rows = databricks_sql.query_sql_statement_rows(
            f"SHOW TABLES IN {schema} LIKE {databricks_sql.sql_string_literal(table)}"
        )
    except Exception as exc:
        return {"available": False, "message": str(exc)}
    return {"available": bool(rows), "message": "" if rows else f"{schema}.{table} missing"}


def _system_feature_rows() -> list[dict[str, Any]]:
    return [
        {
            "billing_origin_product": product,
            "usage_type": usage_type,
            "record_count": None,
            "usage_quantity": None,
        }
        for product, usage_type in (
            ("MODEL_SERVING", "TOKEN"),
            ("MODEL_SERVING", "COMPUTE_TIME"),
            ("JOBS", "COMPUTE_TIME"),
            ("SQL", "COMPUTE_TIME"),
            ("APPS", "COMPUTE_TIME"),
            ("VECTOR_SEARCH", "COMPUTE_TIME"),
            ("LAKEBASE", "COMPUTE_TIME/STORAGE_SPACE"),
        )
    ]


def _configured_endpoints() -> list[str]:
    endpoints = {
        model["endpoint"]
        for model in _model_config()
        if model["status"] == "configured" and model["endpoint"]
    }
    return sorted(endpoints)


def _configured_endpoint_filter() -> str:
    return ", ".join(
        databricks_sql.sql_string_literal(endpoint)
        for endpoint in _configured_endpoints()
    )


def _bounded_days(days: int) -> int:
    return max(1, min(int(days), 30))


def _sql_string_list(values: list[str]) -> str:
    return ", ".join(databricks_sql.sql_string_literal(value) for value in values)


def _brickvision_job_names() -> list[str]:
    names = {
        os.environ.get("BV_INDEXER_JOB_NAME", "bv_capability_indexer").strip()
        or "bv_capability_indexer",
        os.environ.get("BV_EVALUATION_JOB_NAME", "bv_evaluation_scorers").strip()
        or "bv_evaluation_scorers",
        "bv_workspace_kg_refresh",
    }
    return sorted(names)


def _budget_namespaces(exists: bool) -> list[dict[str, Any]]:
    if not exists:
        return []
    try:
        rows = databricks_sql.query_sql_statement_rows(
            f"SELECT namespace, ledger_table FROM {_qualified_table('budget_namespaces')}"
        )
    except Exception:
        return []
    return [
        {
            "namespace": str(row[0]),
            "ledger_table": str(row[1]) if row[1] is not None else "",
        }
        for row in rows
    ]


def _proof_counts(table_exists: bool) -> dict[str, int]:
    if not table_exists:
        return {}
    try:
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT status, COUNT(*)
            FROM {_qualified_table('workspace_usecase_tool_proofs')}
            GROUP BY status
            """
        )
    except Exception:
        return {}
    return {str(row[0]): int(row[1]) for row in rows}


def _entity_index_name() -> str:
    catalog = os.environ.get("BV_CATALOG", "")
    schema = os.environ.get("BV_SCHEMA", "brickvision")
    return f"{catalog}.{schema}.entity_index" if catalog else ""


__all__ = [
    "get_lakeflow_jobs_detail",
    "get_model_serving_usage_detail",
    "get_observability_overview",
    "get_sql_queries_detail",
]
