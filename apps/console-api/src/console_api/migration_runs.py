"""Migration workflow runs owned outside the usecase execution model."""

from __future__ import annotations

import concurrent.futures
import json
import threading
import time
import uuid
from typing import Any

from . import databricks_sql
from .skill_execution_service import execute_skill_for_usecase

_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="bv-migration")
_LOCK = threading.Lock()
_RUNS: dict[str, dict[str, Any]] = {}

WORKFLOW_TYPES = ("assessment", "sql_transpile", "code_convert")


def start_migration_run(
    *,
    user_id: str,
    workflow_type: str,
    usecase_id: str | None = None,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start one migration workflow run and return immediately for UI polling."""

    normalized = workflow_type.strip().lower().replace("-", "_")
    if normalized not in WORKFLOW_TYPES:
        return {
            "status": "invalid_workflow_type",
            "workflow_type": workflow_type,
            "allowed_workflow_types": list(WORKFLOW_TYPES),
            "message": f"Unsupported migration workflow type: {workflow_type}",
        }
    run_id = f"mgr_{uuid.uuid4().hex[:24]}"
    now_ms = _now_ms()
    run = {
        "migration_run_id": run_id,
        "usecase_id": usecase_id or "",
        "workflow_type": normalized,
        "status": "queued",
        "phase": "queued",
        "title": _run_title(normalized),
        "created_by": user_id,
        "created_at_ms": now_ms,
        "updated_at_ms": now_ms,
        "inputs": dict(inputs or {}),
        "result": None,
        "error": None,
        "durable": False,
        "steps": _steps_for_workflow(normalized),
        "next_action": "Migration workflow run is queued.",
    }
    with _LOCK:
        _RUNS[run_id] = run
    _safe_persist_run(run)
    _EXECUTOR.submit(_run_migration_workflow, run_id, user_id)
    return dict(run)


def list_migration_runs(*, user_id: str, usecase_id: str | None = None) -> dict[str, Any]:
    """Return recent migration workflow runs from the sidecar and persisted table."""

    del user_id
    runs = _load_persisted_runs(usecase_id=usecase_id)
    with _LOCK:
        in_memory = [
            dict(run)
            for run in _RUNS.values()
            if not usecase_id or str(run.get("usecase_id") or "") == usecase_id
        ]
    active_ids = {str(run.get("migration_run_id")) for run in in_memory}
    by_id = {
        str(run.get("migration_run_id")): _with_local_liveness(run, active_ids=active_ids)
        for run in runs
    }
    by_id.update({str(run.get("migration_run_id")): run for run in in_memory})
    merged = list(by_id.values())
    merged.sort(key=lambda item: int(item.get("updated_at_ms") or 0), reverse=True)
    return {
        "status": "ready",
        "migration_runs": merged[:50],
        "next_action": (
            "Open a migration run or start a new workflow."
            if merged
            else "No migration workflow runs have been started yet."
        ),
    }


def get_migration_run(*, migration_run_id: str) -> dict[str, Any]:
    """Return one migration workflow run."""

    with _LOCK:
        run = _RUNS.get(migration_run_id)
        if run is not None:
            return dict(run)
    persisted = _load_persisted_runs(migration_run_id=migration_run_id)
    if persisted:
        active_ids: set[str] = set()
        return _with_local_liveness(persisted[0], active_ids=active_ids)
    return {
        "status": "not_found",
        "migration_run_id": migration_run_id,
        "message": "Migration workflow run was not found.",
    }


def _run_migration_workflow(run_id: str, user_id: str) -> None:
    run = get_migration_run(migration_run_id=run_id)
    workflow_type = str(run.get("workflow_type") or "")
    usecase_id = str(run.get("usecase_id") or "")
    inputs = dict(run.get("inputs") if isinstance(run.get("inputs"), dict) else {})
    _update_run(
        run_id,
        status="running",
        phase="assessment" if workflow_type == "assessment" else "convert",
        next_action=f"Running {_run_title(workflow_type)}.",
        step_updates={("assessment" if workflow_type == "assessment" else "convert"): "running"},
    )
    try:
        family = _family_for_workflow(workflow_type)
        result = execute_skill_for_usecase(
            user_id=user_id,
            usecase_id=usecase_id or run_id,
            family=family,
            execution_inputs={**inputs, "standalone_migration_run": not bool(usecase_id)},
        )
    except Exception as exc:  # pragma: no cover - defensive sidecar worker
        _update_run(
            run_id,
            status="failed",
            phase="convert",
            error={"error_kind": type(exc).__name__, "message": str(exc)},
            next_action="Fix migration workflow runtime/configuration and rerun.",
            step_updates={"convert": "failed", "validate": "skipped"},
        )
        return

    completed = str(result.get("status") or "") in {
        "assessment_ready",
        "transpilation_proven",
        "transpilation_completed",
        "code_conversion_completed",
        "code_conversion_submitted",
        "execution_proven",
    }
    _update_run(
        run_id,
        status="completed" if completed else "blocked",
        phase="assessment" if workflow_type == "assessment" else "validate" if completed else "convert",
        result=result,
        next_action=(
            "Conversion completed. Add validation or reconciliation evidence next."
            if completed and workflow_type != "assessment"
            else "Assessment readiness completed. Choose SQL Transpile or Code Convert next."
            if completed
            else str(result.get("message") or "Migration workflow is blocked.")
        ),
        step_updates=_step_updates_for_result(workflow_type=workflow_type, completed=completed),
    )


def _update_run(
    run_id: str,
    *,
    status: str,
    phase: str,
    next_action: str | None = None,
    result: dict[str, Any] | None = None,
    error: dict[str, str] | None = None,
    step_updates: dict[str, str] | None = None,
) -> None:
    with _LOCK:
        run = _RUNS.get(run_id)
        if run is None:
            return
        run["status"] = status
        run["phase"] = phase
        run["updated_at_ms"] = _now_ms()
        if next_action is not None:
            run["next_action"] = next_action
        if result is not None:
            run["result"] = result
        if error is not None:
            run["error"] = error
        if step_updates:
            for step in run.get("steps", []):
                step_id = str(step.get("step_id") or "")
                if step_id in step_updates:
                    step["status"] = step_updates[step_id]
                    step["updated_at_ms"] = _now_ms()
        snapshot = dict(run)
    _safe_persist_run(snapshot)


def _steps_for_workflow(workflow_type: str) -> list[dict[str, Any]]:
    if workflow_type == "assessment":
        return [
            _step("assessment", "pending", "Run Lakebridge Assessment readiness"),
            _step("convert", "pending", "Choose SQL Transpile or Code Convert after assessment"),
            _step("validate", "pending", "Validate or reconcile converted output."),
        ]
    convert_label = "Run SQL Transpile" if workflow_type == "sql_transpile" else "Run Code Convert"
    return [
        _step("assessment", "not_available", "Lakebridge assessment is the next workflow step."),
        _step("convert", "pending", convert_label),
        _step("validate", "pending", "Validate or reconcile converted output."),
    ]


def _step_updates_for_result(*, workflow_type: str, completed: bool) -> dict[str, str]:
    if workflow_type == "assessment":
        return {
            "assessment": "completed" if completed else "blocked",
            "convert": "pending" if completed else "skipped",
            "validate": "pending" if completed else "skipped",
        }
    return {
        "assessment": "not_available",
        "convert": "completed" if completed else "blocked",
        "validate": "pending" if completed else "skipped",
    }


def _step(step_id: str, status: str, label: str) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "status": status,
        "label": label,
        "updated_at_ms": _now_ms(),
    }


def _run_title(workflow_type: str) -> str:
    if workflow_type == "assessment":
        return "Assessment"
    return "SQL Transpile" if workflow_type == "sql_transpile" else "Code Convert"


def _family_for_workflow(workflow_type: str) -> str:
    if workflow_type == "assessment":
        return "Assessment"
    return "Migration" if workflow_type == "sql_transpile" else "Code Convert"


def _with_local_liveness(run: dict[str, Any], *, active_ids: set[str]) -> dict[str, Any]:
    run_id = str(run.get("migration_run_id") or "")
    status = str(run.get("status") or "")
    if status not in {"queued", "running"} or run_id in active_ids:
        return run
    updated = dict(run)
    updated["status"] = "interrupted"
    updated["error"] = {
        "error_kind": "local_sidecar_reloaded",
        "message": (
            "The local sidecar reloaded while this migration workflow was running, "
            "so BrickVision could not capture the final Lakebridge result."
        ),
    }
    updated["next_action"] = (
        "Start a new migration workflow run, or check the Databricks job run directly "
        "if you need the remote output from this interrupted local session."
    )
    for step in updated.get("steps", []):
        if isinstance(step, dict) and step.get("status") == "running":
            step["status"] = "interrupted"
    return updated


def _safe_persist_run(run: dict[str, Any]) -> None:
    try:
        _persist_run(run | {"durable": True})
        with _LOCK:
            if str(run.get("migration_run_id") or "") in _RUNS:
                _RUNS[str(run["migration_run_id"])]["durable"] = True
    except Exception:
        # Local dev can still observe in-process runs when UC persistence is unavailable.
        pass


def _persist_run(run: dict[str, Any]) -> None:
    _ensure_migration_runs_table()
    databricks_sql.execute_sql_statement(
        f"""
        INSERT INTO {databricks_sql.qualified_uc_name("workspace_migration_runs")} (
          migration_run_id,
          usecase_id,
          workflow_type,
          status,
          phase,
          title,
          inputs_json,
          result_json,
          error_json,
          steps_json,
          durable,
          created_by,
          created_at_ms,
          updated_at_ms
        )
        VALUES (
          {databricks_sql.sql_string_literal(str(run.get("migration_run_id", "")))},
          {databricks_sql.sql_string_literal(str(run.get("usecase_id") or ""))},
          {databricks_sql.sql_string_literal(str(run.get("workflow_type", "")))},
          {databricks_sql.sql_string_literal(str(run.get("status", "")))},
          {databricks_sql.sql_string_literal(str(run.get("phase", "")))},
          {databricks_sql.sql_string_literal(str(run.get("title", "")))},
          {databricks_sql.sql_string_literal(json.dumps(run.get("inputs") or {}, sort_keys=True))},
          {databricks_sql.sql_string_literal(json.dumps(run.get("result") or {}, sort_keys=True))},
          {databricks_sql.sql_string_literal(json.dumps(run.get("error") or {}, sort_keys=True))},
          {databricks_sql.sql_string_literal(json.dumps(run.get("steps") or [], sort_keys=True))},
          {str(bool(run.get("durable"))).lower()},
          {databricks_sql.sql_string_literal(str(run.get("created_by", "")))},
          {int(run.get("created_at_ms") or 0)},
          {int(run.get("updated_at_ms") or 0)}
        )
        """
    )


def _ensure_migration_runs_table() -> None:
    databricks_sql.execute_sql_statement(
        f"""
        CREATE TABLE IF NOT EXISTS {databricks_sql.qualified_uc_name("workspace_migration_runs")} (
          migration_run_id STRING NOT NULL,
          usecase_id STRING,
          workflow_type STRING NOT NULL,
          status STRING NOT NULL,
          phase STRING,
          title STRING,
          inputs_json STRING,
          result_json STRING,
          error_json STRING,
          steps_json STRING,
          durable BOOLEAN,
          created_by STRING,
          created_at_ms BIGINT NOT NULL,
          updated_at_ms BIGINT NOT NULL
        )
        USING DELTA
        TBLPROPERTIES ('brickvision.role' = 'workspace_migration_runs')
        """
    )


def _load_persisted_runs(
    *,
    usecase_id: str | None = None,
    migration_run_id: str | None = None,
) -> list[dict[str, Any]]:
    try:
        _ensure_migration_runs_table()
        filters = []
        if usecase_id:
            filters.append(f"usecase_id = {databricks_sql.sql_string_literal(usecase_id)}")
        if migration_run_id:
            filters.append(
                f"migration_run_id = {databricks_sql.sql_string_literal(migration_run_id)}"
            )
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        rows = databricks_sql.query_sql_statement_rows(
            f"""
            SELECT
              migration_run_id,
              usecase_id,
              workflow_type,
              status,
              phase,
              title,
              inputs_json,
              result_json,
              error_json,
              steps_json,
              durable,
              created_by,
              created_at_ms,
              updated_at_ms
            FROM {databricks_sql.qualified_uc_name("workspace_migration_runs")}
            {where}
            ORDER BY updated_at_ms DESC
            LIMIT 100
            """
        )
    except Exception:
        return []
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = _row_to_run(row)
        run_id = str(item.get("migration_run_id") or "")
        if run_id and run_id not in latest:
            latest[run_id] = item
    return list(latest.values())


def _row_to_run(row: list[Any]) -> dict[str, Any]:
    return {
        "migration_run_id": str(row[0] or ""),
        "usecase_id": str(row[1] or ""),
        "workflow_type": str(row[2] or ""),
        "status": str(row[3] or ""),
        "phase": str(row[4] or ""),
        "title": str(row[5] or ""),
        "inputs": _json_object(row[6]),
        "result": _json_object(row[7]) or None,
        "error": _json_object(row[8]) or None,
        "steps": _json_list(row[9]),
        "durable": _bool_value(row[10]),
        "created_by": str(row[11] or ""),
        "created_at_ms": int(row[12] or 0),
        "updated_at_ms": int(row[13] or 0),
    }


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "t", "yes"}


def _now_ms() -> int:
    return int(time.time() * 1000)


__all__ = [
    "get_migration_run",
    "list_migration_runs",
    "start_migration_run",
]
