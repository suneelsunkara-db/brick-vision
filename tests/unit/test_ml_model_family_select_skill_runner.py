import importlib.util
from pathlib import Path
from typing import Any


def _runner():
    skill_path = Path(__file__).resolve().parents[2] / "skills" / "ml.model-family-select" / "skill.py"
    spec = importlib.util.spec_from_file_location("_ml_model_family_select_skill_for_test", skill_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_ml_model_family_select


def _strategy(problem_type: str = "regression") -> dict[str, Any]:
    return {
        "status": "ready_for_approval",
        "problem_type": problem_type,
        "metric_candidates": ["rmse", "mae"],
        "uc_model_name": "main.ml.customer_spend",
    }


def _feature(problem_type: str = "regression") -> dict[str, Any]:
    return {"status": "feature_ready", "problem_type": problem_type, "target_column": "total_spend"}


def _profiles() -> list[dict[str, Any]]:
    return [{"table_ref": "main.ml.rows", "row_count": 240, "columns": [{"name": "total_spend"}]}]


def test_model_family_selects_tabular_regression_without_backend() -> None:
    result = _runner()(
        strategy_plan=_strategy(),
        feature_readiness=_feature(),
        dataset_profiles=_profiles(),
    )

    assert result["status"] == "ready"
    family = result["selected_model_family"]
    assert family["family_id"] == "tabular_regression"
    assert "serverless_python_pyfunc" not in str(family)
    assert "python_imports.sklearn" in family["required_substrate"]


def test_model_family_blocks_when_strategy_is_not_ready() -> None:
    strategy = _strategy()
    strategy["status"] = "blocked"

    result = _runner()(
        strategy_plan=strategy,
        feature_readiness=_feature(),
        dataset_profiles=_profiles(),
    )

    assert result["status"] == "blocked"
    assert result["selected_model_family"] is None
