"""``brickvision evaluation`` — MLflow evaluation operator commands."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib import request


_DEFAULT_JOB_NAME = "bv_evaluation_scorers"


@dataclasses.dataclass(frozen=True, slots=True)
class EvaluationCommandOutcome:
    action: str
    payload: Mapping[str, Any]
    suggested_next_action: str
    exit_code: int

    def to_json(self) -> str:
        return json.dumps(
            {
                "action": self.action,
                "payload": dict(self.payload),
                "suggested_next_action": self.suggested_next_action,
            },
            sort_keys=True,
        )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _is_dry_run() -> bool:
    return os.environ.get("BV_DRY_RUN", "").strip().lower() in {"1", "true", "yes", "on"}


def _evaluation_job_name() -> str:
    return os.environ.get("BV_EVALUATION_JOB_NAME", _DEFAULT_JOB_NAME).strip() or _DEFAULT_JOB_NAME


def _sync_datasets(args: argparse.Namespace) -> int:
    scripts_dir = _repo_root() / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from sync_mlflow_eval_datasets import main as sync_main  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        outcome = EvaluationCommandOutcome(
            action="sync-datasets",
            payload={"status": "import_failed", "error": str(exc)},
            suggested_next_action="run from a BrickVision source checkout with scripts/ available",
            exit_code=2,
        )
        print(outcome.to_json())
        return outcome.exit_code

    argv = ["--manifest", str(args.manifest)]
    if args.dry_run:
        argv.append("--dry-run")
    return int(sync_main(argv))


def _run(args: argparse.Namespace) -> int:
    mode = "existing_data" if bool(getattr(args, "existing_data", False)) else "default"
    if _is_dry_run():
        outcome = EvaluationCommandOutcome(
            action="run",
            payload={
                "evaluation_state": "triggered_dry_run",
                "job_name": _evaluation_job_name(),
                "mode": mode,
                "upstream_refresh_triggered": False,
                "run_id": None,
            },
            suggested_next_action="unset BV_DRY_RUN to trigger the real Databricks evaluation Job",
            exit_code=0,
        )
        print(outcome.to_json())
        return outcome.exit_code

    exit_code, run_id, error_message = _trigger_job_run(job_name=_evaluation_job_name())
    if exit_code != 0:
        outcome = EvaluationCommandOutcome(
            action="run",
            payload={
                "evaluation_state": "trigger_failed",
                "job_name": _evaluation_job_name(),
                "mode": mode,
                "upstream_refresh_triggered": False,
                "error": error_message,
            },
            suggested_next_action=(
                "deploy local jobs via scripts/local_deploy/deploy_indexer_job.py, "
                "then retry `brickvision evaluation run`"
            ),
            exit_code=exit_code,
        )
        print(outcome.to_json())
        return outcome.exit_code

    outcome = EvaluationCommandOutcome(
        action="run",
        payload={
            "evaluation_state": "triggered",
            "job_name": _evaluation_job_name(),
            "mode": mode,
            "upstream_refresh_triggered": False,
            "run_id": run_id,
        },
        suggested_next_action="open the Evaluation page or Databricks Job run to inspect scorer results",
        exit_code=0,
    )
    print(outcome.to_json())
    return outcome.exit_code


def _status(_args: argparse.Namespace) -> int:
    payload, exit_code = _latest_scorer_status()
    outcome = EvaluationCommandOutcome(
        action="status",
        payload=payload,
        suggested_next_action=(
            "run `brickvision evaluation sync-datasets`, then `brickvision evaluation run`"
            if payload.get("evaluation_state") == "never_run"
            else "open the Evaluation page for workflow-level scorer details"
        ),
        exit_code=exit_code,
    )
    print(outcome.to_json())
    return outcome.exit_code


def _trigger_job_run(*, job_name: str) -> tuple[int, int | None, str | None]:
    try:
        from databricks.sdk import WorkspaceClient  # noqa: PLC0415
    except ModuleNotFoundError as exc:
        if exc.name != "databricks":
            return 2, None, f"databricks-sdk import failed: {exc}"
        try:
            return _trigger_job_run_rest(job_name=job_name)
        except Exception as rest_exc:  # noqa: BLE001
            return 2, None, f"Jobs REST fallback failed: {rest_exc}"
    except Exception as exc:  # noqa: BLE001
        return 2, None, f"databricks-sdk import failed: {exc}"
    try:
        client = WorkspaceClient()
    except Exception as exc:  # noqa: BLE001
        return 2, None, f"WorkspaceClient construction failed: {exc}"

    matching_id: int | None = None
    try:
        for job in client.jobs.list(name=job_name):
            settings = getattr(job, "settings", None)
            settings_name = getattr(settings, "name", None) if settings else None
            if settings_name == job_name or job_name in (
                getattr(job, "name", None) or settings_name or ""
            ):
                raw_job_id = getattr(job, "job_id", None)
                matching_id = int(raw_job_id) if raw_job_id is not None else None
                if matching_id:
                    break
    except Exception as exc:  # noqa: BLE001
        return 2, None, f"jobs.list failed: {exc}"

    if matching_id is None:
        return 2, None, f"Job named {job_name!r} not found in this workspace."

    try:
        run = client.jobs.run_now(job_id=matching_id)
    except Exception as exc:  # noqa: BLE001
        return 2, None, f"jobs.run_now failed: {exc}"

    run_id = getattr(run, "run_id", None)
    if run_id is None and hasattr(run, "result"):
        run_id = getattr(run.result, "run_id", None)
    return 0, int(run_id) if run_id is not None else None, None


def _trigger_job_run_rest(*, job_name: str) -> tuple[int, int | None, str | None]:
    host = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/")
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if not host or not token:
        return 2, None, "DATABRICKS_HOST and DATABRICKS_TOKEN are required for REST fallback"

    matching_id = _find_job_id_rest(host=host, token=token, job_name=job_name)
    if matching_id is None:
        return 2, None, f"Job named {job_name!r} not found in this workspace."
    response = _databricks_rest_request(
        f"{host}/api/2.2/jobs/run-now",
        method="POST",
        token=token,
        payload={"job_id": matching_id},
    )
    run_id = response.get("run_id")
    return 0, int(run_id) if run_id is not None else None, None


def _find_job_id_rest(*, host: str, token: str, job_name: str) -> int | None:
    page_token = ""
    while True:
        url = f"{host}/api/2.2/jobs/list?limit=100&expand_tasks=true"
        if page_token:
            url = f"{url}&page_token={page_token}"
        response = _databricks_rest_request(url, method="GET", token=token)
        for job in response.get("jobs") or []:
            if not isinstance(job, dict):
                continue
            settings = job.get("settings") if isinstance(job.get("settings"), dict) else {}
            if settings.get("name") == job_name or job.get("name") == job_name:
                job_id = job.get("job_id")
                return int(job_id) if job_id is not None else None
        page_token = str(response.get("next_page_token") or "")
        if not page_token:
            return None


def _latest_scorer_status() -> tuple[dict[str, Any], int]:
    warehouse_id = (
        os.environ.get("BV_EVALUATION_WAREHOUSE_ID", "").strip()
        or os.environ.get("DATABRICKS_WAREHOUSE_ID", "").strip()
        or os.environ.get("BV_INDEXER_WAREHOUSE_ID", "").strip()
        or os.environ.get("BV_WAREHOUSE_ID", "").strip()
    )
    if not warehouse_id:
        return {
            "evaluation_state": "unavailable",
            "error": "BV_EVALUATION_WAREHOUSE_ID or DATABRICKS_WAREHOUSE_ID is required",
        }, 2

    try:
        from databricks.sdk import WorkspaceClient  # noqa: PLC0415
        from databricks.sdk.service.sql import StatementState  # noqa: PLC0415

        client = WorkspaceClient()
        response = client.statement_execution.execute_statement(
            statement=_latest_scorer_sql(),
            warehouse_id=warehouse_id,
            wait_timeout="50s",
            row_limit=100,
        )
        state = response.status.state if response.status else None
        if state != StatementState.SUCCEEDED:
            err = response.status.error if response.status else None
            message = err.message if err else "(no error message)"
            return {"evaluation_state": "query_failed", "error": message}, 2
        rows = response.result.data_array if response.result else []
    except ModuleNotFoundError as exc:
        if exc.name != "databricks":
            return {"evaluation_state": "query_failed", "error": str(exc)}, 2
        try:
            rows = _latest_scorer_rows_rest(warehouse_id=warehouse_id)
        except Exception as rest_exc:  # noqa: BLE001
            return {"evaluation_state": "query_failed", "error": str(rest_exc)}, 2
    except Exception as exc:  # noqa: BLE001
        return {"evaluation_state": "query_failed", "error": str(exc)}, 2

    if not rows:
        return {"evaluation_state": "never_run", "latest_scorer_runs": []}, 0

    latest = [
        {
            "workflow": str(row[0]),
            "status": str(row[1]),
            "mlflow_dataset_name": str(row[2] or ""),
            "created_at_ms": int(row[3] or 0),
        }
        for row in rows
    ]
    failed = [row for row in latest if row["status"] == "failed"]
    return {
        "evaluation_state": "ready",
        "latest_scorer_runs": latest,
        "workflow_count": len(latest),
        "failing_workflow_count": len(failed),
    }, 1 if failed else 0


def _latest_scorer_rows_rest(*, warehouse_id: str) -> list[list[Any]]:
    host = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/")
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if not host or not token:
        raise RuntimeError("DATABRICKS_HOST and DATABRICKS_TOKEN are required for REST fallback")

    payload = {
        "statement": _latest_scorer_sql(),
        "warehouse_id": warehouse_id,
        "wait_timeout": "30s",
        "disposition": "INLINE",
        "row_limit": 100,
    }
    response = _databricks_rest_request(
        f"{host}/api/2.0/sql/statements/",
        method="POST",
        token=token,
        payload=payload,
    )
    statement_id = str(response.get("statement_id") or "")
    state = str((response.get("status") or {}).get("state") or "")
    for _ in range(20):
        if state not in {"PENDING", "RUNNING"}:
            break
        time.sleep(2)
        response = _databricks_rest_request(
            f"{host}/api/2.0/sql/statements/{statement_id}",
            method="GET",
            token=token,
        )
        state = str((response.get("status") or {}).get("state") or "")
    if state != "SUCCEEDED":
        error = (response.get("status") or {}).get("error") or {}
        raise RuntimeError(str(error.get("message") or error or f"statement ended in {state}"))
    return list((response.get("result") or {}).get("data_array") or [])


def _databricks_rest_request(
    url: str,
    *,
    method: str,
    token: str,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body = json.dumps(dict(payload)).encode("utf-8") if payload is not None else None
    req = request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with request.urlopen(req, timeout=45) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _latest_scorer_sql() -> str:
    catalog = _quote_identifier(os.environ.get("BV_CATALOG", "brickvision").strip() or "brickvision")
    schema = _quote_identifier(os.environ.get("BV_SCHEMA", "brickvision").strip() or "brickvision")
    return f"""
    SELECT workflow, status, mlflow_dataset_name, created_at_ms
    FROM (
      SELECT
        workflow,
        status,
        mlflow_dataset_name,
        created_at_ms,
        ROW_NUMBER() OVER (PARTITION BY workflow ORDER BY created_at_ms DESC) AS rn
      FROM {catalog}.{schema}.evaluation_events
      WHERE event_kind = 'scorer_run'
    )
    WHERE rn = 1
    ORDER BY workflow
    """


def _quote_identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def add_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="evaluation_command", required=True)

    sync = sub.add_parser("sync-datasets", help="sync manifest-backed MLflow evaluation datasets")
    sync.add_argument("--manifest", default="config/evaluation/evalsets.json")
    sync.add_argument("--dry-run", action="store_true")
    sync.set_defaults(_handler=_sync_datasets)

    run = sub.add_parser("run", help="trigger the scheduled evaluation scorer Job now")
    run.add_argument(
        "--existing-data",
        action="store_true",
        help=(
            "score currently registered eval datasets and existing evaluation_events only; "
            "does not trigger indexer or Workspace KG refresh"
        ),
    )
    run.set_defaults(_handler=_run)

    status = sub.add_parser("status", help="print latest scorer-run status")
    status.set_defaults(_handler=_status)


__all__ = ["EvaluationCommandOutcome", "add_parser"]
