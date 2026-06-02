import importlib.util
from pathlib import Path
from typing import Any


def _runner():
    skill_path = Path(__file__).resolve().parents[2] / "skills" / "ml.training-artifact-plan" / "skill.py"
    spec = importlib.util.spec_from_file_location("_ml_training_artifact_plan_skill_for_test", skill_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_ml_training_artifact_plan


def _strategy() -> dict[str, Any]:
    return {
        "status": "ready_for_approval",
        "problem_type": "regression",
        "metric_candidates": ["rmse", "mae"],
        "uc_model_name": "main.ml.customer_spend",
    }


def _family() -> dict[str, Any]:
    return {
        "status": "ready",
        "selected_model_family": {"family_id": "tabular_regression", "problem_type": "regression"},
    }


def _backend_selection() -> dict[str, Any]:
    return {
        "status": "ready",
        "selected_backend": {
            "backend_id": "databricks_mlflow_flavor_job",
            "status": "supported",
            "capability_refs": ["docs:databricks-mlflow", "openapi:2.1:JobsRunsSubmit"],
        },
    }


def _inputs() -> dict[str, Any]:
    return {
        "strategy_plan": _strategy(),
        "model_family": _family(),
        "backend_selection": _backend_selection(),
        "rows_uri": "main.ml.training_rows",
        "model_full_name": "main.ml.customer_spend",
        "feature_columns": ["txn_count"],
        "label_column": "total_spend",
        "primary_key": "customer_id",
        "val_metric_name": "rmse",
        "val_metric_floor": 10.0,
        "split_seed": 7,
        "strategy_approval_id": "approval-1",
        "audit_table": "main.ml.model_training_runs",
    }


def test_artifact_plan_blocks_until_artifact_uri_is_bound() -> None:
    result = _runner()(**_inputs())

    assert result["status"] == "blocked"
    assert result["artifact_template_id"] == "databricks.mlflow-flavor.tabular"
    assert "ARTIFACT_URI_REQUIRED" in {finding["code"] for finding in result["findings"]}
    assert "mlflow.sklearn.log_model" in result["template_source"]


def test_artifact_plan_emits_task_contract_for_bound_artifact() -> None:
    result = _runner()(
        **_inputs(),
        training_artifact_uri="/Workspace/BrickVision/ml/customer_spend_train.py",
        audit_id="audit-1",
    )

    assert result["status"] == "ready"
    assert result["training_artifact_uri"] == "/Workspace/BrickVision/ml/customer_spend_train.py"
    assert "--rows-uri" in result["task_parameters"]
    assert "--audit-id" in result["task_parameters"]
    assert result["artifact_contract"]["backend_id"] == "databricks_mlflow_flavor_job"
