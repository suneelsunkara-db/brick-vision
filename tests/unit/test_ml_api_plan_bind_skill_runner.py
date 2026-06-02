import importlib.util
from pathlib import Path
from typing import Any


def _runner():
    skill_path = Path(__file__).resolve().parents[2] / "skills" / "ml.api-plan-bind" / "skill.py"
    spec = importlib.util.spec_from_file_location("_ml_api_plan_bind_skill_for_test", skill_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_ml_api_plan_bind


def _strategy() -> dict[str, Any]:
    return {
        "status": "ready_for_approval",
        "problem_type": "regression",
        "metric_candidates": ["rmse", "mae"],
        "uc_model_name": "main.ml.customer_spend",
    }


def _jobs_plan() -> dict[str, Any]:
    return {
        "status": "ready",
        "api_operation": {
            "operation_id": "openapi:2.1:JobsRunsSubmit",
            "method": "POST",
            "path": "/api/2.1/jobs/runs/submit",
            "body": {
                "run_name": "brickvision-ml-training",
                "tasks": [
                    {
                        "task_key": "train",
                        "spark_python_task": {
                            "python_file": "dbfs:/brickvision/ml/train.py",
                            "parameters": ["--rows-uri", "main.ml.training_rows"],
                        },
                    }
                ],
            },
            "capability_refs": ["openapi:2.1:JobsRunsSubmit"],
        },
        "capability_refs": ["openapi:2.1:JobsRunsSubmit"],
        "findings": [],
    }


def test_api_plan_bind_blocks_without_jobs_submit_plan() -> None:
    result = _runner()(
        strategy_plan=_strategy(),
        capability_evidence=[{"entity_id": "openapi:2.1:JobsRunsSubmit"}],
        audit_readback_operation={
            "operation_id": "openapi:2.0:StatementExecutionGet",
            "method": "GET",
            "path": "/api/2.0/sql/statements/audit",
            "capability_refs": ["openapi:2.0:StatementExecutionGet"],
        },
    )

    assert result["status"] == "blocked"
    assert result["api_execution_plan"] is None
    assert "JOBS_SUBMIT_PLAN_REQUIRED" in {finding["code"] for finding in result["findings"]}


def test_api_plan_bind_attaches_concrete_api_execution_plan() -> None:
    result = _runner()(
        strategy_plan=_strategy(),
        capability_evidence=[
            {"entity_id": "openapi:2.1:JobsRunsSubmit"},
            {"entity_id": "openapi:2.0:StatementExecutionGet"},
        ],
        job_submit_plan=_jobs_plan(),
        audit_readback_operation={
            "operation_id": "openapi:2.0:StatementExecutionGet",
            "method": "GET",
            "path": "/api/2.0/sql/statements/audit",
            "capability_refs": ["openapi:2.0:StatementExecutionGet"],
        },
    )

    assert result["status"] == "ready"
    assert result["strategy_plan"]["api_execution_plan"] == result["api_execution_plan"]
    assert result["api_execution_plan"]["operations"][0]["body"]["run_name"] == "brickvision-ml-training"
