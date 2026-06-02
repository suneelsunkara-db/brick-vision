"""Thin Databricks API-plan executor for ML training skills.

This module intentionally does not choose a model, embed a training driver, or
implement ML algorithms. The skill layer owns that behavior through its
approved strategy plan, which must be grounded in indexed Databricks SDK,
OpenAPI, and docs capability evidence.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from brickvision_runtime.databricks_api_executor import (
    DatabricksApiExecutionError,
    DatabricksApiOperation,
    execute_operation,
    operation_from_dict,
    statement_row,
    workspace_client,
)
from brickvision_runtime.ml import ModelTrainingRun, TrainingSpec


@dataclasses.dataclass(frozen=True)
class DatabricksApiPlan:
    plan_id: str
    capability_refs: tuple[str, ...]
    operations: tuple[DatabricksApiOperation, ...]
    audit_readback: dict[str, Any]


class DatabricksApiPlanBlocked(RuntimeError):
    """Raised when a skill has not produced a grounded Databricks API plan."""


def run_databricks_training_job(
    *,
    spec: TrainingSpec,
    rows_uri: str,
    skill_id: str,
    audit_id: str,
    api_execution_plan: dict[str, Any] | None = None,
    client: Any | None = None,
) -> ModelTrainingRun:
    """Execute an approved, capability-grounded Databricks API plan."""

    _ = rows_uri
    plan = _parse_api_plan(api_execution_plan)
    if client is None:
        client = workspace_client()
    for operation in plan.operations:
        execute_operation(client=client, operation=operation)
    return _read_audit_result(
        client=client,
        plan=plan,
        audit_id=audit_id,
        skill_id=skill_id,
        model_id=spec.model_id,
    )


def _parse_api_plan(raw_plan: dict[str, Any] | None) -> DatabricksApiPlan:
    if not isinstance(raw_plan, dict) or not raw_plan:
        raise DatabricksApiPlanBlocked(
            "ML training requires an api_execution_plan grounded in indexed Databricks "
            "SDK/OpenAPI/docs capability evidence."
        )
    capability_refs = _string_tuple(raw_plan.get("capability_refs"))
    if not capability_refs:
        raise DatabricksApiPlanBlocked(
            "api_execution_plan must include capability_refs from the capability graph."
        )
    operations = tuple(_operation(item) for item in raw_plan.get("operations", []))
    if not operations:
        raise DatabricksApiPlanBlocked("api_execution_plan must include at least one API operation.")
    return DatabricksApiPlan(
        plan_id=str(raw_plan.get("plan_id") or ""),
        capability_refs=capability_refs,
        operations=operations,
        audit_readback=dict(raw_plan.get("audit_readback") or {}),
    )


def _operation(raw: object) -> DatabricksApiOperation:
    try:
        return operation_from_dict(raw)
    except DatabricksApiExecutionError as exc:
        raise DatabricksApiPlanBlocked(str(exc)) from exc


def _read_audit_result(
    *,
    client: Any,
    plan: DatabricksApiPlan,
    audit_id: str,
    skill_id: str,
    model_id: str,
) -> ModelTrainingRun:
    readback = plan.audit_readback
    if not readback:
        raise DatabricksApiPlanBlocked("api_execution_plan must include audit_readback instructions.")
    operation = _operation(readback.get("operation"))
    response = execute_operation(client=client, operation=operation)
    return _model_training_run_from_response(
        response=response,
        audit_id=audit_id,
        skill_id=skill_id,
        model_id=model_id,
    )


def _model_training_run_from_response(
    *,
    response: object,
    audit_id: str,
    skill_id: str,
    model_id: str,
) -> ModelTrainingRun:
    row = response
    if isinstance(response, dict):
        row = (
            response.get("model_training_run")
            or response.get("row")
            or statement_row(response)
        )
    if not isinstance(row, dict):
        raise RuntimeError(f"Audit readback did not return a ModelTrainingRun object: {response}")
    return ModelTrainingRun(
        audit_id=str(row.get("audit_id") or audit_id),
        model_id=str(row.get("model_id") or model_id),
        skill_id=str(row.get("skill_id") or skill_id),
        val_metric_name=str(row.get("val_metric_name") or ""),
        val_metric_value=float(row.get("val_metric_value") or 0.0),
        val_metric_floor=float(row.get("val_metric_floor") or 0.0),
        val_floor_passed=bool(row.get("val_floor_passed")),
        train_row_count=int(row.get("train_row_count") or 0),
        validation_row_count=int(row.get("validation_row_count") or 0),
        registered_model_name=(
            str(row.get("registered_model_name")) if row.get("registered_model_name") else None
        ),
        registered_model_version=(
            int(row.get("registered_model_version")) if row.get("registered_model_version") else None
        ),
        feature_set_hash=str(row.get("feature_set_hash") or ""),
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


__all__ = [
    "DatabricksApiOperation",
    "DatabricksApiPlan",
    "DatabricksApiPlanBlocked",
    "run_databricks_training_job",
]
