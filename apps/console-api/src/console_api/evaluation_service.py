"""Evaluation read model for MLflow/Unity Catalog evaluation datasets."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from . import databricks_sql
from .evaluation_events import list_recent_evaluation_events

EVALUATION_WORKFLOWS = (
    "capability_graph",
    "hipporag2_retrieval",
    "workspace_context",
    "usecase_lifecycle",
    "skill_execution",
    "platform_cost",
)

CORE_EXPECTATION_KEYS = (
    "expected_facts",
    "expected_response",
    "guidelines",
    "expected_retrieved_context",
)


def get_evaluation_overview(*, user_id: str) -> dict[str, Any]:
    """Return the standalone Evaluation page payload."""

    del user_id
    registry_state = _registry_state()
    datasets = _load_registered_datasets() if registry_state["status"] == "ready" else []
    recent_events = _safe_recent_events()
    scorer_runs = _safe_latest_scorer_runs()
    live_quality = _safe_live_quality()
    scorer_by_workflow = {
        str(run.get("workflow") or ""): run
        for run in scorer_runs
        if run.get("workflow")
    }
    workflows = [
        _workflow_row("capability_graph", "Capability Graph snapshot quality"),
        _workflow_row("hipporag2_retrieval", "HippoRAG2 retrieval and grounded answers"),
        _workflow_row("workspace_context", "Workspace claims and build suggestions"),
        _workflow_row("usecase_lifecycle", "Usecase candidate-to-go/no-go lifecycle"),
        _workflow_row("skill_execution", "SQL, PySpark, ML, and AI tool proofs"),
        _workflow_row("platform_cost", "Reliability, latency, and cost controls"),
    ]
    for workflow in workflows:
        matching = [
            item for item in datasets if item.get("workflow") == workflow["workflow"]
        ]
        record_count = sum(int(item.get("record_count") or 0) for item in matching)
        workflow["dataset_count"] = len(matching)
        workflow["record_count"] = record_count
        latest_scorer_run = scorer_by_workflow.get(str(workflow["workflow"]))
        workflow["latest_scorer_run"] = latest_scorer_run
        if latest_scorer_run:
            workflow["status"] = str(latest_scorer_run.get("status") or "unknown")
        else:
            workflow["status"] = "ready" if record_count else "needs_dataset"
    evaluation_categories = _evaluation_categories(
        workflows=workflows,
        scorer_runs=scorer_runs,
        live_quality=live_quality,
    )

    return {
        "status": "ready",
        "scope": "brickvision_evaluation",
        "links": _evaluation_links(),
        "summary": {
            "dataset_count": len(datasets),
            "record_count": sum(int(item.get("record_count") or 0) for item in datasets),
            "workflow_count": len({item.get("workflow") for item in datasets}),
            "mlflow_experiment_id": os.environ.get("BV_MLFLOW_EVALUATION_EXPERIMENT_ID", ""),
            "registry_status": registry_state["status"],
            "recent_event_count": len(recent_events),
            "live_trace_count_24h": sum(int(row.get("event_count") or 0) for row in live_quality),
            "latest_scorer_run_count": len(scorer_runs),
            "latest_scorer_pass_count": len(
                [run for run in scorer_runs if run.get("status") == "passed"]
            ),
            "latest_scorer_fail_count": len(
                [run for run in scorer_runs if run.get("status") == "failed"]
            ),
            "overall_status": _overall_status(evaluation_categories),
            "category_pass_count": len(
                [row for row in evaluation_categories if row.get("status") == "passed"]
            ),
            "category_warning_count": len(
                [row for row in evaluation_categories if row.get("status") == "warning"]
            ),
            "category_fail_count": len(
                [row for row in evaluation_categories if row.get("status") == "failed"]
            ),
            "category_not_scored_count": len(
                [row for row in evaluation_categories if row.get("status") == "not_scored"]
            ),
        },
        "contract": {
            "storage": "Unity Catalog MLflow evaluation datasets",
            "required_fields": ["inputs"],
            "optional_fields": ["expectations", "source", "tags"],
            "expectation_reserved_keys": list(CORE_EXPECTATION_KEYS),
            "supported_sources": ["trace", "human", "document", "synthetic"],
            "max_records_per_dataset": 2000,
            "next_action": (
                "Curate records in config/evaluation and sync them with "
                "scripts/sync_mlflow_eval_datasets.py."
            ),
        },
        "workflows": workflows,
        "evaluation_categories": evaluation_categories,
        "datasets": datasets,
        "live_quality": live_quality,
        "recent_events": recent_events,
        "latest_scorer_runs": scorer_runs,
        "registry": registry_state,
    }


def _evaluation_links() -> dict[str, str]:
    host = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/")
    experiment_id = os.environ.get("BV_MLFLOW_EVALUATION_EXPERIMENT_ID", "").strip()
    experiment_url = (
        f"{host}/ml/experiments/{experiment_id}"
        if host and experiment_id
        else ""
    )
    return {
        "databricks_host": host,
        "mlflow_experiment_id": experiment_id,
        "mlflow_experiment_url": experiment_url,
    }


def list_evaluation_dataset_records(
    *,
    user_id: str,
    dataset_id: str,
    limit: int = 25,
) -> dict[str, Any]:
    """Return a bounded preview of records from a registered MLflow eval dataset."""

    del user_id
    safe_limit = max(1, min(int(limit), 100))
    dataset = _load_registered_dataset(dataset_id)
    if not dataset:
        return {
            "status": "not_found",
            "dataset_id": dataset_id,
            "records": [],
            "message": "Evaluation dataset is not registered in BrickVision.",
        }
    table_name = str(dataset.get("uc_table_name") or "")
    try:
        records = _load_dataset_records(table_name, limit=safe_limit)
    except Exception as exc:
        return {
            "status": "unavailable",
            "dataset_id": dataset_id,
            "dataset": dataset,
            "records": [],
            "message": f"Could not read evaluation dataset records: {type(exc).__name__}",
        }
    return {
        "status": "ready",
        "dataset_id": dataset_id,
        "dataset": dataset,
        "records": records,
        "limit": safe_limit,
    }


def _workflow_row(workflow: str, title: str) -> dict[str, Any]:
    return {
        "workflow": workflow,
        "title": title,
        "dataset_count": 0,
        "record_count": 0,
        "status": "needs_dataset",
    }


def _evaluation_categories(
    *,
    workflows: list[dict[str, Any]],
    scorer_runs: list[dict[str, Any]],
    live_quality: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scorer_by_workflow = {
        str(run.get("workflow") or ""): run
        for run in scorer_runs
        if run.get("workflow")
    }
    live_by_key = {
        (str(row.get("workflow") or ""), str(row.get("event_kind") or "")): row
        for row in live_quality
    }
    workflow_by_id = {str(row.get("workflow") or ""): row for row in workflows}
    rows = [
        _scorer_category(
            category_id="capability_retrieval",
            title="Capability Retrieval",
            workflow="capability_graph",
            scope="Curated",
            scorer=scorer_by_workflow.get("capability_graph"),
            workflow_row=workflow_by_id.get("capability_graph"),
        ),
        _scorer_category(
            category_id="hipporag2_grounding",
            title="HippoRAG2 Grounding",
            workflow="hipporag2_retrieval",
            scope="Curated + MLflow",
            scorer=scorer_by_workflow.get("hipporag2_retrieval"),
            workflow_row=workflow_by_id.get("hipporag2_retrieval"),
        ),
        _live_category(
            category_id="live_rag_quality",
            title="Live RAG Quality",
            workflow="hipporag2_retrieval",
            event_kind="rag_answer",
            live_row=live_by_key.get(("hipporag2_retrieval", "rag_answer")),
        ),
        _scorer_category(
            category_id="workspace_context",
            title="Workspace Context",
            workflow="workspace_context",
            scope="Curated",
            scorer=scorer_by_workflow.get("workspace_context"),
            workflow_row=workflow_by_id.get("workspace_context"),
        ),
        _scorer_category(
            category_id="usecase_lifecycle",
            title="Usecase Lifecycle",
            workflow="usecase_lifecycle",
            scope="Curated",
            scorer=scorer_by_workflow.get("usecase_lifecycle"),
            workflow_row=workflow_by_id.get("usecase_lifecycle"),
        ),
        _scorer_category(
            category_id="skill_execution",
            title="Skill Execution",
            workflow="skill_execution",
            scope="Curated + Tool Proofs",
            scorer=scorer_by_workflow.get("skill_execution"),
            workflow_row=workflow_by_id.get("skill_execution"),
        ),
        _scorer_category(
            category_id="platform_cost_latency",
            title="Platform Cost / Latency",
            workflow="platform_cost",
            scope="Curated + Live",
            scorer=scorer_by_workflow.get("platform_cost"),
            workflow_row=workflow_by_id.get("platform_cost"),
        ),
    ]
    return rows


def _scorer_category(
    *,
    category_id: str,
    title: str,
    workflow: str,
    scope: str,
    scorer: dict[str, Any] | None,
    workflow_row: dict[str, Any] | None,
) -> dict[str, Any]:
    record_count = int((workflow_row or {}).get("record_count") or 0)
    dataset_count = int((workflow_row or {}).get("dataset_count") or 0)
    if not scorer:
        status = "not_scored" if record_count else "warning"
        reason_summary = (
            "Dataset is registered but no scorer run has been recorded."
            if record_count
            else "No evaluation dataset records are registered for this category."
        )
        return _category_row(
            category_id=category_id,
            title=title,
            workflow=workflow,
            scope=scope,
            status=status,
            numerator=0,
            denominator=record_count,
            evidence=f"{record_count} records across {dataset_count} datasets",
            reason_summary=reason_summary,
            reason_details=[
                reason_summary,
                "Run the evaluation scorer job after syncing MLflow evaluation datasets.",
            ],
            next_action="Run `scripts/run_evaluation_scorers.py` or the Databricks evaluation job.",
            created_at_ms=0,
        )
    scorer_results = [
        item for item in scorer.get("scorer_results", []) if isinstance(item, dict)
    ]
    passed = [item for item in scorer_results if item.get("status") == "passed"]
    failed = [item for item in scorer_results if item.get("status") == "failed"]
    observed = [item for item in scorer_results if item.get("status") == "observed"]
    status = str(scorer.get("status") or "not_scored")
    if status == "passed" and observed and not failed:
        status = "warning"
    denominator = len(scorer_results) or record_count
    numerator = len(passed) if scorer_results else (record_count if status == "passed" else 0)
    reason_details = _scorer_reason_details(scorer=scorer, scorer_results=scorer_results)
    return _category_row(
        category_id=category_id,
        title=title,
        workflow=workflow,
        scope=scope,
        status=status,
        numerator=numerator,
        denominator=denominator,
        evidence=(
            f"{numerator}/{denominator} gates passed"
            if scorer_results
            else f"{record_count} curated records"
        ),
        reason_summary=_reason_summary_for_status(status=status, failed=failed, observed=observed),
        reason_details=reason_details,
        next_action=_next_action_for_status(status=status),
        created_at_ms=int(scorer.get("created_at_ms") or 0),
        mlflow_run_id=str(scorer.get("mlflow_run_id") or ""),
        mlflow_trace_id=str(scorer.get("mlflow_trace_id") or ""),
    )


def _live_category(
    *,
    category_id: str,
    title: str,
    workflow: str,
    event_kind: str,
    live_row: dict[str, Any] | None,
) -> dict[str, Any]:
    minimum_live_events = 10
    if not live_row:
        return _category_row(
            category_id=category_id,
            title=title,
            workflow=workflow,
            scope="Live",
            status="not_scored",
            numerator=0,
            denominator=0,
            evidence="No live events in the current window",
            reason_summary="No live events were observed in the current window.",
            reason_details=[
                "Live quality needs runtime traffic before it can provide population denominators.",
                "Use Knowledge Ask or scheduled live probes to populate rag_answer events.",
            ],
            next_action="Generate representative Knowledge Ask traffic, then refresh Evaluation.",
            created_at_ms=0,
        )
    event_count = int(live_row.get("event_count") or 0)
    traced_count = int(live_row.get("traced_count") or 0)
    failure_count = int(live_row.get("failure_count") or 0)
    trace_coverage = float(live_row.get("trace_coverage") or 0.0)
    status = "passed"
    reason_summary = "Live RAG events are traced and successful enough for inspection."
    next_action = "Continue collecting live traffic and sampling trace-backed records."
    if event_count == 0:
        status = "not_scored"
        reason_summary = "No live events were observed in the current window."
        next_action = "Generate representative Knowledge Ask traffic."
    elif failure_count > 0:
        status = "failed"
        reason_summary = f"{failure_count} live RAG events failed or were blocked."
        next_action = "Inspect recent failed events and their reason codes."
    elif event_count < minimum_live_events:
        status = "warning"
        reason_summary = (
            f"Only {event_count} live rag_answer events were observed; "
            f"minimum useful denominator is {minimum_live_events}."
        )
        next_action = "Collect more representative Knowledge Ask traffic before treating live quality as passed."
    elif trace_coverage < 0.8:
        status = "warning"
        reason_summary = (
            f"Trace coverage is {trace_coverage:.0%}: "
            f"{traced_count} of {event_count} rag_answer events have MLflow traces."
        )
        next_action = (
            "Ensure BV_MLFLOW_EVALUATION_EXPERIMENT_ID is set in every API runtime "
            "and restart stale processes."
        )
    return _category_row(
        category_id=category_id,
        title=title,
        workflow=workflow,
        scope="Live",
        status=status,
        numerator=traced_count,
        denominator=event_count,
        evidence=f"{traced_count}/{event_count} traced",
        reason_summary=reason_summary,
        reason_details=[
            reason_summary,
            f"Minimum useful live denominator: {minimum_live_events} events.",
            f"Success rate: {float(live_row.get('success_rate') or 0.0):.0%}.",
            f"Average latency: {float(live_row.get('avg_latency_ms') or 0.0):.0f} ms.",
            "Untraced answers cannot be inspected in MLflow or replayed into live eval datasets.",
        ],
        next_action=next_action,
        created_at_ms=int(live_row.get("latest_event_at_ms") or 0),
    )


def _category_row(
    *,
    category_id: str,
    title: str,
    workflow: str,
    scope: str,
    status: str,
    numerator: int,
    denominator: int,
    evidence: str,
    reason_summary: str,
    reason_details: list[str],
    next_action: str,
    created_at_ms: int,
    mlflow_run_id: str = "",
    mlflow_trace_id: str = "",
) -> dict[str, Any]:
    return {
        "id": category_id,
        "title": title,
        "workflow": workflow,
        "scope": scope,
        "status": _category_status(status),
        "numerator": int(numerator),
        "denominator": int(denominator),
        "evidence": evidence,
        "reason_summary": reason_summary,
        "reason_details": reason_details,
        "next_action": next_action,
        "created_at_ms": int(created_at_ms),
        "mlflow_run_id": mlflow_run_id,
        "mlflow_trace_id": mlflow_trace_id,
    }


def _scorer_reason_details(
    *, scorer: dict[str, Any], scorer_results: list[dict[str, Any]]
) -> list[str]:
    details: list[str] = []
    for result in scorer_results:
        label = str(result.get("label") or result.get("name") or "scorer")
        status = str(result.get("status") or "unknown")
        value = result.get("value")
        threshold = result.get("threshold")
        details.append(f"{label}: {status}; value={value}, threshold={threshold}.")
    reason_codes = [str(code) for code in scorer.get("reason_codes", []) if code]
    if reason_codes:
        details.append("Reason codes: " + ", ".join(reason_codes) + ".")
    if not details:
        details.append("No scorer-level details were recorded for this run.")
    return details


def _reason_summary_for_status(
    *, status: str, failed: list[dict[str, Any]], observed: list[dict[str, Any]]
) -> str:
    if status == "failed":
        names = ", ".join(str(item.get("label") or item.get("name")) for item in failed[:3])
        return f"One or more gates failed: {names or 'see scorer details'}."
    if status == "warning":
        if observed:
            return "Some gates are observed without enforced thresholds, so this is not a clean pass."
        return "The category has useful data but does not meet full pass criteria."
    if status == "not_scored":
        return "This category has not been scored yet."
    return "All enforced gates passed in the latest scorer run."


def _next_action_for_status(*, status: str) -> str:
    if status == "failed":
        return "Open scorer details, inspect failed gates, and fix the underlying workflow."
    if status == "warning":
        return "Add thresholds or runtime coverage so this category can graduate to pass/fail."
    if status == "not_scored":
        return "Run the evaluation scorer job."
    return "No blocking action. Keep collecting live and curated evidence."


def _category_status(status: str) -> str:
    normalized = status.lower().strip()
    if normalized in {"passed", "pass", "observed", "ready"}:
        return "passed"
    if normalized in {"failed", "fail", "blocked"}:
        return "failed"
    if normalized in {"not_scored", "not scored", "not_run", "needs_dataset"}:
        return "not_scored"
    return "warning"


def _overall_status(categories: list[dict[str, Any]]) -> str:
    statuses = {str(row.get("status") or "") for row in categories}
    if "failed" in statuses:
        return "failed"
    if "warning" in statuses:
        return "warning"
    if "not_scored" in statuses:
        return "warning"
    return "passed"


def _registry_state() -> dict[str, Any]:
    try:
        _ensure_evaluation_dataset_registry()
    except Exception as exc:
        return {
            "status": "unavailable",
            "message": f"Evaluation registry is not available: {type(exc).__name__}",
            "table": databricks_sql.qualified_uc_name("evaluation_datasets"),
        }
    return {
        "status": "ready",
        "table": databricks_sql.qualified_uc_name("evaluation_datasets"),
    }


def _safe_recent_events() -> list[dict[str, Any]]:
    try:
        return list_recent_evaluation_events(limit=20)
    except Exception:
        return []


def _safe_latest_scorer_runs() -> list[dict[str, Any]]:
    try:
        return _load_latest_scorer_runs()
    except Exception:
        return []


def _safe_live_quality() -> list[dict[str, Any]]:
    try:
        return _load_live_quality(window_hours=24)
    except Exception:
        return []


def _load_live_quality(*, window_hours: int) -> list[dict[str, Any]]:
    since_ms = _current_time_ms() - (max(1, int(window_hours)) * 60 * 60 * 1000)
    rows = databricks_sql.query_sql_statement_rows(
        f"""
        SELECT
          workflow,
          event_kind,
          COUNT(*) AS event_count,
          SUM(CASE WHEN status IN ('observed', 'passed') THEN 1 ELSE 0 END) AS success_count,
          SUM(CASE WHEN status IN ('failed', 'blocked') THEN 1 ELSE 0 END) AS failure_count,
          SUM(CASE WHEN mlflow_trace_id IS NOT NULL AND mlflow_trace_id <> '' THEN 1 ELSE 0 END) AS traced_count,
          AVG(CAST(get_json_object(metrics_json, '$.latency_ms') AS DOUBLE)) AS avg_latency_ms,
          MAX(created_at_ms) AS latest_event_at_ms
        FROM {databricks_sql.qualified_uc_name("evaluation_events")}
        WHERE event_kind IN ('rag_search', 'rag_answer', 'usecase_stage', 'tool_proof')
          AND created_at_ms >= {since_ms}
        GROUP BY workflow, event_kind
        ORDER BY workflow, event_kind
        """
    )
    live_rows: list[dict[str, Any]] = []
    for row in rows:
        event_count = int(row[2] or 0)
        success_count = int(row[3] or 0)
        failure_count = int(row[4] or 0)
        traced_count = int(row[5] or 0)
        live_rows.append(
            {
                "workflow": str(row[0]),
                "event_kind": str(row[1]),
                "window_hours": int(window_hours),
                "event_count": event_count,
                "success_count": success_count,
                "failure_count": failure_count,
                "traced_count": traced_count,
                "success_rate": _ratio(success_count, event_count),
                "failure_rate": _ratio(failure_count, event_count),
                "trace_coverage": _ratio(traced_count, event_count),
                "avg_latency_ms": round(float(row[6] or 0.0), 2),
                "latest_event_at_ms": int(row[7] or 0),
                "dataset_source": "live_events",
            }
        )
    return live_rows


def _load_latest_scorer_runs() -> list[dict[str, Any]]:
    rows = databricks_sql.query_sql_statement_rows(
        f"""
        SELECT
          workflow,
          status,
          subject_id,
          mlflow_run_id,
          mlflow_trace_id,
          mlflow_dataset_name,
          metrics_json,
          outputs_json,
          evidence_json,
          reason_codes_json,
          created_at_ms
        FROM (
          SELECT
            *,
            ROW_NUMBER() OVER (PARTITION BY workflow ORDER BY created_at_ms DESC) AS rn
          FROM {databricks_sql.qualified_uc_name("evaluation_events")}
          WHERE event_kind = 'scorer_run'
        )
        WHERE rn = 1
        ORDER BY workflow
        """
    )
    return [_scorer_run_from_row(row) for row in rows]


def _scorer_run_from_row(row: list[Any]) -> dict[str, Any]:
    outputs = _decode_json_object(row[7])
    evidence = _decode_json_list(row[8])
    scorer_results = outputs.get("scorer_results")
    if not isinstance(scorer_results, list):
        scorer_results = evidence
    return {
        "workflow": str(row[0]),
        "status": str(row[1]),
        "subject_id": str(row[2]),
        "mlflow_run_id": str(row[3] or ""),
        "mlflow_trace_id": str(row[4] or ""),
        "mlflow_dataset_name": str(row[5] or ""),
        "metrics": _decode_json_object(row[6]),
        "quality_gates": _decode_json_object(outputs.get("quality_gates")),
        "scorer_results": [
            item for item in scorer_results if isinstance(item, dict)
        ],
        "reason_codes": _decode_json_list(row[9]),
        "created_at_ms": int(row[10] or 0),
    }


def _ensure_evaluation_dataset_registry() -> None:
    databricks_sql.execute_sql_statement(
        f"""
        CREATE TABLE IF NOT EXISTS {databricks_sql.qualified_uc_name("evaluation_datasets")} (
          dataset_id STRING NOT NULL,
          name STRING NOT NULL,
          workflow STRING NOT NULL,
          uc_table_name STRING NOT NULL,
          mlflow_experiment_id STRING,
          description STRING,
          quality_gates_json STRING,
          tags_json STRING,
          source_kinds_json STRING,
          created_by STRING,
          created_at_ms BIGINT NOT NULL,
          updated_at_ms BIGINT NOT NULL
        )
        USING DELTA
        TBLPROPERTIES ('brickvision.role' = 'evaluation_datasets')
        """
    )


def _load_registered_datasets() -> list[dict[str, Any]]:
    rows = databricks_sql.query_sql_statement_rows(
        f"""
        SELECT
          dataset_id,
          name,
          workflow,
          uc_table_name,
          mlflow_experiment_id,
          description,
          quality_gates_json,
          tags_json,
          source_kinds_json,
          created_by,
          created_at_ms,
          updated_at_ms
        FROM (
          SELECT
            *,
            ROW_NUMBER() OVER (PARTITION BY dataset_id ORDER BY updated_at_ms DESC) AS rn
          FROM {databricks_sql.qualified_uc_name("evaluation_datasets")}
        )
        WHERE rn = 1
        ORDER BY workflow, name
        """
    )
    datasets = [_dataset_from_row(row) for row in rows]
    for dataset in datasets:
        dataset["record_count"] = _safe_record_count(str(dataset.get("uc_table_name") or ""))
    for dataset in datasets:
        dataset["workflow_status"] = (
            "ready" if int(dataset.get("record_count") or 0) > 0 else "needs_records"
        )
    return datasets


def _load_registered_dataset(dataset_id: str) -> dict[str, Any] | None:
    for dataset in _load_registered_datasets():
        if dataset.get("dataset_id") == dataset_id:
            return dataset
    return None


def _dataset_from_row(row: list[Any]) -> dict[str, Any]:
    return {
        "dataset_id": str(row[0]),
        "name": str(row[1]),
        "workflow": str(row[2]),
        "uc_table_name": str(row[3]),
        "mlflow_experiment_id": str(row[4] or ""),
        "description": str(row[5] or ""),
        "quality_gates": _decode_json_object(row[6]),
        "tags": _decode_json_object(row[7]),
        "source_kinds": _decode_json_list(row[8]),
        "created_by": str(row[9] or ""),
        "created_at_ms": int(row[10] or 0),
        "updated_at_ms": int(row[11] or 0),
    }


def _safe_record_count(uc_table_name: str) -> int:
    try:
        rows = databricks_sql.query_sql_statement_rows(
            f"SELECT COUNT(*) FROM {_qualified_uc_table_name(uc_table_name)}"
        )
    except Exception:
        return 0
    return int(rows[0][0]) if rows else 0


def _load_dataset_records(uc_table_name: str, *, limit: int) -> list[dict[str, Any]]:
    qualified_table = _qualified_uc_table_name(uc_table_name)
    try:
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT
              dataset_record_id,
              inputs,
              expectations,
              source,
              tags
            FROM {qualified_table}
            ORDER BY dataset_record_id
            LIMIT {int(limit)}
            """
        )
    except Exception:
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT
              inputs,
              expectations,
              source,
              tags
            FROM {qualified_table}
            LIMIT {int(limit)}
            """
        )
        return [
            {
                "dataset_record_id": f"record_{index + 1}",
                "inputs": _decode_maybe_json(row[0]),
                "expectations": _decode_maybe_json(row[1]),
                "source": _decode_maybe_json(row[2]),
                "tags": _decode_maybe_json(row[3]),
            }
            for index, row in enumerate(rows)
        ]
    return [
        {
            "dataset_record_id": str(row[0]),
            "inputs": _decode_maybe_json(row[1]),
            "expectations": _decode_maybe_json(row[2]),
            "source": _decode_maybe_json(row[3]),
            "tags": _decode_maybe_json(row[4]),
        }
        for row in rows
    ]


def _qualified_uc_table_name(uc_table_name: str) -> str:
    parts = [part.strip() for part in uc_table_name.split(".") if part.strip()]
    if len(parts) != 3:
        raise ValueError("Evaluation dataset table must be a three-part UC name")
    return ".".join(databricks_sql.quote_identifier(part) for part in parts)


def _decode_json_object(value: Any) -> dict[str, Any]:
    decoded = _decode_maybe_json(value)
    return decoded if isinstance(decoded, dict) else {}


def _decode_json_list(value: Any) -> list[Any]:
    decoded = _decode_maybe_json(value)
    return decoded if isinstance(decoded, list) else []


def _decode_maybe_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict | list):
        return value
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _current_time_ms() -> int:
    return int(time.time() * 1000)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)
