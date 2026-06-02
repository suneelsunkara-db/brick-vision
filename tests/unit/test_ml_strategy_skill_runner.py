import importlib.util
from pathlib import Path
from typing import Any

from tests.unit.test_ml_readiness import _customer_spend_profile


def _runner():
    skill_path = Path(__file__).resolve().parents[2] / "skills" / "ml.strategy-plan" / "skill.py"
    spec = importlib.util.spec_from_file_location("_ml_strategy_skill_for_test", skill_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_ml_strategy_plan


def _problem() -> dict[str, Any]:
    return {
        "status": "ready_for_strategy",
        "recommended_problem_type": "regression",
        "target_column": "total_spend",
        "findings": [],
    }


def _readiness() -> dict[str, Any]:
    return {
        "status": "feature_ready",
        "problem_type": "regression",
        "target_column": "total_spend",
        "entity_key": "customer_id",
        "findings": [],
    }


def test_strategy_skill_builds_api_plan_from_capability_evidence() -> None:
    plan = _runner()(
        problem_selection=_problem(),
        feature_readiness=_readiness(),
        dataset_profiles=_customer_spend_profile(),
        model_full_name="main.ml.customer_spend_model",
        capability_evidence=[
            {"entity_id": "openapi:2.1:JobsRunsSubmit"},
            {"entity_id": "docs:mlflow-unity-catalog-models"},
        ],
        api_operations=[
            {
                "operation_id": "openapi:2.1:JobsRunsSubmit",
                "method": "POST",
                "path": "/api/2.1/jobs/runs/submit",
                "body": {"run_name": "brickvision-ml-training"},
                "capability_refs": ["openapi:2.1:JobsRunsSubmit"],
            },
            {
                "operation_id": "openapi:2.0:StatementExecutionGet",
                "method": "GET",
                "path": "/api/2.0/sql/statements/audit-readback",
                "capability_refs": ["openapi:2.0:StatementExecutionGet"],
            },
        ],
    )

    assert plan["api_execution_plan"]["operations"][0]["operation_id"] == "openapi:2.1:JobsRunsSubmit"
