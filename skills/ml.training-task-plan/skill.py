"""Mechanical Layer-0 skill: ``skill:ml.training-task-plan``."""

from __future__ import annotations

import json
from typing import Any

from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG


SKILL = Skill.mechanical(
    id="skill:ml.training-task-plan",
    version="0.1.0",
    dag=DAG(name="ml.training-task-plan"),
    constitutional=(
        "training.must.not.run.before.strategy-approval",
        "training.driver.must.be-real-artifact-ref",
        "runtime.adapters.must.not.embed-training-code",
    ),
)


def run_ml_training_task_plan(
    *,
    strategy_plan: dict[str, Any],
    backend_selection: dict[str, Any] | None = None,
    training_driver_uri: str | None = None,
    training_artifact_uri: str | None = None,
    task_parameters: list[str] | None = None,
    environment_dependencies: list[str] | None = None,
    rows_uri: str | None = None,
    model_full_name: str | None = None,
    feature_columns: list[str] | None = None,
    label_column: str | None = None,
    primary_key: str | None = None,
    val_metric_name: str | None = None,
    val_metric_floor: float | int | None = None,
    split_seed: int | None = None,
    strategy_approval_id: str | None = None,
    audit_table: str | None = None,
    audit_id: str | None = None,
    job_run_name: str | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    if strategy_plan.get("status") != "ready_for_approval":
        findings.append(_finding("blocking", "STRATEGY_NOT_READY", "Strategy must be ready_for_approval."))
    selected_backend = _selected_backend(backend_selection)
    if selected_backend is None:
        findings.append(
            _finding(
                "blocking",
                "TRAINING_BACKEND_NOT_SELECTED",
                "Run skill:ml.training-backend-select and bind a supported backend before planning a task.",
            )
        )
    artifact_uri = str(training_artifact_uri or training_driver_uri or "").strip()
    if not _valid_artifact_uri(artifact_uri):
        findings.append(
            _finding(
                "blocking",
                "TRAINING_ARTIFACT_URI_REQUIRED",
                "Bind an existing Databricks notebook/script artifact URI generated or approved by the ML skill.",
            )
        )
    parameters = list(task_parameters or [])
    if not parameters:
        parameters = _default_task_parameters(
            rows_uri=rows_uri,
            model_full_name=model_full_name,
            feature_columns=feature_columns,
            label_column=label_column,
            primary_key=primary_key,
            val_metric_name=val_metric_name,
            val_metric_floor=val_metric_floor,
            split_seed=split_seed,
            strategy_approval_id=strategy_approval_id,
            audit_table=audit_table,
            audit_id=audit_id,
            strategy_plan=strategy_plan,
            selected_backend=selected_backend,
        )
    missing = [name for name, value in {"task_parameters": parameters, "audit_table": audit_table}.items() if not _has_value(value)]
    if missing:
        findings.append(
            _finding(
                "blocking",
                "TRAINING_INPUTS_REQUIRED",
                f"Missing training artifact inputs: {', '.join(sorted(missing))}.",
            )
        )

    metric_candidates = {str(metric) for metric in strategy_plan.get("metric_candidates", [])}
    if metric_candidates and val_metric_name and str(val_metric_name) not in metric_candidates:
        findings.append(
            _finding(
                "blocking",
                "METRIC_NOT_IN_APPROVED_STRATEGY",
                f"{val_metric_name} is not in the approved strategy metric candidates.",
            )
        )

    job_submit_body = None
    if not _blocking(findings):
        job_submit_body = {
            "run_name": job_run_name or f"brickvision-ml-training-{_model_slug(str(model_full_name))}",
            "environments": [
                {
                    "environment_key": "default",
                    "spec": {
                        "client": "2",
                        "dependencies": list(environment_dependencies or []),
                    },
                }
            ],
            "tasks": [
                {
                    "task_key": "train_evaluate_register",
                    "environment_key": "default",
                    "spark_python_task": {
                        "python_file": artifact_uri,
                        **({"source": "WORKSPACE"} if artifact_uri.startswith("/Workspace/") else {}),
                        "parameters": parameters,
                    },
                }
            ],
        }

    return {
        "status": "ready" if job_submit_body else "blocked",
        "job_submit_body": job_submit_body,
        "findings": findings,
        "next_action": (
            "Pass job_submit_body to skill:lakeflow.jobs-run-submit."
            if job_submit_body
            else "Bind a real Databricks training artifact and its declared task parameters."
        ),
    }


def _valid_artifact_uri(value: str) -> bool:
    return value.startswith(("dbfs:/", "/Workspace/", "/Volumes/"))


def _selected_backend(backend_selection: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(backend_selection, dict) or backend_selection.get("status") != "ready":
        return None
    selected = backend_selection.get("selected_backend")
    if not isinstance(selected, dict) or selected.get("status") != "supported":
        return None
    if not selected.get("backend_id") or not selected.get("capability_refs"):
        return None
    return dict(selected)


def _default_task_parameters(
    *,
    rows_uri: str | None,
    model_full_name: str | None,
    feature_columns: list[str] | None,
    label_column: str | None,
    primary_key: str | None,
    val_metric_name: str | None,
    val_metric_floor: float | int | None,
    split_seed: int | None,
    strategy_approval_id: str | None,
    audit_table: str | None,
    audit_id: str | None,
    strategy_plan: dict[str, Any],
    selected_backend: dict[str, Any] | None,
) -> list[str]:
    required = {
        "rows_uri": rows_uri,
        "model_full_name": model_full_name,
        "feature_columns": feature_columns,
        "label_column": label_column,
        "primary_key": primary_key,
        "val_metric_name": val_metric_name,
        "val_metric_floor": val_metric_floor,
        "split_seed": split_seed,
        "strategy_approval_id": strategy_approval_id,
        "audit_table": audit_table,
    }
    if any(not _has_value(value) for value in required.values()):
        return []
    parameters = [
        "--rows-uri",
        str(rows_uri),
        "--model-full-name",
        str(model_full_name),
        "--feature-columns-json",
        json.dumps(list(feature_columns or []), separators=(",", ":")),
        "--label-column",
        str(label_column),
        "--primary-key",
        str(primary_key),
        "--val-metric-name",
        str(val_metric_name),
        "--val-metric-floor",
        str(float(val_metric_floor or 0.0)),
        "--split-seed",
        str(int(split_seed or 0)),
        "--strategy-approval-id",
        str(strategy_approval_id),
        "--selected-backend-json",
        json.dumps(selected_backend or {}, separators=(",", ":"), sort_keys=True),
        "--strategy-plan-json",
        json.dumps(strategy_plan, separators=(",", ":"), sort_keys=True),
        "--audit-table",
        str(audit_table),
    ]
    if audit_id:
        parameters.extend(["--audit-id", str(audit_id)])
    return parameters


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True


def _model_slug(value: str) -> str:
    return value.replace("`", "").replace(".", "-").replace("_", "-")[-64:] or "model"


def _finding(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _blocking(findings: list[dict[str, str]]) -> bool:
    return any(item.get("severity") == "blocking" for item in findings)


__all__ = ["SKILL", "run_ml_training_task_plan"]
