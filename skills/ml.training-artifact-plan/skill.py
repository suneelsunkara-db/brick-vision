"""Mechanical Layer-0 skill: ``skill:ml.training-artifact-plan``."""

from __future__ import annotations

import json
from typing import Any

from brickvision_runtime.harness import Skill
from brickvision_runtime.orchestration import DAG


SKILL = Skill.mechanical(
    id="skill:ml.training-artifact-plan",
    version="0.1.0",
    dag=DAG(name="ml.training-artifact-plan"),
    constitutional=(
        "training.artifact.must-be-databricks-native",
        "runtime.adapters.must-not-embed-training-code",
        "artifact.template.must-declare-audit-contract",
    ),
)


def run_ml_training_artifact_plan(
    *,
    strategy_plan: dict[str, Any],
    model_family: dict[str, Any],
    backend_selection: dict[str, Any],
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
    training_artifact_uri: str | None = None,
    artifact_template_id: str | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    selected_backend = _selected_backend(backend_selection)
    selected_family = _selected_family(model_family)
    template_id = str(artifact_template_id or "").strip() or _template_for(
        backend=selected_backend,
        family=selected_family,
    )

    if strategy_plan.get("status") != "ready_for_approval":
        findings.append(_finding("blocking", "STRATEGY_NOT_READY", "Strategy must be ready_for_approval."))
    if selected_backend is None:
        findings.append(_finding("blocking", "BACKEND_NOT_READY", "Backend selection must be ready."))
    if selected_family is None:
        findings.append(_finding("blocking", "MODEL_FAMILY_NOT_READY", "Model family selection must be ready."))
    if template_id not in _TEMPLATES:
        findings.append(
            _finding(
                "blocking",
                "TEMPLATE_NOT_SUPPORTED",
                f"No Databricks-native training artifact template is available for {template_id!r}.",
            )
        )

    required_values = {
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
    missing = [name for name, value in required_values.items() if not _has_value(value)]
    if missing:
        findings.append(
            _finding(
                "blocking",
                "ARTIFACT_PARAMETERS_REQUIRED",
                f"Missing artifact parameters: {', '.join(sorted(missing))}.",
            )
        )

    artifact_uri = str(training_artifact_uri or "").strip()
    if artifact_uri and not _valid_artifact_uri(artifact_uri):
        findings.append(
            _finding(
                "blocking",
                "ARTIFACT_URI_INVALID",
                "training_artifact_uri must be a Workspace, Volumes, or DBFS URI.",
            )
        )
    if not artifact_uri:
        findings.append(
            _finding(
                "blocking",
                "ARTIFACT_URI_REQUIRED",
                "Generate or approve this Databricks artifact and bind its URI before execution.",
            )
        )

    template = _TEMPLATES.get(template_id, {})
    task_parameters = (
        []
        if missing
        else _standard_task_parameters(
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
    )
    return {
        "status": "ready" if not _blocking(findings) else "blocked",
        "artifact_template_id": template_id,
        "training_artifact_uri": artifact_uri or None,
        "artifact_contract": {
            "backend_id": (selected_backend or {}).get("backend_id"),
            "model_family_id": (selected_family or {}).get("family_id"),
            "task_type": template.get("task_type"),
            "required_outputs": [
                "Append one ModelTrainingRun-compatible row to audit_table",
                "Register a Unity Catalog model version when the validation gate passes",
            ],
            "required_apis": list((selected_backend or {}).get("capability_refs") or []),
        },
        "task_parameters": task_parameters,
        "environment_dependencies": list(template.get("environment_dependencies") or []),
        "template_source": template.get("source"),
        "findings": findings,
        "next_action": (
            "Pass artifact URI and task parameters to skill:ml.training-task-plan."
            if not _blocking(findings)
            else "Generate or approve the Databricks-native training artifact, then bind its URI."
        ),
    }


def _selected_backend(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("status") != "ready":
        return None
    selected = value.get("selected_backend")
    return dict(selected) if isinstance(selected, dict) and selected.get("status") == "supported" else None


def _selected_family(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    selected = value.get("selected_model_family")
    return dict(selected) if isinstance(selected, dict) else None


def _template_for(*, backend: dict[str, Any] | None, family: dict[str, Any] | None) -> str:
    backend_id = str((backend or {}).get("backend_id") or "")
    family_id = str((family or {}).get("family_id") or "")
    if backend_id == "databricks_mlflow_flavor_job" and family_id in {
        "tabular_regression",
        "tabular_classification",
        "tabular_anomaly_detection",
    }:
        return "databricks.mlflow-flavor.tabular"
    if backend_id == "databricks_automl":
        return "databricks.automl.generated-notebook"
    if backend_id == "spark_ml":
        return "databricks.spark-ml.pipeline"
    return ""


def _standard_task_parameters(
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


def _valid_artifact_uri(value: str) -> bool:
    return value.startswith(("dbfs:/", "/Workspace/", "/Volumes/"))


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True


def _finding(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _blocking(findings: list[dict[str, str]]) -> bool:
    return any(item.get("severity") == "blocking" for item in findings)


_TEMPLATES: dict[str, dict[str, Any]] = {
    "databricks.mlflow-flavor.tabular": {
        "task_type": "spark_python_task",
        "environment_dependencies": ["mlflow", "scikit-learn", "pandas"],
        "source": """# Databricks-native tabular ML artifact contract
# Use MLflow built-in flavors such as mlflow.sklearn.log_model(..., signature=...)
# and mlflow.set_registry_uri("databricks-uc"). The artifact must append one
# ModelTrainingRun-compatible audit row to --audit-table keyed by --audit-id.
""",
    },
    "databricks.automl.generated-notebook": {
        "task_type": "notebook_task",
        "environment_dependencies": [],
        "source": """# Databricks AutoML artifact contract
# Use Databricks AutoML on supported compute, register the best model to Unity
# Catalog, and append the ModelTrainingRun audit row.
""",
    },
    "databricks.spark-ml.pipeline": {
        "task_type": "spark_python_task",
        "environment_dependencies": ["mlflow"],
        "source": """# Spark ML artifact contract
# Use pyspark.ml pipeline components on supported compute, log/register via
# MLflow/Unity Catalog, and append the ModelTrainingRun audit row.
""",
    },
}


__all__ = ["SKILL", "run_ml_training_artifact_plan"]
