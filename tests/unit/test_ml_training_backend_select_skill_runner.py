import importlib.util
from pathlib import Path
from typing import Any


def _runner():
    skill_path = Path(__file__).resolve().parents[2] / "skills" / "ml.training-backend-select" / "skill.py"
    spec = importlib.util.spec_from_file_location("_ml_training_backend_select_skill_for_test", skill_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_ml_training_backend_select


def _strategy() -> dict[str, Any]:
    return {
        "status": "ready_for_approval",
        "problem_type": "regression",
        "metric_candidates": ["rmse", "mae"],
        "uc_model_name": "main.ml.customer_spend",
    }


def _feature() -> dict[str, Any]:
    return {"status": "feature_ready", "problem_type": "regression", "target_column": "total_spend"}


def _profiles() -> list[dict[str, Any]]:
    return [{"table_ref": "main.ml.rows", "row_count": 240, "columns": [{"name": "total_spend"}]}]


def _model_family() -> dict[str, Any]:
    return {
        "status": "ready",
        "selected_model_family": {
            "family_id": "tabular_regression",
            "problem_type": "regression",
            "required_substrate": [
                "databricks_jobs",
                "mlflow_tracking",
                "unity_catalog_model_registry",
            ],
        },
    }


def test_backend_select_rejects_unavailable_automl_on_serverless_jobs() -> None:
    result = _runner()(
        strategy_plan=_strategy(),
        feature_readiness=_feature(),
        dataset_profiles=_profiles(),
        runtime_surface="serverless_jobs",
        capability_evidence=[{"entity_id": "docs:databricks-automl"}],
        runtime_evidence={"databricks_automl_available": False},
    )

    assert result["status"] == "blocked"
    assert result["selected_backend"] is None
    assert any(
        item["backend_id"] == "databricks_automl"
        and "not available" in " ".join(item["reasons"])
        for item in result["rejected_backends"]
    )


def test_backend_select_selects_spark_ml_only_when_runtime_support_is_proven() -> None:
    result = _runner()(
        strategy_plan=_strategy(),
        feature_readiness=_feature(),
        dataset_profiles=_profiles(),
        runtime_surface="serverless_jobs",
        capability_evidence=[{"entity_id": "docs:databricks-spark-mllib"}],
        runtime_evidence={"spark_ml_allowed": True},
    )

    assert result["status"] == "ready"
    assert result["selected_backend"]["backend_id"] == "spark_ml"


def test_backend_select_maps_model_family_to_mlflow_artifact_backend() -> None:
    result = _runner()(
        strategy_plan=_strategy(),
        feature_readiness=_feature(),
        dataset_profiles=_profiles(),
        runtime_surface="serverless_jobs",
        capability_evidence=[
            {"entity_id": "docs:databricks-mlflow"},
            {"entity_id": "openapi:2.1:JobsRunsSubmit"},
        ],
        model_family=_model_family(),
    )

    assert result["status"] == "ready"
    assert result["selected_backend"]["backend_id"] == "databricks_mlflow_flavor_job"
    assert result["selected_backend"]["model_family"]["family_id"] == "tabular_regression"


def test_backend_select_rejects_mlflow_artifact_without_jobs_evidence() -> None:
    result = _runner()(
        strategy_plan=_strategy(),
        feature_readiness=_feature(),
        dataset_profiles=_profiles(),
        runtime_surface="serverless_jobs",
        capability_evidence=[{"entity_id": "docs:databricks-mlflow"}],
        model_family=_model_family(),
    )

    assert result["status"] == "blocked"
    assert any(
        item["backend_id"] == "databricks_mlflow_flavor_job"
        and "missing Databricks Jobs capability evidence" in " ".join(item["reasons"])
        for item in result["rejected_backends"]
    )
