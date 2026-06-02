import importlib.util
from pathlib import Path
from typing import Any


def _runner():
    skill_path = Path(__file__).resolve().parents[2] / "skills" / "ml.training-task-plan" / "skill.py"
    spec = importlib.util.spec_from_file_location("_ml_training_task_plan_skill_for_test", skill_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_ml_training_task_plan


def _strategy() -> dict[str, Any]:
    return {
        "status": "ready_for_approval",
        "problem_type": "regression",
        "metric_candidates": ["rmse", "mae"],
        "uc_model_name": "main.ml.customer_spend",
    }


def _inputs() -> dict[str, Any]:
    return {
        "strategy_plan": _strategy(),
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


def _backend_selection() -> dict[str, Any]:
    return {
        "status": "ready",
        "selected_backend": {
            "backend_id": "spark_ml",
            "status": "supported",
            "capability_refs": ["docs:databricks-spark-mllib"],
        },
    }


def test_training_task_plan_blocks_without_real_artifact_uri() -> None:
    result = _runner()(**_inputs())

    assert result["status"] == "blocked"
    assert result["job_submit_body"] is None
    assert "TRAINING_ARTIFACT_URI_REQUIRED" in {finding["code"] for finding in result["findings"]}


def test_training_task_plan_blocks_without_selected_backend() -> None:
    inputs = _inputs()
    inputs.pop("backend_selection")
    result = _runner()(**inputs, training_artifact_uri="/Workspace/BrickVision/ml/train.py")

    assert result["status"] == "blocked"
    assert "TRAINING_BACKEND_NOT_SELECTED" in {finding["code"] for finding in result["findings"]}


def test_training_task_plan_emits_jobs_body_for_existing_artifact_ref() -> None:
    result = _runner()(
        **_inputs(),
        training_artifact_uri="/Workspace/BrickVision/ml/train_evaluate_register.py",
        audit_id="audit-1",
    )

    body = result["job_submit_body"]
    assert result["status"] == "ready"
    assert body["tasks"][0]["spark_python_task"]["python_file"] == "/Workspace/BrickVision/ml/train_evaluate_register.py"
    parameters = body["tasks"][0]["spark_python_task"]["parameters"]
    assert "--rows-uri" in parameters
    assert "--selected-backend-json" in parameters
    assert "--strategy-plan-json" in parameters
    assert "--audit-table" in parameters
    assert "--audit-id" in parameters


def test_training_task_plan_uses_declared_artifact_dependencies() -> None:
    result = _runner()(
        **_inputs(),
        training_artifact_uri="/Workspace/BrickVision/ml/train_evaluate_register.py",
        environment_dependencies=["mlflow", "scikit-learn", "pandas"],
        audit_id="audit-1",
    )

    dependencies = result["job_submit_body"]["environments"][0]["spec"]["dependencies"]
    assert "scikit-learn" in dependencies
    assert "pandas" in dependencies
