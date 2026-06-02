from typing import Any
import importlib.util
from pathlib import Path

from brickvision_runtime.ml import ModelTrainingRun


def _runner():
    skill_path = Path(__file__).resolve().parents[2] / "skills" / "ml.train-evaluate-register" / "skill.py"
    spec = importlib.util.spec_from_file_location("_ml_train_skill_for_test", skill_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_ml_train_evaluate_register


def _strategy_plan(api_execution_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = {
        "status": "ready_for_approval",
        "registry": "Unity Catalog Registered Model",
        "uc_model_name": "main.ml.customer_spend_forecast",
        "problem_type": "regression",
        "metric_candidates": ["rmse", "mae"],
    }
    if api_execution_plan is not None:
        plan["api_execution_plan"] = api_execution_plan
    return plan


def test_train_skill_passes_api_execution_plan_to_tool_adapter() -> None:
    captured: dict[str, Any] = {}

    def submit_training_job(**kwargs: Any) -> ModelTrainingRun:
        captured.update(kwargs)
        return ModelTrainingRun(
            audit_id=kwargs["audit_id"],
            model_id=kwargs["spec"].model_id,
            skill_id=kwargs["skill_id"],
            val_metric_name="rmse",
            val_metric_value=9.0,
            val_metric_floor=10.0,
            val_floor_passed=True,
            train_row_count=80,
            validation_row_count=20,
            registered_model_name=kwargs["spec"].model_id,
            registered_model_version=1,
            feature_set_hash="feature-hash",
        )

    api_plan = {
        "plan_id": "plan-1",
        "capability_refs": ["openapi:2.1:JobsRunsSubmit"],
        "operations": [
            {
                "operation_id": "openapi:2.1:JobsRunsSubmit",
                "method": "POST",
                "path": "/api/2.1/jobs/runs/submit",
                "body": {"run_name": "brickvision-ml-training"},
                "capability_refs": ["openapi:2.1:JobsRunsSubmit"],
            }
        ],
        "audit_readback": {
            "operation": {
                "operation_id": "openapi:2.0:StatementExecutionGet",
                "method": "GET",
                "path": "/api/2.0/sql/statements/audit",
                "body": {},
                "capability_refs": ["openapi:2.0:StatementExecutionGet"],
            }
        },
    }

    result = _runner()(
        strategy_plan=_strategy_plan(api_plan),
        model_full_name="main.ml.customer_spend_forecast",
        feature_columns=["txn_count"],
        label_column="total_spend",
        primary_key="customer_id",
        rows_uri="main.ml.training_rows",
        split_seed=7,
        strategy_approval_id="approval-1",
        coordinator_call=lambda _: {
            "transforms": [],
            "val_metric_name": "rmse",
            "val_metric_floor": 10.0,
        },
        submit_training_job=submit_training_job,
        audit_id="audit-1",
    )

    assert result["model_training_run"].registered_model_name == "main.ml.customer_spend_forecast"
    assert captured["api_execution_plan"] == api_plan
